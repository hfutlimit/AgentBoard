"""Epic 96 P3 — Proposal 定稿转化 Story/Task（人工终审确认端点）全链路测试。

覆盖：
1. converged 提案转化 → Story + 子 Task 落库、story_id 回填、状态 story_created
2. 非 converged 状态拒绝（400）
3. converged_spec 为空拒绝（400）
4. 幂等重放：重复调用返回既有 Story，不重复创建
5. Epic 不属于提案项目拒绝（400）；Epic 不存在 404；提案不存在 404
6. 子 Task 从 `- [ ]` 清单解析生成（同 project/story、type=task、status=backlog）
7. MCP 工具注册（proposal_convert 存在）且调用走 _http（无 _api 未定义）

运行：
    PYTHONPATH=. python -m pytest tests/test_epic96_p3_proposal_convert.py -q

注意：与 P0 测试同因（audit_log_middleware 基于 BaseHTTPMiddleware 会 await
request.body()，TestClient 下争抢 receive 通道挂死）→ 用真实 uvicorn 子进程。
"""
import os
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 独立临时数据库（与其它测试隔离）
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard.domains.proposals.models import (  # noqa: E402
    PROPOSAL_TRANSITIONS, ProposalStatus,
)

init_db = None  # placeholder; database init happens inside the server process


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
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(base + "/api/meta", timeout=1).status_code == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"API 服务在 {base} 启动超时")


@pytest.fixture(scope="module")
def ctx():
    """真实拉起 API，建 admin 用户 + 项目 + 两个 epic，返回带鉴权头的上下文。"""
    port = _free_port()
    proc = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        c = httpx.Client(base_url=base, timeout=30)
        r = c.post("/api/auth/register",
                   json={"username": "p96p3admin", "password": "p96p3admin123"})
        assert r.status_code in (201, 409), r.text
        r = c.post("/api/auth/login",
                   json={"username": "p96p3admin", "password": "p96p3admin123"})
        assert r.status_code == 200, r.text
        c.headers.update({"Authorization": f"Bearer {r.json()['token']}"})

        r = c.post("/api/projects", json={"name": "Epic96 P3 项目"})
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]
        r = c.post(f"/api/projects/{pid}/epics", json={"title": "目标 Epic"})
        assert r.status_code in (200, 201), r.text
        eid = r.json()["id"]
        r = c.post(f"/api/projects/{pid}/epics", json={"title": "另一项目 Epic"})
        assert r.status_code in (200, 201), r.text
        eid_other = r.json()["id"]

        # 另一个项目（用于验证「Epic 不属于提案项目」拒绝）
        r = c.post("/api/projects", json={"name": "无关项目"})
        other_pid = r.json()["id"]
        r = c.post(f"/api/projects/{other_pid}/epics", json={"title": "无关 Epic"})
        other_eid = r.json()["id"]

        yield {
            "c": c, "project_id": pid, "epic_id": eid,
            "epic_id_other": eid_other, "other_pid": other_pid,
            "other_eid": other_eid,
        }
        c.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


CONVERGED_SPEC = (
    "## 最终需求\n"
    "实现周报自动整理功能。\n\n"
    "## 任务清单\n"
    "- [ ] 周报数据源接入\n"
    "- [x] 模板引擎选型\n"
    "- [ ] 导出 PDF\n"
    "- 非清单普通行不应生成任务\n"
    "- [ ]  去首尾空格标题  \n"
    "- [ ] 周报数据源接入\n"   # 重复标题应被去重
)


def _make_converged(ctx, title="定稿转化测试"):
    r = ctx["c"].post("/api/proposals", json={
        "project_id": ctx["project_id"], "title": title,
        "content": "希望做一个能自动整理周报的东西",
    })
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    for st in ("queued", "analyzing", "converged"):
        r = ctx["c"].put(f"/api/proposals/{pid}/status", json={"status": st})
        assert r.status_code == 200, f"{st}: {r.text}"
    r = ctx["c"].patch(f"/api/proposals/{pid}", json={"converged_spec": CONVERGED_SPEC})
    assert r.status_code == 200, r.text
    return pid


# ---------------- 1. 状态机表自洽 ----------------

def test_state_machine_converged_to_story_created():
    assert ProposalStatus.CONVERGED in PROPOSAL_TRANSITIONS
    assert ProposalStatus.STORY_CREATED in PROPOSAL_TRANSITIONS[ProposalStatus.CONVERGED]
    # story_created 为终态（P3 转化完成后不可再流转）
    assert PROPOSAL_TRANSITIONS[ProposalStatus.STORY_CREATED] == set()


# ---------------- 2. 转化主链路 ----------------

def test_convert_converged_proposal_to_story_and_tasks(ctx):
    pid = _make_converged(ctx, title="完整转化")
    r = ctx["c"].post(f"/api/proposals/{pid}/convert",
                      json={"epic_id": ctx["epic_id"]})
    assert r.status_code == 200, r.text
    payload = r.json()

    # Story：标题用提案标题，description 存 converged_spec 原文
    story = payload["story"]
    assert story["epic_id"] == ctx["epic_id"]
    assert story["title"] == "完整转化"
    assert story["description"] == CONVERGED_SPEC
    assert story["status"] == "backlog"

    # 子 Task：4 个清单项（含 [x] 已勾选项，与 generate_tasks_from_spec 语义一致；
    # 重复项去重、空白标题剔除、普通行不生成）
    tasks = payload["tasks"]
    titles = {t["title"] for t in tasks}
    assert titles == {"导出 PDF", "去首尾空格标题", "模板引擎选型",
                      "周报数据源接入"}, titles
    assert len(tasks) == 4
    for t in tasks:
        assert t["project_id"] == ctx["project_id"]
        assert t["story_id"] == story["id"]
        assert t["type"] == "dev"  # Story 265 后任务类型 task→dev
        assert t["status"] == "todo"  # Story 265 后默认 todo（backlog 下线）
        assert t["description"] == t["title"]

    # 提案：story_id 回填 + 终态
    prop = payload["proposal"]
    assert prop["id"] == pid
    assert prop["story_id"] == story["id"]
    assert prop["status"] == "story_created"

    # 服务端实查：Story 下 4 个 spec 任务 + 2 个自动默认任务（design/实现，2026-08-09）
    r = ctx["c"].get(f"/api/stories/{story['id']}/tasks")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 6


