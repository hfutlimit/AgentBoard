"""Ticket 全流程（2026-08-09）：Worker Story 编排 + Agent 心跳探测测试。

策略：worker 侧用 FakeClient mock HTTP（验证行为与调用序列）；
service 层效果（confirm/complete/set_story_status/blocked）由
test_story_status_machine.py 覆盖，避免双写。

覆盖：
1. build_story_context：Story 全量 + tasks（design/实现）结构正确；
2. _build_story_prompt：执行铁律（design 先行 / story_handled / fail 协议）；
3. handle_story：
   - story_handled + 任务未全 done → 保持 confirmed（handled，下轮继续）；
   - 全部 task done → 调用 /complete 自动收尾；
   - 节流：min_interval 内重复拉起 → skipped；
   - agent fail → 评论 + 失败计数 → 连续 3 次 → PATCH blocked；
   - invoker 异常 → 评论 + failed；
4. agent_heartbeat_once：无 cli_command 跳过；坏 CLI 命令 → deregister；
   正常 CLI（sys.executable）→ heartbeat 置在线。
"""
import os
import sys
import threading
import time
import uuid

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentboard import worker  # noqa: E402
from agentboard.worker import (  # noqa: E402
    AgentDecision, ProposalWorker, WorkerConfig,
)


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

    def __init__(self, get_responses: dict | None = None,
                 request_status: int = 200,
                 request_status_map: dict[str, int] | None = None):
        self.calls: list[tuple[str, str]] = []
        self.get_responses = get_responses or {}
        self.request_status = request_status
        self.request_status_map = request_status_map or {}  # path 子串 → status

    def request(self, method: str, path: str, **kw):
        self.calls.append((method, path))
        if method == "GET" and path in self.get_responses:
            return _FakeResponse(200, self.get_responses[path])
        for frag, status in self.request_status_map.items():
            if frag in path:
                return _FakeResponse(status, {}, text="st")
        return _FakeResponse(self.request_status, {}, text="ok")

    def get(self, path: str, **kw):
        self.calls.append(("GET", path))
        resp = self.get_responses.get(path)
        return _FakeResponse(200, resp if resp is not None else {})


def _cfg() -> WorkerConfig:
    return WorkerConfig(api_url="http://x", token="t", agent_cmd="x",
                        agent_timeout=5)


def _story(**over) -> dict:
    base = {"id": 1, "epic_id": 2, "title": "S", "description": "d",
            "status": "confirmed", "needs_design": True}
    base.update(over)
    return base


def _tasks_response() -> dict:
    return {"items": [
        {"id": 11, "type": "design", "title": "设计：S", "status": "backlog"},
        {"id": 12, "type": "task", "title": "实现：S", "status": "backlog"},
    ]}


class _StubInvoker:
    def __init__(self, decision: AgentDecision | None = None, error: Exception | None = None):
        self.decision = decision
        self.error = error
        self.calls = 0

    def invoke(self, context):
        self.calls += 1
        if self.error:
            raise self.error
        return self.decision or AgentDecision(action="story_handled", summary="ok")


def _worker(client=None, invoker=None) -> ProposalWorker:
    w = ProposalWorker(_cfg(), invoker=invoker or _StubInvoker(), client=client)
    w._story_min_interval = 0.0
    return w


# ===================== Story 编排 =====================

def test_build_story_context_includes_tasks():
    client = _FakeClient(get_responses={"/api/stories/1/tasks": _tasks_response()})
    w = _worker(client=client)
    ctx = w.build_story_context(_story())
    assert ctx["action"] == "process_story"
    assert ctx["story_id"] == 1
    assert {t["type"] for t in ctx["tasks"]} == {"design", "task"}


def test_build_story_prompt_renders_rules():
    client = _FakeClient(get_responses={"/api/stories/1/tasks": _tasks_response()})
    w = _worker(client=client)
    prompt = worker._build_story_prompt(w.build_story_context(_story()))
    assert "story_handled" in prompt and '"fail"' in prompt
    assert "design" in prompt and "in_design" in prompt


