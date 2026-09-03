"""Implementation Plan T6.2/T4.2 · worker 本地注册表 + 心跳本地优先 回归测试。

单一真源原则：worker 的 agent 配置真源**已经存在** ——
``worker/local_registry.py``（``~/.codebuddy/agents.db`` SQLite，portal 写入、
WSS 推送 server 缓存）。T4.2 的正确实现是让心跳**读同一份本地库**，
而不是再造一套 JSON 存储（cli_storage.py 那版已删除——重复造轮子）。

兼容语义：本地无记录时回落 server 下发的 ``cli_command``，存量部署零迁移。

运行：
    PYTHONPATH=src/backend-fastapi python -m pytest tests/test_m6_cli_storage.py -q
"""
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "src", "backend-fastapi")
sys.path.insert(0, _BACKEND)

from agentboard.processors.heartbeat import _resolve_cmd_local  # noqa: E402
from agentboard.processors.local_registry import LocalAgentRegistry  # noqa: E402


@pytest.fixture(autouse=True)
def local_db(tmp_path, monkeypatch):
    """隔离的本地注册表（真实用户目录绝不能被动到）。"""
    db = str(tmp_path / "agents.db")
    monkeypatch.setenv("AGENTBOARD_LOCAL_AGENT_DB", db)
    registry = LocalAgentRegistry(db_path=db)
    yield registry


def _stats():
    return {}


# ---------- 1. 解析优先级 ----------

def test_exact_agent_id_match_wins(local_db):
    local_registry = local_db
    local_registry.upsert("codex-agent-1", cli_command="C:/cli/precise.cmd",
                          model="m1", enabled=True,
                          roles=["developer"])
    local_registry.upsert("codex-agent-2", cli_command="C:/cli/other.cmd",
                          model="m2", enabled=True,
                          roles=["developer"])
    cmd, model = _resolve_cmd_local(
        agent_id="codex-agent-1", executor_type="codex", model="",
        fallback_cmd="server-cmd", fallback_model="", stats=_stats())
    assert cmd == "C:/cli/precise.cmd"
    assert model == "m1"


def test_falls_back_to_first_enabled_local_agent(local_db):
    """无精确命中 → 本机默认兜底（第一个 enabled 且配置了命令的）。"""
    local_registry = local_db
    local_registry.upsert("some-agent", cli_command="C:/cli/default.cmd",
                          model="dm", enabled=True, roles=["developer"])
    cmd, model = _resolve_cmd_local(
        agent_id="unknown-agent", executor_type="", model="",
        fallback_cmd="server-cmd", fallback_model="", stats=_stats())
    assert cmd == "C:/cli/default.cmd"
    assert model == "dm"


def test_disabled_local_agent_not_used_as_fallback(local_db):
    """兜底跳过 disabled 的本地 agent。"""
    local_registry = local_db
    local_registry.upsert("a-off", cli_command="C:/cli/off.cmd",
                          model="", enabled=False, roles=["developer"])
    local_registry.upsert("a-on", cli_command="C:/cli/on.cmd",
                          model="", enabled=True, roles=["developer"])
    cmd, _ = _resolve_cmd_local(
        agent_id="unknown", executor_type="", model="",
        fallback_cmd="server-cmd", fallback_model="", stats=_stats())
    assert cmd == "C:/cli/on.cmd"


def test_empty_local_db_falls_back_to_server(local_db):
    """本地库为空 → 回落 server 下发命令（存量部署零迁移）。"""
    cmd, _ = _resolve_cmd_local(
        agent_id="any", executor_type="", model="",
        fallback_cmd="server-cmd", fallback_model="", stats=_stats())
    assert cmd == "server-cmd"


def test_corrupt_local_db_does_not_crash(tmp_path, monkeypatch):
    """本地库损坏 → 按无配置处理、回落 server，不抛异常（心跳是常驻循环）。"""
    db = tmp_path / "broken.db"
    db.write_bytes(b"not a sqlite file at all")
    monkeypatch.setenv("AGENTBOARD_LOCAL_AGENT_DB", str(db))
    cmd, _ = _resolve_cmd_local(
        agent_id="any", executor_type="", model="",
        fallback_cmd="server-cmd", fallback_model="", stats=_stats())
    assert cmd == "server-cmd"


# ---------- 2. source 标记（排障口径） ----------

def test_stats_marks_local_and_server_sources(local_db, monkeypatch):
    local_registry = local_db
    local_registry.upsert("a1", cli_command="C:/cli/a1.cmd",
                          model="", enabled=True, roles=["developer"])
    stats_local: dict = {}
    _resolve_cmd_local(agent_id="a1", executor_type="", model="",
                       fallback_cmd="server-cmd", fallback_model="",
                       stats=stats_local)
    assert stats_local["cli_source"] == "local"

    # server 回落要求本地库**没有任何可用 agent**（本机有 enabled agent 时，
    # "no-such" 走本机默认兜底 —— 那是设计语义，见上一个断言）
    empty_db = os.path.join(tempfile.mkdtemp(), "empty.db")
    monkeypatch.setenv("AGENTBOARD_LOCAL_AGENT_DB", empty_db)
    stats_server: dict = {}
    _resolve_cmd_local(agent_id="no-such", executor_type="", model="",
                       fallback_cmd="server-cmd", fallback_model="",
                       stats=stats_server)
    assert stats_server["cli_source"] == "server"


# ---------- 3. T4.2 验收：前端/portal 配的 CLI 对启动生效 ----------

def test_portal_written_config_feeds_heartbeat(local_db):
    """portal（LocalAgentRegistry）写入的配置，心跳立即可用 —— 一份数据两个消费方。"""
    local_registry = local_db
    # 模拟 portal 保存动作（portal 调的就是同一个 upsert）
    saved = local_registry.upsert(
        "portal-agent", cli_command="codebuddy -p --model {model}",
        model="glm-4", enabled=True, roles=["developer"])
    assert saved.agent_id == "portal-agent"

    stats: dict = {}
    cmd, model = _resolve_cmd_local(
        agent_id="portal-agent", executor_type="", model="",
        fallback_cmd="", fallback_model="", stats=stats)
    # {model} 占位符**保留原样**返回 —— 替换是 probe_cli 的职责
    # （它拿 model 参数做注入），_resolve_cmd_local 只负责选命令和选模型
    assert cmd == "codebuddy -p --model {model}"
    assert model == "glm-4", "未指定 model 时用本地记录里的默认模型"
    assert stats["cli_source"] == "local"
