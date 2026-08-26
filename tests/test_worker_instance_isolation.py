"""两 Worker 互不影响回归测试（2026-08-26 P1 修复）。

场景：
  Worker A 安装了 codex，没装 claude
  Worker B 安装了 claude，没装 codex

旧 bug：``agent_heartbeat_once`` 走 ``GET /api/agents`` 拉全表逐个探测。
Worker A 跑 ``claude --version`` 失败 → 触发 ``/api/agents/claude/deregister``，
把 B 的健康 claude agent 也打 offline；B 同样把 A 的 codex 打 offline。
→ 两台机器互相把对方 Agent 打 offline，正确的判定全被破坏。

修复后（验证项）：
  1. Worker A 的探测只触达 A 的 AgentInstance，**绝不**调 B 的 deregister。
  2. A 的 ``codex`` 探测失败 → A 的 ``codex-agent`` instance offline，
     B 的 ``codex-agent`` instance / B 的任何 instance **不变**。
  3. ``Agent.online`` 聚合：A offline + B online = true（任一 online 即 online）。
  4. A 调 ``/api/workers/B/agent-instances/{id}/heartbeat`` → 403，
     B 的 instance 状态不变。
  5. A 调 ``/api/agents/codex-agent/instances`` 挂 B 的 instance → 走 service 层
     校验（不通过 URL 路径，但 instance.worker_id=B 是事实）。A 拿到 B 的
     instance **没有**触发 deregister —— 它根本没机会调 deregister
     （deregister 路径带 worker_id 强校验）。

策略：service 层 + 端到端（TestClient）双测；service 层覆盖核心 ownership 校验，
端到端覆盖 ``agent_heartbeat_once`` 调 HTTP 路径是否触达错 Worker。
"""
from __future__ import annotations

import os
import sys
import uuid
import importlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 用临时 SQLite 隔离 DB（多测试互不影响）
TEST_DB = f"_test_worker_isolation_{uuid.uuid4().hex[:8]}.db"


def _make_session():
    """每个测试一个临时 SQLite DB（create_all 同步建表）。"""
    # 显式 import 用到的 model（让 Base.metadata 知道）—— 不依赖全局 schema 同步
    from agentboard.core.common.models import Base
    from agentboard.features.projects.models import Agent  # noqa: F401
    from agentboard.features.projects.models import AgentInstance  # noqa: F401
    from agentboard.features.projects.models import Worker  # noqa: F401

    eng = create_engine(
        f"sqlite:///{TEST_DB}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, autoflush=False)
    return eng, Sess()


# ----- 直接用 service 层做 ownership 校验（不依赖 FastAPI Client） -----

def _bootstrap_two_workers(s):
    """注册 worker A / B，register agent "codex-agent"，各自挂 instance。"""
    from agentboard.features.projects.models import Agent
    from agentboard.features.scheduling.service import (
        register_worker, upsert_agent_instance,
    )

    register_worker(s, worker_id="worker-A", hostname="host-a")
    register_worker(s, worker_id="worker-B", hostname="host-b")
    a = Agent(agent_id="codex-agent", name="Codex Agent",
              cli_command="", model="", auth_key="")
    s.add(a); s.commit()
    upsert_agent_instance(s, worker_id="worker-A", agent_id="codex-agent",
                          cli_command=sys.executable)  # A 的 cli 在本机可用
    upsert_agent_instance(s, worker_id="worker-B", agent_id="codex-agent",
                          cli_command="nonexistent-cli-xyz")  # B 的 cli 不存在
    return a


# ======================== 1. Service 层 ownership 校验 ========================

def test_instance_heartbeat_rejects_cross_worker():
    """A 调 B 的 instance heartbeat → Forbidden，B 的 instance 不变。"""
    eng, s = _make_session()
    try:
        agent = _bootstrap_two_workers(s)
        from agentboard.features.scheduling.service import (
            get_agent_instance, instance_heartbeat, list_agent_instances,
        )
        b_inst = next(i for i in list_agent_instances(s, worker_id="worker-B")
                      if i.agent_id == "codex-agent")
        b_inst_id = b_inst.id

        # 起始 B instance 是 online=False（刚建）
        assert b_inst.online is False

        # A 试图以 B 的 worker_id 报 B 的 instance heartbeat
        from agentboard.core.exceptions import Forbidden
        with pytest.raises(Forbidden):
            instance_heartbeat(s, b_inst_id, caller_worker_id="worker-A",
                               probe_ok=True, probe_message="OK 1.0")

        # B 的 instance 状态没变
        fresh = get_agent_instance(s, b_inst_id)
        assert fresh.online is False
        assert fresh.last_heartbeat is None
    finally:
        s.close(); eng.dispose()
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)