def test_convert_with_explicit_title(ctx):
    pid = _make_converged(ctx, title="原标题")
    r = ctx["c"].post(f"/api/proposals/{pid}/convert",
                      json={"epic_id": ctx["epic_id"], "title": "人工终审改标题"})
    assert r.status_code == 200, r.text
    assert r.json()["story"]["title"] == "人工终审改标题"


def test_convert_idempotent_replay(ctx):
    """幂等：重复调用返回既有 Story + 既有 tasks，不重复创建（防 at-least-once 重放）。"""
    pid = _make_converged(ctx, title="幂等重放")
    r1 = ctx["c"].post(f"/api/proposals/{pid}/convert",
                       json={"epic_id": ctx["epic_id"]})
    assert r1.status_code == 200, r1.text
    r2 = ctx["c"].post(f"/api/proposals/{pid}/convert",
                       json={"epic_id": ctx["epic_id"]})
    assert r2.status_code == 200, r2.text

    assert r2.json()["story"]["id"] == r1.json()["story"]["id"]
    # 首次返回 4 个 spec 任务；幂等分支返回 Story 下全部任务（含 2 个自动默认任务，
    # 2026-08-09 口径变化）——幂等契约由「任务总数不翻倍」保证
    assert r2.json()["proposal"]["story_id"] == r1.json()["story"]["id"]

    # 服务端实查：Story 仍只有 6 个任务（4 spec + 2 自动默认，没有因重放翻倍）
    r = ctx["c"].get(f"/api/stories/{r1.json()['story']['id']}/tasks")
    assert r.json()["total"] == 6


# ---------------- 3. 拒绝路径 ----------------

def test_convert_rejects_non_converged(ctx):
    pid = _make_converged(ctx, title="回退到 analyzing")
    # converged → analyzing 回退（人工终审驳回继续澄清），此时转化应被拒绝
    r = ctx["c"].put(f"/api/proposals/{pid}/status", json={"status": "analyzing"})
    assert r.status_code == 200, r.text
    r = ctx["c"].post(f"/api/proposals/{pid}/convert",
                      json={"epic_id": ctx["epic_id"]})
    assert r.status_code == 400, r.text
    assert "converged" in r.json()["detail"]

    # 但 story_id 已被清空？—— 不，回退后 story_id 仍为 None，状态非 converged 即拒绝
    fresh = ctx["c"].get(f"/api/proposals/{pid}").json()
    assert fresh["status"] == "analyzing"


def test_convert_rejects_empty_converged_spec(ctx):
    r = ctx["c"].post("/api/proposals", json={
        "project_id": ctx["project_id"], "title": "空规格",
    })
    pid = r.json()["id"]
    for st in ("queued", "analyzing", "converged"):
        ctx["c"].put(f"/api/proposals/{pid}/status", json={"status": st})
    # 不 PATCH converged_spec，保持为空
    r = ctx["c"].post(f"/api/proposals/{pid}/convert",
                      json={"epic_id": ctx["epic_id"]})
    assert r.status_code == 400, r.text
    assert "converged_spec" in r.json()["detail"]


def test_convert_rejects_epic_of_other_project(ctx):
    pid = _make_converged(ctx, title="跨项目 Epic")
    r = ctx["c"].post(f"/api/proposals/{pid}/convert",
                      json={"epic_id": ctx["other_eid"]})
    assert r.status_code == 400, r.text
    assert "不属于" in r.json()["detail"]


def test_convert_404s(ctx):
    assert ctx["c"].post("/api/proposals/999999/convert",
                         json={"epic_id": ctx["epic_id"]}).status_code == 404
    pid = _make_converged(ctx, title="Epic 不存在")
    assert ctx["c"].post(f"/api/proposals/{pid}/convert",
                         json={"epic_id": 999999}).status_code == 404


# ---------------- 4. MCP 工具注册与护栏 ----------------

def test_proposal_convert_mcp_tool_registered():
    """proposal_convert 工具已注册且走 _http（Epic 97 静态护栏顺带验证）。"""
    import asyncio
    import agentboard.mcp_server as mcp_server
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert "proposal_convert" in names, (
        f"proposal_convert 未注册。已注册 proposal_*："
        f"{sorted(n for n in names if n.startswith('proposal_'))}"
    )

    import ast
    mcp_path = (
        os.path.join(_ROOT, "src", "backend-fastapi", "agentboard", "mcp_server.py")
        if os.path.exists(os.path.join(_ROOT, "src", "backend-fastapi", "agentboard", "mcp_server.py"))
        else os.path.join(_ROOT, "agentboard", "mcp_server.py")
    )
    src = open(mcp_path, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "proposal_convert")
    # 函数体只调用 _http 等已定义助手，无裸 _api(
    bad = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
           and isinstance(n.func, ast.Name) and n.func.id == "_api"]
    assert not bad, "proposal_convert 内出现未定义的 _api 调用"
