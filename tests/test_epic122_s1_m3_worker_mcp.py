"""Epic 122 S1 M3：Workflow 分配器 Worker + MCP 评审工具回归护栏。

覆盖：
1. WorkflowConsumer.handle_message：
   - story.created → 触发 assign-reviewer（mock HTTP）；
   - 网络异常 → False（重投语义）；无在线 reviewer（422）→ True（ack，轮询兜底）；
   - story.ready / review.rejected / 未知事件 → ack 不调 HTTP；
2. run_poll_once：只处理 backlog 且未指派 reviewer 的 Story（幂等跳过已指派）；
3. run_mq_forever：MQ 未配置回退轮询模式；
4. MCP 工具 AST 注册检查（6 工具存在 + @mcp.tool）；
5. MCP 真实栈直调全链路：agent_register → heartbeat → list_agents →
   assign-reviewer → list_review_tasks → review_story → deregister。
"""
import ast
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import mq, workflow_worker  # noqa: E402
from agentboard.mq import (  # noqa: E402
    EVENT_REVIEW_REJECTED, EVENT_STORY_CREATED, EVENT_STORY_READY,
    WorkflowMessage, WorkflowTopology,
)

_MCP_SOURCE = Path(_ROOT) / "agentboard" / "mcp_server.py"

_M3_TOOLS = {
    "agent_register": ("/api/agents/register", "POST"),
    "agent_heartbeat": ("/api/agents/", "POST"),
    "agent_deregister": ("/api/agents/", "POST"),
    "list_agents": ("/api/agents", "GET"),
    "review_story": ("/api/stories/", "POST"),
    "list_review_tasks": ("/api/stories", "GET"),
}


# ===================== WorkflowConsumer（mock HTTP） =====================

def _msg(event: str, entity_id: int, ref_id: int | None = None) -> WorkflowMessage:
    return WorkflowMessage(event=event, entity_type="story",
                           entity_id=entity_id, ref_id=ref_id)


class _FakeResponse:
    def __init__(self, status_code: int, json_body=None, text: str = ""):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        self.text = text or str(json_body)

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self)


class _FakeClient:
    """记录请求，可编程返回。"""

    def __init__(self, response: _FakeResponse | None = None):
        self.calls: list[tuple[str, str]] = []
        self.response = response

    def request(self, method: str, path: str, **kw):
        self.calls.append((method, path))
        if self.response is not None:
            return self.response
        return _FakeResponse(200, {})

    def get(self, path: str, **kw):
        self.calls.append(("GET", path))
        if self.response is not None:
            return self.response
        return _FakeResponse(200, {})


def _cfg() -> workflow_worker.WorkflowConsumerConfig:
    return workflow_worker.WorkflowConsumerConfig(
        api_url="http://test", token="t", mq=mq.MQConfig())


def test_handle_story_created_calls_assign_reviewer():
    client = _FakeClient(_FakeResponse(200, {"id": 7, "status": "pending_review",
                                             "reviewer_id": 3}))
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    assert w.handle_message(_msg(EVENT_STORY_CREATED, 7)) is True
    assert ("POST", "/api/stories/7/assign-reviewer") in client.calls


def test_handle_story_created_network_error_returns_false():
    class _ErrClient:
        def request(self, method, path, **kw):
            raise ConnectionError("boom")

    w = workflow_worker.WorkflowConsumer(_cfg(), client=_ErrClient())
    assert w.handle_message(_msg(EVENT_STORY_CREATED, 9)) is False


def test_handle_story_created_no_online_reviewer_acks():
    # 422 = 无在线 reviewer：ack（True），由轮询兜底重试，不污染死信
    client = _FakeClient(_FakeResponse(422, text="no online reviewer available"))
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    assert w.handle_message(_msg(EVENT_STORY_CREATED, 10)) is True


def test_handle_story_ready_broadcasts_tasks_rejected_no_http():
    """S2 M1 起 story.ready 会回查 Story 任务广播 task.available（切片 2）；
    review.rejected / 无任务 story 不触发额外 HTTP 副作用。"""
    client = _FakeClient(_FakeResponse(200, {"items": []}))  # 无可认领任务
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    assert w.handle_message(_msg(EVENT_STORY_READY, 1)) is True
    assert ("GET", "/api/stories/1/tasks") in client.calls
    before = len(client.calls)
    assert w.handle_message(_msg(EVENT_REVIEW_REJECTED, 2, ref_id=1)) is True
    assert len(client.calls) == before  # review.rejected 不触发 HTTP


def test_handle_unknown_event_acks():
    w = workflow_worker.WorkflowConsumer(_cfg(), client=_FakeClient())
    assert w.handle_message(_msg("bogus.event", 1)) is True


def test_run_poll_once_skips_already_assigned():
    assigned = {"id": 1, "reviewer_id": 5, "status": "pending_review"}
    unassigned = {"id": 2, "reviewer_id": None, "status": "backlog"}
    client = _FakeClient()
    client.response = _FakeResponse(200, {"items": [assigned, unassigned]})
    w = workflow_worker.WorkflowConsumer(_cfg(), client=client)
    n = w.run_poll_once()
    assert n == 1
    assert ("POST", "/api/stories/2/assign-reviewer") in client.calls
    # 已指派的不触发
    assert not any(c[1] == "/api/stories/1/assign-reviewer" for c in client.calls)


def test_run_mq_forever_falls_back_to_poll_when_mq_disabled():
    stop = threading.Event()
    w = workflow_worker.WorkflowConsumer(_cfg(), client=_FakeClient())
    stop.set()  # 立即退出轮询
    stats = w.run_mq_forever(stop=stop)
    assert stats["mode"] == "poll"


# ===================== MCP 工具 AST 注册检查 =====================

