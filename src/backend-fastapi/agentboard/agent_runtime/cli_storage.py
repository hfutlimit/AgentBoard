"""Worker 本地 CLI 存储格式（Implementation Plan T6.2 / T4.2）。

为什么要有这个模块
------------------
143 宪法：Control Plane / Execution Plane 分离 —— CLI 路径、参数、凭据是
**执行面**配置，归属 Worker 本机；server 只保留安全清单（safe inventory）。
此前（2026-08-09「Agent 配置中心化」）CLI 配置存在 server、worker 每次心跳
从 API 拉回来 —— 方向是反的：server 说不清自己机器上有什么，却替机器决定
用什么命令。

存储格式（v1）
--------------
JSON 文件，位置（按优先级）：

1. ``AGENTBOARD_WORKER_CLI_STORE`` 环境变量（显式指定，测试/多 worker 共机用）；
2. Windows：``%LocalAppData%/AgentBoard/worker/cli_installs.json``；
3. POSIX：``~/.agentboard/worker/cli_installs.json``。

::

    {
      "version": 1,
      "installs": {
        "<agent_id>":       {"path": "...", "model": "", "version": "", "secret_ref": ""},
        "<executor_type>":  {"path": "...", ...},   # 兜底：按执行器类型
        "*":                {"path": "...", ...}    # 再兜底：本机默认 CLI
      }
    }

解析优先级（T4.2 的核心语义）：**agent_id 精确 > executor_type > "*" 默认**。
精确匹配保证「同一个 worker 上不同 agent 用不同 CLI」；两级兜底保证新 agent
零配置可用。

安全边界
--------
- ``secret_ref`` 是对 Worker 本机 secret store 的**引用**（如
  ``secret://codex/work``），不是密钥本身 —— 密钥不出本机；
- 本模块只做读/写与解析，不做任何 subprocess —— 执行仍在
  ``heartbeat.probe_cli`` / adapters。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("agentboard.worker.cli_storage")

STORE_VERSION = 1
DEFAULT_FILENAME = "cli_installs.json"


def default_store_path() -> Path:
    """平台默认存储路径。env ``AGENTBOARD_WORKER_CLI_STORE`` 优先。"""
    env = os.environ.get("AGENTBOARD_WORKER_CLI_STORE", "").strip()
    if env:
        return Path(env)
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "AgentBoard" / "worker" / DEFAULT_FILENAME
    return Path.home() / ".agentboard" / "worker" / DEFAULT_FILENAME


@dataclass
class CliInstall:
    """一条本机 CLI 安装记录。

    ``path``       可执行文件路径（或 PATH 上可解析的命令名）；
    ``model``      该 CLI 的默认模型（可为空，``{model}`` 占位符注入用）；
    ``version``    安装时记录的 CLI 版本（inventory，不参与解析）；
    ``secret_ref`` 凭据引用（``secret://...``，**不是**密钥明文）；
    ``args_extra`` 固定附加参数（放在 ``{model}`` 替换之后、子命令之前）。
    """
    path: str
    model: str = ""
    version: str = ""
    secret_ref: str = ""
    args_extra: list[str] = field(default_factory=list)

    def command_for(self, model: str = "") -> str:
        """拼出可执行命令行（``{model}`` 占位符替换语义与 server 侧一致）。

        ``args_extra`` 中元素含空格时不做 re-join——调用方拿
        :attr:`argv` 走列表执行，避免 shell 注入面。
        """
        cmd = str(self.path or "").strip()
        if "{model}" in cmd:
            cmd = cmd.replace("{model}", (model or self.model or "").strip())
        return cmd

    def argv(self, model: str = "") -> list[str]:
        """列表形式的完整 argv（``{model}`` 已替换），供 subprocess 直接用。"""
        base = shlex_split(self.command_for(model))
        extra: list[str] = []
        for a in self.args_extra:
            a = str(a)
            extra.extend(shlex_split(a.replace("{model}", (model or self.model or "").strip())))
        return base + extra


def shlex_split(cmd: str) -> list[str]:
    """POSIX shell 风格切分（Windows 下 ``subprocess`` 也能吃这个列表）。"""
    import shlex

    try:
        return shlex.split(cmd or "")
    except ValueError:
        # 引号不配对等畸形输入：整串作为一个 argv，让下游报错时能看见原文
        return [cmd] if (cmd or "").strip() else []


def _coerce_install(raw: Any) -> CliInstall | None:
    if not isinstance(raw, dict):
        return None
    path = str(raw.get("path", "")).strip()
    if not path:
        return None
    args_extra = raw.get("args_extra") or []
    if not isinstance(args_extra, list):
        args_extra = []
    return CliInstall(
        path=path,
        model=str(raw.get("model", "") or ""),
        version=str(raw.get("version", "") or ""),
        secret_ref=str(raw.get("secret_ref", "") or ""),
        args_extra=[str(a) for a in args_extra],
    )


def load_store(path: Path | None = None) -> dict[str, Any]:
    """读取本地存储；不存在/损坏返回空结构（**不抛异常**）。

    心跳是常驻循环，存储文件损坏不应该让 worker 整个停摆 —— 打 WARNING
    后按「无本地配置」处理，调用方回落 server 旧路径。
    """
    p = Path(path) if path else default_store_path()
    if not p.is_file():
        return {"version": STORE_VERSION, "installs": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("cli_storage: 本地 CLI 存储 %s 读取失败（%s），按空处理", p, e)
        return {"version": STORE_VERSION, "installs": {}}
    if not isinstance(data, dict):
        log.warning("cli_storage: 本地 CLI 存储 %s 格式异常（非对象），按空处理", p)
        return {"version": STORE_VERSION, "installs": {}}
    installs = data.get("installs")
    if not isinstance(installs, dict):
        installs = {}
    return {"version": data.get("version", STORE_VERSION), "installs": installs}


def save_store(installs: dict[str, dict[str, Any]], path: Path | None = None) -> Path:
    """写入本地存储（全量覆盖；调用方先 load 再改再存）。"""
    p = Path(path) if path else default_store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": STORE_VERSION, "installs": installs}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def resolve_cli(
    agent_id: str = "", executor_type: str = "", *,
    path: Path | None = None,
) -> CliInstall | None:
    """按优先级解析本机 CLI：agent_id 精确 > executor_type > "*"。

    任一命中即返回；全部未命中返回 ``None``（调用方回落 server 旧路径）。
    """
    store = load_store(path)
    installs = store.get("installs") or {}
    for key in filter(None, (str(agent_id or "").strip(),
                             str(executor_type or "").strip(), "*")):
        raw = installs.get(key)
        if raw is None:
            continue
        install = _coerce_install(raw)
        if install is not None:
            return install
    return None