def test_handle_story_partial_keeps_confirmed():
    """story_handled 但任务未全 done → handled，不触发 complete。"""
    client = _FakeClient(get_responses={"/api/stories/1/tasks": _tasks_response()})
    w = _worker(client=client)
    out = w.handle_story(_story())
    assert out == "handled"
    assert not any(m == "POST" and p.endswith("/complete")
                   for m, p in client.calls)


def test_handle_story_all_done_calls_complete():
    """全部 task done → story_handled → POST /complete 自动收尾。"""
    done_tasks = {"items": [
        {"id": 11, "type": "design", "title": "设计：S", "status": "done"},
        {"id": 12, "type": "task", "title": "实现：S", "status": "done"},
    ]}
    client = _FakeClient(get_responses={"/api/stories/1/tasks": done_tasks})
    w = _worker(client=client)
    out = w.handle_story(_story())
    assert out == "handled"
    assert ("POST", "/api/stories/1/complete") in client.calls


def test_handle_story_all_done_design_approved_counts_finished():
    """design task 终态 design_review_approved 亦视为完成（收尾判据）。"""
    done_tasks = {"items": [
        {"id": 11, "type": "design", "title": "设计：S",
         "status": "design_review_approved"},
        {"id": 12, "type": "task", "title": "实现：S", "status": "done"},
    ]}
    client = _FakeClient(get_responses={"/api/stories/1/tasks": done_tasks})
    w = _worker(client=client)
    assert w._story_all_tasks_done(_story()) is True
    out = w.handle_story(_story())
    assert out == "handled"
    assert ("POST", "/api/stories/1/complete") in client.calls


def test_handle_story_throttle():
    client = _FakeClient(get_responses={"/api/stories/1/tasks": _tasks_response()})
    w = _worker(client=client)
    w._story_min_interval = 30.0
    w._story_attempts[1] = time.time()
    assert w.handle_story(_story()) == "skipped"
    assert not client.calls  # 节流不产生任何 HTTP


def test_handle_story_fail_blocks_after_3():
    """agent fail 连续 3 次 → PATCH blocked 转人工 + 每次评论。"""
    client = _FakeClient(get_responses={"/api/stories/1/tasks": _tasks_response()})
    w = _worker(client=client, invoker=_StubInvoker(
        decision=AgentDecision(action="fail", error="no mcp")))
    w._story_min_interval = 0.0
    assert w.handle_story(_story()) == "failed"
    assert w.handle_story(_story()) == "failed"
    assert w.handle_story(_story()) == "blocked"
    comments = [c for c in client.calls if c[0] == "POST" and "/comments" in c[1]]
    assert len(comments) == 3
    assert ("PATCH", "/api/stories/1") in client.calls  # 置 blocked


def test_handle_story_invoker_exception_comments():
    client = _FakeClient(get_responses={"/api/stories/1/tasks": _tasks_response()})
    w = _worker(client=client, invoker=_StubInvoker(
        error=worker.AgentInvocationError("cli 挂了")))
    w._story_min_interval = 0.0
    assert w.handle_story(_story()) == "failed"
    assert any("/comments" in p for m, p in client.calls)


# ---------- 多实例竞争认领（2026-08-09） ----------

def test_handle_story_claim_conflict_skips():
    """claim 409（已被其它 Worker 认领）→ skipped，不拉起 agent。"""
    client = _FakeClient(get_responses={"/api/stories/1/tasks": _tasks_response()},
                         request_status_map={"/claim": 409})
    invoker = _StubInvoker()
    w = _worker(client=client, invoker=invoker)
    w._story_min_interval = 0.0
    assert w.handle_story(_story()) == "skipped"
    assert invoker.calls == 0


def test_handle_story_partial_unclaims_to_pool():
    """部分推进（任务未全完成）→ unclaim 回退 confirmed 交接。"""
    client = _FakeClient(get_responses={"/api/stories/1/tasks": _tasks_response()})
    w = _worker(client=client)
    w._story_min_interval = 0.0
    out = w.handle_story(_story())
    assert out == "handled"
    assert ("POST", "/api/stories/1/claim") in client.calls
    assert ("POST", "/api/stories/1/unclaim") in client.calls
    assert not any("complete" in p for m, p in client.calls)