def _is_mcp_tool_decorator(d: ast.AST) -> bool:
    """@mcp.tool() 装饰器识别。"""
    return (isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "tool"
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "mcp")


def test_m3_tools_registered_in_mcp_server():
    """6 个 M3 工具必须带 @mcp.tool() 且路径命中对应 REST 端点。"""
    src = _MCP_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    tool_names: set[str] = set()
    rest_calls: list[tuple[str, str]] = []  # (method, path 字面量或 f-string 首片段)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            if any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
                tool_names.add(node.name)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_http"):
            args = node.args
            if len(args) < 2 or not isinstance(args[0], ast.Constant):
                continue
            method = args[0].value
            pnode = args[1]
            if isinstance(pnode, ast.Constant) and isinstance(pnode.value, str):
                literal = pnode.value
            elif isinstance(pnode, ast.JoinedStr) and pnode.values:
                head = pnode.values[0]
                if not (isinstance(head, ast.Constant) and isinstance(head.value, str)):
                    continue
                literal = head.value
            else:
                continue
            rest_calls.append((method, literal))
    missing = set(_M3_TOOLS) - tool_names
    assert not missing, f"M3 工具未注册：{missing}"
    # 每个工具对应的 REST 路径片段必须在 _http 调用中出现
    for tool, (path_frag, method) in _M3_TOOLS.items():
        assert any(m == method and path_frag in p
                   for m, p in rest_calls), (
            f"{tool} 缺少 {method} {path_frag} 的 _http 调用")


# ===================== MCP 真实栈直调全链路 =====================

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_ready(base: str, timeout: float = 30.0) -> None:
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(base + "/api/meta", timeout=1).status_code == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"API 服务在 {base} 启动超时")


@pytest.fixture(scope="module")
def stack():
    """真实拉起 API，把 mcp_server 的 HTTP 客户端指向它。"""
    import httpx
    from agentboard import mcp_server

    port = _free_port()
    proc = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    prev_url = mcp_server.API_URL
    prev_token = os.environ.get("AGENTBOARD_MCP_TOKEN")
    try:
        _wait_ready(base)
        c = httpx.Client(base_url=base, timeout=30)
        c.post("/api/auth/register", json={"username": "m3admin", "password": "m3admin123"})
        r = c.post("/api/auth/login", json={"username": "m3admin", "password": "m3admin123"})
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        c.headers.update({"Authorization": f"Bearer {token}"})

        mcp_server.API_URL = base
        os.environ["AGENTBOARD_MCP_TOKEN"] = token

        r = c.post("/api/projects", json={"name": "M3 MCP 验证"})
        pid = r.json()["id"]
        r = c.post(f"/api/projects/{pid}/epics", json={"title": "M3 Epic"})
        eid = r.json()["id"]
        r = c.post(f"/api/epics/{eid}/stories", json={"title": "M3 Story"})
        sid = r.json()["id"]

        yield {"c": c, "base": base, "project_id": pid, "epic_id": eid, "story_id": sid}
        c.close()
    finally:
        mcp_server.API_URL = prev_url
        if prev_token is None:
            os.environ.pop("AGENTBOARD_MCP_TOKEN", None)
        else:
            os.environ["AGENTBOARD_MCP_TOKEN"] = prev_token
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def test_mcp_review_chain_end_to_end(stack):
    """agent_register → heartbeat → list_agents → assign → list_review_tasks → review → deregister。"""
    from agentboard import mcp_server

    # 1. 注册评审 Agent（绑定 MCP 身份的 testadmin 用户）
    r = mcp_server.agent_register(
        agent_id="wb-m3-mcp", name="M3MCPBot", roles='["reviewer"]',
        capabilities='["backend"]', cli_command="codex exec {prompt}",
    )
    assert isinstance(r, dict) and "error" not in r, f"注册失败：{r!r}"
    assert r["agent_id"] == "wb-m3-mcp"
    assert "reviewer" in r.get("roles", "")

    # 2. 心跳置在线
    r = mcp_server.agent_heartbeat("wb-m3-mcp")
    assert isinstance(r, dict) and "error" not in r, f"心跳失败：{r!r}"
    assert r["online"] is True

    # 3. 列表过滤
    r = mcp_server.list_agents(online=True, role="reviewer")
    assert isinstance(r, list), f"list_agents 应返回 list：{r!r}"
    assert "wb-m3-mcp" in {a["agent_id"] for a in r}

    # 4. 指派评审（REST 直调，MCP 无 assign 工具，分配器/REST 负责）
    c = stack["c"]
    sid = stack["story_id"]
    r = c.post(f"/api/stories/{sid}/assign-reviewer")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_review"
    my_uid = c.get("/api/auth/me").json()["id"]

    # 5. 我的评审任务（reviewer_id=me）
    r = mcp_server.list_review_tasks()
    assert isinstance(r, dict) and "items" in r, f"list_review_tasks 返回异常：{r!r}"
    items = r.get("items", [])
    assert any(it["id"] == sid for it in items), f"应包含 story {sid}：{items!r}"
    assert any(it["reviewer_id"] == my_uid for it in items), "指派对象应为我"

    # 6. 评审通过 → ready
    r = mcp_server.review_story(sid, "approve", "LGTM，合入开发队列")
    assert isinstance(r, dict) and "error" not in r, f"评审失败：{r!r}"
    assert r["status"] == "ready"
    # 评论落库（评审意见载体）
    comments = c.get(f"/api/stories/{sid}/comments").json()
    assert any("LGTM" in cm.get("content", "") for cm in comments)

    # 7. 注销下线
    r = mcp_server.agent_deregister("wb-m3-mcp")
    assert isinstance(r, dict) and "error" not in r, f"注销失败：{r!r}"
    assert r["online"] is False
