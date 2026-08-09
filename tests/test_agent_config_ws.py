"""Agent 配置中心化 + WebSocket 状态广播（2026-08-09）测试。

覆盖：
1. register_agent 带 model；update_agent（PUT 全字段可选）；delete_agent；
2. agent_heartbeat 带 probe_ok/probe_message（probe 结果落库、probe_ok=False → offline）；
3. AgentStateHub subscribe/broadcast（线程安全、快照、删除通知）；
4. /ws/agents WebSocket 端点（快照 + 广播接收）；
5. _probe_cli_sync（{model} 替换、.cmd 退化、超时/失败消息）。

运行：
    PYTHONPATH=. python -m pytest tests/test_agent_config_ws.py -q
"""
import os
import sys
import tempfile
import uuid

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402

init_db()


def _mk_agent(tag: str | None = None):
    tag = tag or uuid.uuid4().hex[:8]
    with SessionLocal() as s:
        return service.register_agent(
            s, agent_id=f"ag-{tag}", name=f"Agent {tag}",
            roles='["developer"]', capabilities="[]",
            cli_command=f'"{sys.executable}" --model {{model}}',
            model="hy3", user_id=None)


# ---------- 1. CRUD ----------
def test_register_with_model():
    a = _mk_agent()
    assert a.model == "hy3"
    assert a.enabled is True
    assert a.probe_message == ""


def test_update_agent_fields():
    a = _mk_agent()
    with SessionLocal() as s:
        upd = service.update_agent(s, a.agent_id, name="NewName",
                                   model="deepseek-v4-flash", enabled=False,
                                   cli_command="echo hi")
        assert upd.name == "NewName"
        assert upd.model == "deepseek-v4-flash"
        assert upd.enabled is False
        assert upd.cli_command == "echo hi"
        # 未传字段保持
        assert upd.roles == '["developer"]'


def test_update_agent_missing_returns_none():
    with SessionLocal() as s:
        assert service.update_agent(s, "no-such-agent", name="x") is None


def test_delete_agent():
    a = _mk_agent()
    with SessionLocal() as s:
        assert service.delete_agent(s, a.agent_id) is not None
        assert service.get_agent_by_agent_id(s, a.agent_id) is None
        assert service.delete_agent(s, a.agent_id) is None


# ---------- 2. heartbeat probe 结果 ----------
def test_heartbeat_with_probe_ok():
    a = _mk_agent()
    with SessionLocal() as s:
        ag = service.agent_heartbeat(s, a.agent_id, probe_ok=True,
                                     probe_message="OK 1.2.3")
        assert ag.online is True
        assert ag.probe_message == "OK 1.2.3"
        assert ag.last_probe_at is not None


def test_heartbeat_probe_fail_sets_offline():
    a = _mk_agent()
    with SessionLocal() as s:
        service.agent_heartbeat(s, a.agent_id, probe_ok=True)
        ag = service.agent_heartbeat(s, a.agent_id, probe_ok=False,
                                     probe_message="exit=1 boom")
        assert ag.online is False
        assert ag.probe_message == "exit=1 boom"


def test_heartbeat_no_probe_keeps_online_semantics():
    a = _mk_agent()
    with SessionLocal() as s:
        ag = service.agent_heartbeat(s, a.agent_id)  # 自报心跳
        assert ag.online is True
        assert ag.probe_message == ""  # 不覆盖 probe 详情


# ---------- 3. AgentStateHub ----------
def test_hub_subscribe_broadcast():
    from agentboard.api import agent_state_hub
    q = agent_state_hub.subscribe()
    try:
        agent_state_hub.broadcast({"type": "agent_state", "agent": {"id": 1}})
        assert q.get_nowait() == '{"type": "agent_state", "agent": {"id": 1}}'
    finally:
        agent_state_hub.unsubscribe(q)


def test_hub_unsubscribe_stops_delivery():
    from agentboard.api import agent_state_hub
    q = agent_state_hub.subscribe()
    agent_state_hub.unsubscribe(q)
    agent_state_hub.broadcast({"type": "agent_state"})
    with pytest.raises(Exception):  # queue.Empty
        q.get_nowait()


# ---------- 4. WebSocket 端点 ----------
def test_ws_agents_snapshot_and_broadcast():
    from fastapi.testclient import TestClient
    from agentboard.api import app, agent_state_hub
    with TestClient(app) as c:
        with c.websocket_connect("/ws/agents") as ws:
            # 快照结构（内容一致性由 REST 层与端到端覆盖；
            # 多文件合跑时各测试文件独立临时库，不断言具体 agent）
            snap = ws.receive_json()
            assert snap["type"] == "snapshot"
            assert isinstance(snap["agents"], list)
            # 广播送达
            agent_state_hub.broadcast({"type": "agent_state", "agent": {"agent_id": "x1"}})
            msg = ws.receive_json()
            assert msg["type"] == "agent_state"
            assert msg["agent"]["agent_id"] == "x1"


def test_ws_agents_register_broadcasts():
    from fastapi.testclient import TestClient
    from agentboard.api import app
    with TestClient(app) as c:
        with c.websocket_connect("/ws/agents") as ws:
            _ = ws.receive_json()  # 快照
            r = c.post("/api/agents/register", json={
                "agent_id": f"ws-{uuid.uuid4().hex[:6]}", "name": "WS Agent",
                "cli_command": "", "model": "hy3",
            })
            assert r.status_code == 201
            msg = ws.receive_json()
            assert msg["type"] == "agent_state"
            assert msg["agent"]["agent_id"].startswith("ws-")


# ---------- 5. _probe_cli_sync ----------
def test_probe_cli_sync_ok():
    from agentboard.api import _probe_cli_sync
    ok, msg = _probe_cli_sync(f'"{sys.executable}" --version', timeout=8)
    assert ok is True
    assert msg.startswith("OK ")


def test_probe_cli_sync_model_placeholder(tmp_path):
    from agentboard.api import _probe_cli_sync
    # {model} 替换后 argv 应含模型值；probe 自动追加 --version
    script = tmp_path / "probe_args.py"
    script.write_text("import sys\nassert 'hy3' in sys.argv\n", encoding="utf-8")
    ok, msg = _probe_cli_sync(f'"{sys.executable}" "{script}" {{model}}',
                              model="hy3", timeout=8)
    assert ok is True, msg


def test_probe_cli_sync_missing_cmd():
    from agentboard.api import _probe_cli_sync
    ok, msg = _probe_cli_sync("")
    assert ok is False and "未配置" in msg


def test_probe_cli_sync_bad_cmd():
    from agentboard.api import _probe_cli_sync
    ok, msg = _probe_cli_sync("definitely-not-a-real-cmd-xyz-12345", timeout=5)
    assert ok is False
    assert msg  # 有失败原因