def test_instance_deregister_rejects_cross_worker():
    """A 调 B 的 instance deregister → Forbidden。"""
    eng, s = _make_session()
    try:
        _bootstrap_two_workers(s)
        from agentboard.features.scheduling.service import (
            get_agent_instance, instance_deregister, list_agent_instances,
            instance_heartbeat,
        )
        b_inst = next(i for i in list_agent_instances(s, worker_id="worker-B")
                      if i.agent_id == "codex-agent")
        # 先让 B 的 instance 处于 online=True（B 自己是 owner）
        instance_heartbeat(s, b_inst.id, caller_worker_id="worker-B",
                           probe_ok=True, probe_message="OK 1.0")
        assert get_agent_instance(s, b_inst.id).online is True

        from agentboard.core.exceptions import Forbidden
        with pytest.raises(Forbidden):
            instance_deregister(s, b_inst.id, caller_worker_id="worker-A",
                                probe_message="hijack attempt")

        # 状态没变
        assert get_agent_instance(s, b_inst.id).online is True
    finally:
        s.close(); eng.dispose()
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)


def test_instance_heartbeat_requires_caller_worker_id():
    """空 caller_worker_id 必须抛 InvalidValue —— 这是防 bypass 的关键。"""
    eng, s = _make_session()
    try:
        _bootstrap_two_workers(s)
        from agentboard.features.scheduling.service import (
            instance_heartbeat, list_agent_instances,
        )
        inst = list_agent_instances(s, worker_id="worker-A")[0]
        from agentboard.core.exceptions import InvalidValue
        with pytest.raises(InvalidValue):
            instance_heartbeat(s, inst.id, caller_worker_id="",
                               probe_ok=True, probe_message="x")
    finally:
        s.close(); eng.dispose()
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)


# ======================== 2. agent.online 聚合 ========================

def test_agent_online_aggregates_any_instance_online():
    """任一 instance online → Agent.online = true；全 offline → false。"""
    eng, s = _make_session()
    try:
        agent = _bootstrap_two_workers(s)
        from agentboard.features.projects.models import Agent as AgentModel
        from agentboard.features.scheduling.service import (
            instance_heartbeat, instance_deregister, list_agent_instances,
        )

        # 初始：全 offline → Agent.online = false
        a = s.get(AgentModel, agent.id)
        assert a.online is False

        # A instance online → Agent.online = true
        a_inst = next(i for i in list_agent_instances(s, worker_id="worker-A")
                      if i.agent_id == "codex-agent")
        instance_heartbeat(s, a_inst.id, caller_worker_id="worker-A",
                           probe_ok=True, probe_message="OK")
        s.refresh(a)
        assert a.online is True

        # A offline + B 仍 offline → Agent.online = false
        instance_deregister(s, a_inst.id, caller_worker_id="worker-A",
                            probe_message="cli gone")
        s.refresh(a)
        assert a.online is False

        # B online（即使 A 仍 offline）→ Agent.online = true
        b_inst = next(i for i in list_agent_instances(s, worker_id="worker-B")
                      if i.agent_id == "codex-agent")
        instance_heartbeat(s, b_inst.id, caller_worker_id="worker-B",
                           probe_ok=True, probe_message="OK")
        s.refresh(a)
        assert a.online is True
    finally:
        s.close(); eng.dispose()
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)


# ======================== 3. agent_heartbeat_once 走新路径 ========================

