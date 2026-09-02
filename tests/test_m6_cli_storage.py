"""Implementation Plan T6.2/T4.2 · Worker 本地 CLI 存储 + 心跳本地优先 回归测试。

为什么本地优先（而不是 server 优先 + 本地兜底）：执行面配置的真源在
Worker 本机（143 宪法）—— server 说不清本机有什么，却替机器决定用什么
命令，方向本来就是反的。兼容语义：本地没有记录时回落 server 下发的
``cli_command``，存量部署零迁移成本。

运行：
    PYTHONPATH=src/backend-fastapi python -m pytest tests/test_m6_cli_storage.py -q
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "src", "backend-fastapi")
sys.path.insert(0, _BACKEND)

from agentboard.agent_runtime import cli_storage  # noqa: E402
from agentboard.agent_runtime.cli_storage import (  # noqa: E402
    CliInstall, load_store, resolve_cli, save_store,
)


@pytest.fixture()
def store_path(tmp_path):
    return tmp_path / "cli_installs.json"


@pytest.fixture(autouse=True)
def _isolated_store(store_path, monkeypatch):
    """所有测试隔离到临时存储，别碰真实用户目录。"""
    monkeypatch.setenv("AGENTBOARD_WORKER_CLI_STORE", str(store_path))


# ---------- 1. 存储 ----------

def test_missing_store_is_empty_not_error(store_path):
    """存储不存在 → 空结构（心跳常驻循环不能因文件缺失停摆）。"""
    assert load_store(store_path) == {"version": 1, "installs": {}}
    assert resolve_cli("any-agent") is None


def test_corrupt_store_does_not_crash(store_path):
    store_path.write_text("{not json", encoding="utf-8")
    assert load_store(store_path) == {"version": 1, "installs": {}}
    assert resolve_cli("any-agent") is None


def test_save_load_roundtrip(store_path):
    save_store({"codex": {"path": "C:/cli/codex.cmd", "model": "gpt"}})
    data = load_store(store_path)
    assert data["installs"]["codex"]["path"] == "C:/cli/codex.cmd"


# ---------- 2. 解析优先级 ----------

def test_resolve_priority_agent_over_type_over_default(store_path):
    save_store({
        "codex-agent-1": {"path": "C:/cli/precise.cmd"},   # agent_id 精确
        "codex": {"path": "C:/cli/by-type.cmd"},           # executor_type
        "*": {"path": "C:/cli/default.cmd"},               # 本机默认
    })
    assert resolve_cli("codex-agent-1", "codex").path == "C:/cli/precise.cmd"
    # 无精确命中 → 按 executor_type
    assert resolve_cli("other-agent", "codex").path == "C:/cli/by-type.cmd"
    # 都没有 → 默认
    assert resolve_cli("other-agent", "qa").path == "C:/cli/default.cmd"
    # 连默认都没有 → None
    save_store({"codex": {"path": "C:/cli/by-type.cmd"}})
    assert resolve_cli("other-agent", "qa") is None


def test_resolve_skips_entries_without_path(store_path):
    """path 为空的记录无效，继续向下兜底（而不是返回一条空命令）。"""
    save_store({
        "codex-agent-1": {"path": ""},
        "*": {"path": "C:/cli/default.cmd"},
    })
    assert resolve_cli("codex-agent-1", "codex").path == "C:/cli/default.cmd"


# ---------- 3. 命令构造 ----------

def test_command_for_model_placeholder():
    inst = CliInstall(path="codebuddy -p --model {model}", model="gpt-x")
    assert inst.command_for("glm-4") == "codebuddy -p --model glm-4"
    # 未指定 model → 用记录里的默认
    assert "gpt-x" in inst.command_for("")
    # 本地无 {model} 占位 → 原样
    assert CliInstall(path="codex").command_for("glm-4") == "codex"


def test_argv_list_no_shell_injection():
    """argv 列表形式执行，含空格/引号的参数不会被 shell 重新解释。"""
    inst = CliInstall(path="codebuddy -p", args_extra=["--config my file.json"])
    argv = inst.argv()
    assert argv == ["codebuddy", "-p", "--config", "my", "file.json"]


def test_secret_ref_is_reference_not_secret():
    """存储格式只放 secret:// 引用 —— 密钥不出本机（143 宪法）。"""
    save_store({"codex": {"path": "codex", "secret_ref": "secret://codex/work"}})
    inst = resolve_cli("codex")
    assert inst.secret_ref == "secret://codex/work"
    assert inst.secret_ref.startswith("secret://")


# ---------- 4. T4.2：心跳本地优先 ----------

def test_heartbeat_resolve_prefers_local_over_server(monkeypatch, store_path):
    """本地有记录 → 用本地命令，忽略 server 下发的 cli_command。"""
    from agentboard.agent_runtime.heartbeat import _resolve_cmd_local
    save_store({"codex-agent-1": {"path": "C:/cli/local.cmd",
                                  "model": "local-model"}})
    stats: dict = {}
    cmd, model = _resolve_cmd_local(
        agent_id="codex-agent-1", executor_type="codex", model="",
        fallback_cmd="server-cmd --bad", fallback_model="server-model",
        stats=stats)
    assert cmd == "C:/cli/local.cmd"
    assert model == "local-model"
    assert stats["cli_source"] == "local"


def test_heartbeat_falls_back_to_server_when_no_local(monkeypatch, store_path):
    """本地无记录 → 回落 server 下发命令（存量部署零迁移）。"""
    from agentboard.agent_runtime.heartbeat import _resolve_cmd_local
    stats: dict = {}
    cmd, _ = _resolve_cmd_local(
        agent_id="no-local-agent", executor_type="codex", model="",
        fallback_cmd="server-cmd", fallback_model="",
        stats=stats)
    assert cmd == "server-cmd"
    assert stats["cli_source"] == "server"


def test_legacy_path_skip_problem_resolved(monkeypatch, store_path):
    """config.py:89 的「已知问题」消解验证：/api/agents 不返回 cli_command
    （legacy 路径 skip 全体）—— 本地有存储后命令照样解析得出来。"""
    from agentboard.agent_runtime.heartbeat import _resolve_cmd_local
    save_store({"*": {"path": "C:/cli/default.cmd"}})
    stats: dict = {}
    cmd, _ = _resolve_cmd_local(
        agent_id="whatever", executor_type="", model="",
        fallback_cmd="", fallback_model="", stats=stats)
    assert cmd == "C:/cli/default.cmd", \
        "server 不给 cli_command 时本地默认 CLI 应当接管，legacy 不再全体 skip"