def test_handle_story_fail_unclaims_then_blocks():
    """失败 → unclaim 回退；连续 3 次 → blocked（第 3 次不 unclaim）。"""
    client = _FakeClient(get_responses={"/api/stories/1/tasks": _tasks_response()})
    w = _worker(client=client, invoker=_StubInvoker(
        decision=AgentDecision(action="fail", error="no mcp")))
    w._story_min_interval = 0.0
    assert w.handle_story(_story()) == "failed"      # 第 1 次：unclaim
    assert w.handle_story(_story()) == "failed"      # 第 2 次：unclaim
    assert w.handle_story(_story()) == "blocked"     # 第 3 次：blocked 不 unclaim
    unclaims = [c for c in client.calls if "unclaim" in c[1]]
    assert len(unclaims) == 2
    assert ("PATCH", "/api/stories/1") in client.calls  # 置 blocked


# ===================== Agent 心跳探测 =====================

def _agent(agent_id: str, cli: str = "", model: str = "", enabled: bool = True) -> dict:
    return {"id": 1, "agent_id": agent_id, "name": "A", "roles": "[]",
            "capabilities": "[]", "cli_command": cli, "model": model,
            "enabled": enabled, "user_id": None,
            "online": False, "last_heartbeat": None, "probe_message": ""}


def test_heartbeat_skips_no_cli_and_deregisters_bad():
    client = _FakeClient(get_responses={
        "/api/agents": [_agent("a1"), _agent("a2", "definitely-not-a-real-cmd-xyz")]})
    w = _worker(client=client)
    stats = w.agent_heartbeat_once()
    assert stats["skipped"] == 1
    assert stats["offline"] == 1
    assert ("POST", "/api/agents/a2/deregister") in client.calls
    assert not any("a1" in p for m, p in client.calls if "heartbeat" in p)


def test_heartbeat_marks_good_cli_online():
    client = _FakeClient(get_responses={
        "/api/agents": [_agent("a3", sys.executable)]})  # python --version 成功
    w = _worker(client=client)
    stats = w.agent_heartbeat_once()
    assert stats["online"] == 1
    assert ("POST", "/api/agents/a3/heartbeat") in client.calls


def test_heartbeat_disabled_agent_skipped():
    client = _FakeClient(get_responses={
        "/api/agents": [_agent("a9", sys.executable, enabled=False)]})
    w = _worker(client=client)
    stats = w.agent_heartbeat_once()
    assert stats["skipped"] == 1
    assert stats["checked"] == 0
    assert not any("a9" in p for m, p in client.calls)


def test_heartbeat_model_placeholder_injected():
    """cli_command 含 {model}：probe 命令注入模型（argv 含模型名）→ 判活 online。"""
    # python -c 'sys.exit(0 if "hy3" in sys.argv else 1)' hy3 --version
    probe_script = "import sys; sys.exit(0 if 'hy3' in sys.argv else 1)"
    cli = f'{sys.executable} -c "{probe_script}" {{model}}'
    client = _FakeClient(get_responses={
        "/api/agents": [_agent("a5", cli, model="hy3")]})
    w = _worker(client=client)
    stats = w.agent_heartbeat_once()
    assert stats["online"] == 1, f"stats={stats}"
    assert ("POST", "/api/agents/a5/heartbeat") in client.calls


def test_heartbeat_loop_stops_on_event():
    w = _worker(client=_FakeClient())
    stop = threading.Event()
    stop.set()
    w._agent_heartbeat_loop(stop)  # 不抛异常即可


def test_heartbeat_skips_probe_timeout():
    """超时探测视为不可用（不抛异常，静默降级）。"""
    client = _FakeClient(get_responses={
        "/api/agents": [_agent("a4", sys.executable)]})
    w = _worker(client=client)
    w.config.heartbeat_timeout = 0.0001  # 必然超时
    stats = w.agent_heartbeat_once()
    assert stats["offline"] == 1
    assert ("POST", "/api/agents/a4/deregister") in client.calls