class _RecordingClient:
    """记录所有 HTTP 请求，可编程返回。"""

    def __init__(self, responses: dict | None = None):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses = responses or {}
        # 默认 instances 列表：worker-A 的 codex 在本机可用（sys.executable），
        # worker-B 的 codex 不存在（探测会失败）
        self.responses.setdefault(
            "/api/workers/worker-A/instances",
            [{
                "id": 101, "worker_id": "worker-A", "agent_id": "codex-agent",
                "cli_command": sys.executable, "model": "", "enabled": True,
                "online": False,
            }],
        )

    def request(self, method: str, path: str, json=None):
        self.calls.append((method, path, json))
        if path in self.responses:
            from tests.test_worker_heartbeat import _FakeResponse
            body = self.responses[path]
            return _FakeResponse(200, body if not isinstance(body, Exception) else {})
        if path.startswith("/api/workers/worker-A/agent-instances/101/"):
            from tests.test_worker_heartbeat import _FakeResponse
            return _FakeResponse(200, {"id": 101, "online": True,
                                        "cli_command": sys.executable})
        from tests.test_worker_heartbeat import _FakeResponse
        return _FakeResponse(200, {})


def test_heartbeat_once_uses_new_path_when_worker_id_set():
    """config.worker_id 非空 → 调 ``/api/workers/.../instances`` 路径。"""
    from agentboard.worker import WorkerConfig
    from agentboard.agent_runtime.heartbeat import agent_heartbeat_once

    cfg = WorkerConfig(api_url="http://x", token="t", worker_id="worker-A",
                       heartbeat_timeout=2.0, heartbeat_interval=1.0)
    client = _RecordingClient()
    stats = agent_heartbeat_once(client, cfg)

    # 验证：调的是新路径，不是旧 /api/agents
    paths = [p for m, p, _ in client.calls]
    assert any(p == "/api/workers/worker-A/instances" for p in paths), \
        f"expected new path, got: {paths}"
    assert not any(p == "/api/agents" for p in paths), \
        f"should NOT hit old /api/agents when worker_id set: {paths}"
    assert stats["mode"] == "instance"
    assert stats["worker_id"] == "worker-A"
    assert stats["checked"] == 1
    # sys.executable --version 应该成功
    assert stats["online"] == 1
    assert stats["offline"] == 0


def test_heartbeat_once_falls_back_to_legacy_when_worker_id_empty():
    """config.worker_id 空 → 走旧 ``/api/agents`` 路径。"""
    from agentboard.worker import WorkerConfig
    from agentboard.agent_runtime.heartbeat import agent_heartbeat_once
    from tests.test_worker_heartbeat import _FakeClient

    cfg = WorkerConfig(api_url="http://x", token="t", worker_id="",
                       heartbeat_timeout=2.0)
    client = _FakeClient(get_responses={"/api/agents": []})
    stats = agent_heartbeat_once(client, cfg)
    assert stats["mode"] == "legacy"
    assert ("GET", "/api/agents") in client.calls


def test_heartbeat_once_worker_a_does_not_touch_worker_b():
    """核心场景：Worker A 探测只调 A 的 instance，**绝不**调 B 的 instance。"""
    from agentboard.worker import WorkerConfig
    from agentboard.agent_runtime.heartbeat import agent_heartbeat_once

    cfg = WorkerConfig(api_url="http://x", token="t", worker_id="worker-A",
                       heartbeat_timeout=2.0)
    # A 看到本机只有 A 的 instance（不返回 B 的）
    client = _RecordingClient(responses={
        "/api/workers/worker-A/instances": [
            {"id": 101, "worker_id": "worker-A", "agent_id": "codex-agent",
             "cli_command": sys.executable, "model": "", "enabled": True,
             "online": False},
        ],
    })
    agent_heartbeat_once(client, cfg)

    paths = [p for m, p, _ in client.calls]
    # **关键断言**：A 没调任何 /api/workers/worker-B/... 路径
    assert not any("worker-B" in p for p in paths), \
        f"Worker A leaked to Worker B: {paths}"
    # A 调的是 /api/workers/worker-A/agent-instances/101/heartbeat
    assert any(p == "/api/workers/worker-A/agent-instances/101/heartbeat"
               for p in paths), f"missing heartbeat path: {paths}"
    # A 没调旧的 /api/agents/{id}/deregister
    assert not any("/api/agents/" in p and p.endswith("/deregister")
                   for p in paths), \
        f"Worker A called old deregister: {paths}"
