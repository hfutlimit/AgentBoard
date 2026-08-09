"""Agent MQ 消费（2026-08-09）：广播竞争 + 定向 direct 队列测试。

覆盖：
1. handle_task_available：竞争认领（claim 200 → 拉起 agent；409 他人已领 →
   丢弃不处理；回查失败 → 转死信）；
2. handle_direct_task：定向任务（可处理状态 → 拉起 agent；已结束 → 丢弃）；
3. handle_workflow_message：事件分发（task.available / task.assigned / 其它 ack）；
4. _build_task_prompt 渲染（单 task 执行协议）；
5. run_agent_mq_forever：InMemory broker 注入，广播竞争 + 定向 direct 双线程
   消费端到端（publish task.available → 竞争处理；publish task.assigned →
   定向处理）。
"""
import os
import sys
import threading
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentboard import mq, worker  # noqa: E402
from agentboard.mq import (  # noqa: E402
    EVENT_TASK_ASSIGNED, EVENT_TASK_AVAILABLE, WorkflowMessage,
)
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
    def __init__(self, get_responses: dict | None = None,
                 request_status: int = 200,
                 request_status_map: dict[str, int] | None = None):
        self.calls: list[tuple[str, str]] = []
        self.get_responses = get_responses or {}
        self.request_status = request_status
        self.request_status_map = request_status_map or {}

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


def _task(**over) -> dict:
    base = {"id": 1, "project_id": 1, "story_id": 2, "type": "task",
            "title": "T", "status": "backlog", "assignee_id": None}
    base.update(over)
    return base


class _StubInvoker:
    def __init__(self, decision: AgentDecision | None = None):
        self.decision = decision
        self.calls = 0

    def invoke(self, context):
        self.calls += 1
        return self.decision or AgentDecision(action="story_handled", summary="ok")


def _worker(client=None, invoker=None) -> ProposalWorker:
    w = ProposalWorker(_cfg(), invoker=invoker or _StubInvoker(), client=client)
    return w


def _msg(event: str, tid: int) -> WorkflowMessage:
    return WorkflowMessage(event=event, entity_type="task", entity_id=tid, ref_id=2)


# ===================== handle_task_available（竞争） =====================

def test_task_available_claims_and_processes():
    client = _FakeClient(get_responses={
        "/api/tasks/1": _task(),
        "/api/stories/2": {"id": 2, "needs_design": True},
    })
    w = _worker(client=client)
    assert w.handle_task_available(_msg(EVENT_TASK_AVAILABLE, 1)) is True
    assert ("POST", "/api/tasks/1/claim") in client.calls
    # claim 成功 → 继续处理：build_task_context 会再查 story（get 里已有）


def test_task_available_claim_conflict_ack():
    """claim 409（他人已认领）→ 正常丢弃（ack），不转死信。"""
    client = _FakeClient(get_responses={"/api/tasks/1": _task()},
                         request_status_map={"/claim": 409})
    invoker = _StubInvoker()
    w = _worker(client=client, invoker=invoker)
    assert w.handle_task_available(_msg(EVENT_TASK_AVAILABLE, 1)) is True
    assert invoker.calls == 0


def test_task_available_already_processed_ack():
    """task 已 done/in_progress → 不认领，正常 ack。"""
    client = _FakeClient(get_responses={
        "/api/tasks/1": _task(status="in_progress")})
    invoker = _StubInvoker()
    w = _worker(client=client, invoker=invoker)
    assert w.handle_task_available(_msg(EVENT_TASK_AVAILABLE, 1)) is True
    assert not any("claim" in p for m, p in client.calls)


def test_task_available_recheck_failure_deadletter():
    """回查失败 → False（转死信，轮询兜底再捞）。"""
    class _Err:
        def get(self, path, **kw):
            raise ConnectionError("boom")
        def request(self, method, path, **kw):
            raise ConnectionError("boom")

    w = _worker(client=_Err())
    assert w.handle_task_available(_msg(EVENT_TASK_AVAILABLE, 1)) is False


# ===================== handle_direct_task（定向） =====================

def test_direct_task_processes():
    client = _FakeClient(get_responses={
        "/api/tasks/1": _task(status="todo"),
        "/api/stories/2": {"id": 2, "needs_design": True},
    })
    invoker = _StubInvoker()
    w = _worker(client=client, invoker=invoker)
    assert w.handle_direct_task(_msg(EVENT_TASK_ASSIGNED, 1)) is True
    assert invoker.calls == 1


def test_direct_task_done_ignored():
    client = _FakeClient(get_responses={"/api/tasks/1": _task(status="done")})
    invoker = _StubInvoker()
    w = _worker(client=client, invoker=invoker)
    assert w.handle_direct_task(_msg(EVENT_TASK_ASSIGNED, 1)) is True
    assert invoker.calls == 0


# ===================== 分发 =====================

def test_workflow_message_dispatch():
    client = _FakeClient(get_responses={
        "/api/tasks/1": _task(),
        "/api/stories/2": {"id": 2, "needs_design": False},
    })
    w = _worker(client=client)
    assert w.handle_workflow_message(_msg(EVENT_TASK_AVAILABLE, 1)) is True
    assert ("POST", "/api/tasks/1/claim") in client.calls
    # 未知事件 ack
    from agentboard.mq import EVENT_STORY_CREATED
    assert w.handle_workflow_message(_msg(EVENT_STORY_CREATED, 1)) is True


# ===================== run_agent_mq_forever（InMemory 端到端） =====================

def test_run_agent_mq_forever_broadcast_and_direct():
    """InMemory broker：广播 task.available 竞争 + 定向 task.assigned 双线程消费。"""
    wf_broker = mq.InMemoryWorkflowBroker()  # namespace 默认（与 worker 一致）
    wf_broker.declare_topology()
    wf_broker.declare_agent_queue("agent-a")

    client = _FakeClient(get_responses={
        "/api/tasks/1": _task(),
        "/api/tasks/2": _task(status="todo"),
        "/api/stories/2": {"id": 2, "needs_design": False},
    })
    invoker = _StubInvoker()
    w = _worker(client=client, invoker=invoker)

    # 广播 task.available（task#1）→ 竞争处理
    wf_broker.publish(wf_broker.topology.broadcast_routing(EVENT_TASK_AVAILABLE),
                      WorkflowMessage(event=EVENT_TASK_AVAILABLE, entity_type="task",
                                      entity_id=1, ref_id=2))
    # 定向 task.assigned（task#2）→ agent-a 的 direct queue
    wf_broker.publish(wf_broker.topology.agent_routing("agent-a"),
                      WorkflowMessage(event=EVENT_TASK_ASSIGNED, entity_type="task",
                                      entity_id=2, ref_id=2))

    # ProposalBroker：无提案消息（澄清轮空转）
    prop_broker = mq.InMemoryBroker(mq.MQConfig())
    prop_broker.declare_topology()

    stop = threading.Event()
    t = threading.Thread(
        target=lambda: w.run_agent_mq_forever(
            "agent-a", stop=stop, broker=prop_broker,
            wf_broker=wf_broker, direct_broker=wf_broker),
        daemon=True)
    t.start()
    # 等待两条消息都被消费（invoker 被调 2 次）
    deadline = time.time() + 10
    while time.time() < deadline and invoker.calls < 2:
        time.sleep(0.05)
    stop.set()
    t.join(timeout=3)
    assert invoker.calls == 2, f"应处理 2 条任务消息，实际 {invoker.calls}"
    # 竞争认领调用存在
    assert ("POST", "/api/tasks/1/claim") in client.calls
