"""Agent CLI 心跳探测（Epic 123 Step 2 · 从 worker.py 拆分）。

Ticket 全流程（2026-08-09）：worker 主动经 CLI 判活（``<cmd> --version``），
成功上报 heartbeat（probe_ok=true），失败 deregister（probe_message 带原因）。
Flake 修复（2026-08-10 review）：探测前 sleep(0.05) + 显式 stdout/stderr=PIPE。
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Any

import httpx

from .invokers import split_command

log = logging.getLogger("agentboard.worker.heartbeat")


def probe_cli(config: Any, cmd: str, model: str = "") -> tuple[bool, str]:
    """CLI 可用性探测：``<cmd> --version``（8s 超时）。

    - ``{model}`` 占位符替换（同 CLI 多 agent 各注入模型；空 model 移除占位符）；
    - 返回 ``(ok, message)``：message 为探测详情（版本号 / 超时 / 退出码）。
    """
    full = str(cmd or "").strip().replace("{model}", (model or "").strip())
    if not full.strip():
        return False, "未配置 cli_command"
    if "{model}" in full:
        full = full.replace("{model}", "").strip()
    try:
        argv = split_command(full) + ["--version"]
    except ValueError as e:
        return False, f"命令解析失败：{e}"
    # Flake 修复（2026-08-10 review）：探测前让出 GIL + 显式 PIPE 替代 capture_output
    time.sleep(0.05)
    try:
        proc = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=config.heartbeat_timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"探测超时 {config.heartbeat_timeout}s"
    except (OSError, FileNotFoundError, ValueError) as e:
        log.debug("Agent CLI 探测失败 %r：%s", cmd, e)
        return False, f"无法启动 CLI：{e}"
    ok = proc.returncode == 0
    detail = ""
    if (proc.stdout or "").strip():
        detail = proc.stdout.strip().splitlines()[0][:80]
    elif (proc.stderr or "").strip():
        detail = proc.stderr.strip().splitlines()[-1][:80]
    msg = (f"OK {detail}" if ok else f"exit={proc.returncode} {detail}").strip()
    return ok, msg or ("OK" if ok else f"exit={proc.returncode}")


def agent_heartbeat_once(client: httpx.Client, config: Any) -> dict:
    """执行一轮 Agent 心跳探测：遍历 agents 表，逐 agent 跑 cli_command 判活。

    - 成功 → POST /api/agents/{id}/heartbeat（probe_ok=true + 版本详情）；
    - 失败 → POST /api/agents/{id}/deregister（probe_message 带原因）；
    - 无 cli_command / enabled=false 的 agent 跳过。
    """
    try:
        agents = client.request("GET", "/api/agents").json() or []
    except Exception as e:
        log.warning("拉取 Agent 列表失败（心跳探测跳过本轮）：%s", e)
        return {"checked": 0, "online": 0, "offline": 0, "skipped": 0}
    stats = {"checked": 0, "online": 0, "offline": 0, "skipped": 0}
    for a in agents or []:
        aid = a.get("agent_id")
        cmd = a.get("cli_command") or ""
        if not aid or not cmd or not a.get("enabled", True):
            stats["skipped"] += 1
            continue
        stats["checked"] += 1
        ok_r = False
        try:
            ok, msg = probe_cli(config, cmd, model=a.get("model") or "")
            if ok:
                r = client.request("POST", f"/api/agents/{aid}/heartbeat",
                                   json={"probe_ok": True, "probe_message": msg})
                ok_r = r.status_code in (200, 201)
                stats["online"] += 1 if ok_r else 0
            else:
                r = client.request("POST", f"/api/agents/{aid}/deregister",
                                   json={"probe_message": msg})
                ok_r = r.status_code in (200, 201)
                stats["offline"] += 1 if ok_r else 0
            if not ok_r:
                log.warning("Agent %s probe 结果上报失败（HTTP %s）", aid, r.status_code)
        except Exception as e:
            log.warning("Agent %s 心跳上报异常：%s", aid, e)
    if stats["checked"]:
        log.info("Agent 心跳探测：%s", stats)
    return stats
