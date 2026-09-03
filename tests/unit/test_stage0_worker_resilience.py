"""Stage 0 worker 止血回归测试（2026-08-26）。

覆盖五项修复：
1. AsyncWorkExecutor：(kind,id) in-flight 去重、per-kind 通道隔离、drain_finished 实装；
2. 路由键白名单对齐真实 action（review_task / process_task），未知键告警忽略、历史别名归一化；
3. 子进程环境隔离：AGENTBOARD_* 凭据族不继承给子 agent CLI；
4. Story/Task 租约回收端点接入（maintenance.reclaim_stale_stories/tasks）；
5. MQ 瞬时错误 requeue：MessageRetry 三态判定 + ProposalProcessor 回查退避重试。

运行：
    PYTHONPATH=. python -m pytest tests/unit/test_stage0_worker_resilience.py -q
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
import pytest  # noqa: E402

from agentboard.processors.config import AgentDecision, ProcessorConfig  # noqa: E402
from agentboard.processors import invokers as inv_mod  # noqa: E402
from agentboard.processors import maintenance as maint  # noqa: E402
from agentboard.core.infrastructure import messaging as mq  # noqa: E402
from agentboard.core.infrastructure.messaging.rabbitmq import InMemoryBroker  # noqa: E402


# ---------- fakes ----------

class _StubInvoker:
    """可控延迟的 invoker 替身：记录调用并返回固定决策。"""

    def __init__(self, sleep_s: float = 0.0):
        self.sleep_s = sleep_s
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def invoke(self, context):
        with self._lock:
            self.calls.append(context)
        if self.sleep_s:
            time.sleep(self.sleep_s)
        return AgentDecision(action=context.get("action", "clarify"),
                             summary="ok")


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None,
                 text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _RecordingClient:
    """记录 HTTP 调用并按 path 返回预设响应的 client 替身。"""

    def __init__(self, routes: dict[str, object] | None = None):
        self.routes = routes or {}
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, **kw):
        self.calls.append((method, path))
        for key, val in self.routes.items():
            if path == key or path.startswith(key):
                if isinstance(val, Exception):
                    raise val
                return val
        return _FakeResponse(payload={})


def _build_worker(invoker=None, client=None) -> "object":
    from agentboard.processors.worker import ProposalProcessor
    cfg = ProcessorConfig(
        api_url="http://127.0.0.1:9",
        token="t",
        poll_interval=0.05,
        agent_cmd='"echo" "noop"',
        agent_timeout=5,
        async_story_executor=True,
    )
    return ProposalProcessor(
        cfg,
        invoker=invoker or _StubInvoker(),
        client=client or httpx.Client(timeout=1.0),
    )


# ---------- 1. AsyncWorkExecutor ----------

def test_executor_dedups_inflight_same_story():
    """同一 story 在后台未完成时重复 submit 必须被拒绝（根治排队堆积）。"""
    ex = _build_executor()
    slow_handler = mock.MagicMock(return_value="handled")
    gate = threading.Event()

    def _slow_handle(item, invoker):
        gate.wait(timeout=5)
        return "handled"

    slow_handler.handle.side_effect = _slow_handle
    ex._handlers["story"] = slow_handler

    story = {"id": 42}
    assert ex.submit("story", story) == "submitted"
    assert ex.submit("story", {"id": 42}) == "duplicate_inflight"
    assert ex.inflight_count("story") == 1
    gate.set()
    ex.shutdown()


def _build_executor():
    from agentboard.processors.async_story import AsyncWorkExecutor
    handlers = {}
    for k in AsyncWorkExecutor.KINDS:
        h = mock.MagicMock()
        h.handle.return_value = "handled"
        handlers[k] = h
    return AsyncWorkExecutor(invoker=_StubInvoker(), handlers=handlers)


def test_executor_per_kind_isolation():
    """慢 story 占住自己的槽位时，ticket/clarify 通道仍能立即执行。"""
    ex = _build_executor()
    release = threading.Event()
    story_calls: list[int] = []

    def _slow_story(item, invoker):
        story_calls.append(item["id"])
        release.wait(timeout=5)
        return "handled"

    ex._handlers["story"].handle.side_effect = _slow_story
    ticket_done = threading.Event()

    def _fast_ticket(item, invoker):
        ticket_done.set()
        return "handled"

    ex._handlers["ticket"].handle.side_effect = _fast_ticket

    assert ex.submit("story", {"id": 1}) == "submitted"
    time.sleep(0.1)  # 让 story 先占住通道
    assert ex.submit("ticket", {"id": 9}) == "submitted"
    assert ticket_done.wait(timeout=3), "慢 story 不应饿死 ticket 通道"
    release.set()
    ex.shutdown()
    finished = dict((k, o) for k, _, o in [(k, i, o) for k, i, o in ex.drain_finished()])
    assert finished.get("story") == "handled"
    assert finished.get("ticket") == "handled"


def test_drain_finished_returns_and_clears():
    ex = _build_executor()
    assert ex.submit("clarify", {"id": 7}) == "submitted"
    ex.shutdown()
    first = ex.drain_finished()
    assert ("clarify", 7, "handled") in first
    assert ex.drain_finished() == []  # 取走即清空


# ---------- 2. 路由键白名单 ----------

def test_known_routing_actions_align_real_actions():
    """白名单必须包含 Worker 真实产生的 action（Stage 0 修正 review→review_task）。"""
    assert "review_task" in inv_mod.KNOWN_ROUTING_ACTIONS
    assert "process_task" in inv_mod.KNOWN_ROUTING_ACTIONS
    assert "process_story" in inv_mod.KNOWN_ROUTING_ACTIONS
    # 旧错误键不再作为合法键出现
    assert "review" not in inv_mod.KNOWN_ROUTING_ACTIONS


def test_parse_routing_normalizes_legacy_alias(monkeypatch):
    monkeypatch.setenv("AGENTBOARD_WORKER_AGENT_ROUTING",
                       '{"review": "codebuddy"}')
    out = inv_mod.parse_agent_routing()
    assert out == {"review_task": "codebuddy"}


def test_parse_routing_warns_and_skips_unknown_key(monkeypatch):
    monkeypatch.setenv("AGENTBOARD_WORKER_AGENT_ROUTING",
                       '{"typo_action": "minimax", "process_story": "codebuddy"}')
    warnings: list[str] = []
    monkeypatch.setattr(inv_mod.log, "warning",
                        lambda msg, *args: warnings.append(msg % args if args else msg))
    out = inv_mod.parse_agent_routing()
    assert out == {"process_story": "codebuddy"}
    assert any("未知路由键" in w for w in warnings)


# ---------- 3. 子进程环境隔离 ----------

def test_sanitize_env_preserves_mcp_api_key_but_strips_worker_credentials():
    env = inv_mod.sanitize_subprocess_env({
        "PATH": "C:/bin",
        "AgentBoard_Api_Key": "mcp-key",
        "AGENTBOARD_WORKER_TOKEN": "secret",
        "agentboard_mcp_token": "leak",   # 小写也要拦
        "AGENTBOARD_API_URL": "https://should-not-leak.example",
        "OTHER": "keep",
    })
    assert env["PATH"] == "C:/bin"
    assert env["OTHER"] == "keep"
    assert env["AgentBoard_Api_Key"] == "mcp-key"
    assert "AGENTBOARD_WORKER_TOKEN" not in env
    assert "agentboard_mcp_token" not in env
    assert "AGENTBOARD_API_URL" not in env
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_subprocess_invoker_env_never_inherits_credentials(monkeypatch):
    monkeypatch.setenv("AGENTBOARD_WORKER_TOKEN", "super-secret")
    monkeypatch.setenv("AGENTBOARD_API_KEY", "mcp-key")
    inv = inv_mod.SubprocessProcessorInvoker('"echo" "noop"', timeout=1)
    assert "AGENTBOARD_WORKER_TOKEN" not in inv.env
    assert inv.env["AGENTBOARD_API_KEY"] == "mcp-key"
    assert inv.env["PYTHONUTF8"] == "1"


# ---------- 4. Story/Task 租约回收接入 ----------

def test_reclaim_stale_stories_hits_new_endpoint():
    routes = {
        "/api/stories/reclaim-stale": _FakeResponse(payload={"reclaimed": [11, 12]}),
    }
    client = _RecordingClient(routes)

    class _Cfg:
        lease_seconds = 1800

    ids = maint.reclaim_stale_stories(client, _Cfg())
    assert ids == [11, 12]
    assert ("POST", "/api/stories/reclaim-stale") in client.calls


def test_reclaim_stale_tasks_hits_new_endpoint():
    routes = {
        "/api/tasks/reclaim-stale": _FakeResponse(payload={"reclaimed": [7]}),
    }
    client = _RecordingClient(routes)

    class _Cfg:
        lease_seconds = 1800

    ids = maint.reclaim_stale_tasks(client, _Cfg())
    assert ids == [7]
    assert ("POST", "/api/tasks/reclaim-stale") in client.calls


def test_poll_once_reports_story_and_task_reclaim_keys():
    w = _build_worker()
    fake_story = {"id": 9001, "story_id": 9001, "title": "demo", "epic_id": 1,
                  "tasks": [], "status": "confirmed"}
    with mock.patch.object(w, "fetch_confirmed_stories", return_value=[]), \
         mock.patch.object(w, "fetch_work", return_value=[]), \
         mock.patch.object(w, "fetch_ticket_requests", return_value=[]), \
         mock.patch.object(w, "reclaim_stale", return_value=[]), \
         mock.patch.object(w, "reclaim_stale_ticket_requests", return_value=[]), \
         mock.patch.object(w, "reclaim_stale_stories", return_value=[9001]), \
         mock.patch.object(w, "reclaim_stale_tasks", return_value=[8001]), \
         mock.patch.object(w, "recover_failed", return_value=[]):
        summary = w.poll_once()
    assert summary["story_reclaimed"] == [9001]
    assert summary["task_reclaimed"] == [8001]
    w.close()


# ---------- 5. MQ 瞬时错误 requeue ----------

def test_inmemory_broker_message_retry_requeues_not_dead():
    broker = InMemoryBroker()
    attempts = {"n": 0}

    def flaky(msg):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise mq.MessageRetry("网络抖动")
        return True

    body = mq.ProposalMessage(proposal_id=5, round=1).to_bytes()
    broker.publish_raw(body)  # InMemoryBroker 无 routing key（单队列）
    stats = broker.consume(flaky, max_messages=2, idle_timeout=2)
    assert stats["consumed"] == 2
    assert stats["acked"] == 1
    assert stats["retried"] == 1
    assert stats["dead"] == 0
    assert broker.dead_letters() == []


def test_proposal_worker_transient_lookup_raises_retry(monkeypatch):
    """回查网络异常 → 抛 MessageRetry（broker requeue），不再直接进死信。"""
    w = _build_worker()
    monkeypatch.setattr(type(w), "MSG_RETRY_BACKOFF", (0.0,))
    monkeypatch.setattr(w, "MSG_RETRY_BACKOFF", (0.0,))
    err_client = _RecordingClient(
        routes={"/api/proposals/": ConnectionError("boom")})
    w.client = err_client
    msg = mq.ProposalMessage(proposal_id=77, round=1)
    with pytest.raises(Exception) as excinfo:
        w.handle_message(msg)
    assert "MessageRetry" in type(excinfo.value).__name__
    assert w._msg_retries[77] == 1


def test_proposal_worker_retry_exhaustion_goes_deadletter(monkeypatch):
    """连续瞬时失败超过退避序列长度 → 放弃重试，return False 进死信。"""
    w = _build_worker()
    monkeypatch.setattr(type(w), "MSG_RETRY_BACKOFF", (0.0, 0.0))
    monkeypatch.setattr(w, "MSG_RETRY_BACKOFF", (0.0, 0.0))
    err_client = _RecordingClient(
        routes={"/api/proposals/": ConnectionError("boom")})
    w.client = err_client
    msg = mq.ProposalMessage(proposal_id=78, round=1)
    with pytest.raises(Exception) as excinfo1:
        w.handle_message(msg)
    assert "MessageRetry" in type(excinfo1.value).__name__
    with pytest.raises(Exception) as excinfo2:
        w.handle_message(msg)
    assert "MessageRetry" in type(excinfo2.value).__name__
    assert w.handle_message(msg) is False  # 第 3 次：耗尽 → 死信
    assert 78 not in w._msg_retries  # 计数已清


def test_proposal_worker_5xx_is_transient_but_404_drops(monkeypatch):
    """server 5xx 视为瞬时（requeue）；404 视为永久缺失（ack 丢弃）。"""
    w = _build_worker()
    monkeypatch.setattr(type(w), "MSG_RETRY_BACKOFF", (0.0,))
    monkeypatch.setattr(w, "MSG_RETRY_BACKOFF", (0.0,))
    w.client = _RecordingClient(routes={
        "/api/proposals/503": _FakeResponse(status_code=503, text="overloaded"),
    })
    with pytest.raises(Exception) as excinfo:
        w.handle_message(mq.ProposalMessage(proposal_id=503, round=1))
    assert "MessageRetry" in type(excinfo.value).__name__

    w.client = _RecordingClient(routes={
        "/api/proposals/404": _FakeResponse(status_code=404, text="gone"),
    })
    assert w.handle_message(mq.ProposalMessage(proposal_id=404, round=1)) is True


def test_proposal_worker_successful_lookup_clears_retry_counter(monkeypatch):
    w = _build_worker()
    monkeypatch.setattr(type(w), "MSG_RETRY_BACKOFF", (0.0,))
    ok = _RecordingClient(routes={
        "/api/proposals/79": _FakeResponse(payload={"id": 79, "status": "queued"}),
    })
    # handle 会继续走 handler 链路 —— mock 掉避免真实副作用
    with mock.patch.object(w, "handle", return_value="ask") as h:
        w.client = ok
        assert w.handle_message(mq.ProposalMessage(proposal_id=79, round=1)) is True
        h.assert_called_once()
    w._msg_retries[79] = 3  # 人为污染后再次成功消费应清除
    with mock.patch.object(w, "handle", return_value="ask"):
        w.handle_message(mq.ProposalMessage(proposal_id=79, round=1))
    assert 79 not in w._msg_retries


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
