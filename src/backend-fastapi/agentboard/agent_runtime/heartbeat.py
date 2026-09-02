"""Agent CLI 心跳探测（Epic 123 Step 2 · 从 worker.py 拆分）。

Ticket 全流程（2026-08-09）：worker 主动经 CLI 判活（``<cmd> --version``），
成功上报 heartbeat（probe_ok=true），失败 deregister（probe_message 带原因）。
Flake 修复（2026-08-10 review）：探测前 sleep(0.05) + 显式 stdout/stderr=PIPE。

2026-08-26 P1 修复：多 Worker 部署隔离。``config.worker_id`` 非空时改走
``/api/workers/{worker_id}/agent-instances`` 路径，**只探测本机**；
探测结果通过 ``/api/workers/{worker_id}/agent-instances/{id}/{heartbeat,deregister}``
上报（URL path worker_id 强校验 ownership，防 A 覆盖 B）。原 ``GET /api/agents``
路径保留为 ``worker_id`` 留空时的旧单 Worker 兜底。

2026-08-26 23:06 修复：worker 自注册（commit 4480967 留的 P2 follow-up）。
首次心跳前调 ``POST /api/workers/register`` 把本机 upsert 到 ``workers`` 表，
后续才能走 ``GET /api/workers/{id}/instances`` 拿到 AgentInstance 列表。
"""
from __future__ import annotations

import logging
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from .invokers import split_command

log = logging.getLogger("agentboard.worker.heartbeat")


def _resolve_cmd_local(agent_id: str, executor_type: str, model: str,
                       fallback_cmd: str, fallback_model: str,
                       stats: dict, source_key: str = "cli_source",
                       ) -> tuple[str, str]:
    """T4.2/T6.2：CLI 命令**本地优先**。

    Worker 本地存储（cli_storage，含 path/model/args_extra/secret_ref）是
    执行面配置的真源；server 下发的 ``cli_command`` 降级为兜底 —— 兼容
    尚未写本地存储的存量部署，同时让「worker 本机配置即可用」成立。
    返回 (cmd, model)；解析失败（本地无记录且 server 也空）两者为空，
    调用方按未配置 skip。
    """
    # 真源是 worker 本地注册表（worker/local_registry.py，portal 写入的
    # 同一份 ~/.codebuddy/agents.db）—— 不是再造一套 JSON 存储。
    # 解析优先级：agent_id 精确 > 本机第一个 enabled 且配置了命令的 agent
    # （本机默认兜底）；两者皆无 → 回落 server 下发（存量兼容）。
    from ..worker.local_registry import LocalAgentRegistry
    db_path = os.environ.get("AGENTBOARD_LOCAL_AGENT_DB", "").strip() or None
    try:
        registry = (LocalAgentRegistry(db_path=db_path) if db_path
                    else LocalAgentRegistry())
        la = registry.get(agent_id) if agent_id else None
        if la is None or not (la.cli_command or "").strip():
            for cand in registry.list_agents():
                if cand.enabled and (cand.cli_command or "").strip():
                    la = cand
                    break
    except Exception as e:  # 存储读取异常不阻断心跳
        log.warning("本地 CLI 注册表读取异常（agent=%s）：%s", agent_id, e)
        la = None
    if la is not None and (la.cli_command or "").strip():
        stats[source_key] = "local"
        return la.cli_command, (model or la.model or "")
    stats[source_key] = "server"
    return fallback_cmd, (model or fallback_model)


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
    if "--mcp-config" in argv:
        try:
            mcp_path = Path(argv[argv.index("--mcp-config") + 1])
        except (IndexError, TypeError, ValueError):
            return False, "MCP 配置参数缺失"
        if not mcp_path.is_file():
            return False, f"MCP 配置不存在：{mcp_path}"
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
    probe_payload: dict[str, Any] | None = None
    # Some adapter commands deliberately encode invocation failures as a
    # structured ``{"action":"fail", ...}`` decision while exiting with 0.
    # Treating that protocol-level failure as a healthy version probe creates
    # a misleading online Agent that can never accept work (notably the
    # MiniMax stdin adapter when ``--version`` supplies no prompt).
    stdout_text = (proc.stdout or "").strip()
    if ok and stdout_text:
        try:
            probe_payload = json.loads(stdout_text)
        except json.JSONDecodeError:
            probe_payload = None
        if isinstance(probe_payload, dict) and str(probe_payload.get("action", "")).lower() == "fail":
            ok = False
    detail = ""
    if stdout_text:
        if isinstance(probe_payload, dict) and probe_payload.get("error"):
            detail = str(probe_payload["error"]).strip()[:80]
        else:
            detail = stdout_text.splitlines()[0][:80]
    elif (proc.stderr or "").strip():
        detail = proc.stderr.strip().splitlines()[-1][:80]
    msg = (f"OK {detail}" if ok else f"exit={proc.returncode} {detail}").strip()
    return ok, msg or ("OK" if ok else f"exit={proc.returncode}")


def _empty_stats(worker_id: str = "") -> dict:
    return {
        "checked": 0, "online": 0, "offline": 0, "skipped": 0,
        "mode": "legacy", "worker_id": worker_id,
    }


# Hostname lazy cache：worker 进程生命周期内 hostname 不会变，避免每个心跳都调
# socket.gethostname()（Linux 上涉及 DNS 反查，~10ms 浪费）
_HOSTNAME_CACHE: str | None = None


def _get_local_hostname() -> str:
    """best-effort hostname（失败回空字符串，注册接口允许 hostname 为空）。"""
    global _HOSTNAME_CACHE
    if _HOSTNAME_CACHE is None:
        try:
            _HOSTNAME_CACHE = socket.gethostname()
        except Exception:
            _HOSTNAME_CACHE = ""
    return _HOSTNAME_CACHE


def _ensure_worker_registered(
    client: httpx.Client, worker_id: str, status: str = "active",
) -> bool:
    """Worker 自注册：``POST /api/workers/register`` upsert 本机到 ``workers`` 表。

    幂等：每次心跳都调（server 端 upsert）。失败不抛，降级到 legacy 心跳路径。

    返回 True = 注册成功 / 已存在；False = 调用失败（worker 仍在 legacy 路径跑）。
    """
    try:
        resp = client.request(
            "POST", "/api/workers/register",
            json={
                "worker_id": worker_id,
                "hostname": _get_local_hostname(),
                "status": status,
            },
        )
        if 200 <= resp.status_code < 300:
            return True
        log.warning(
            "Worker %s 自注册失败（HTTP %s）：%s",
            worker_id, resp.status_code, (resp.text or "")[:200],
        )
        return False
    except Exception as e:
        log.warning("Worker %s 自注册异常：%s", worker_id, e)
        return False


def agent_heartbeat_once(client: httpx.Client, config: Any) -> dict:
    """执行一轮 Agent 心跳探测。

    **2026-08-26 P1 修复**：路由按 ``config.worker_id`` 切分：

    - ``worker_id`` 非空 → 走 :func:`_heartbeat_via_instances`，只探测本机
      AgentInstance，**绝不**触达其他 Worker；
    - 空 → 走 :func:`_heartbeat_via_agents_legacy`，旧 ``GET /api/agents`` 路径
      （保留给单 Worker 历史部署；该路径已知 ``/api/agents`` 不返回
      ``cli_command`` 是预存问题，本 change 不引入新症状）。
    """
    worker_id = (getattr(config, "worker_id", "") or "").strip()
    if worker_id:
        return _heartbeat_via_instances(client, config, worker_id)
    return _heartbeat_via_agents_legacy(client, config)


def _heartbeat_via_instances(
    client: httpx.Client, config: Any, worker_id: str,
) -> dict:
    """多 Worker 部署（推荐）：Worker 只探测本机 AgentInstance。

    流程：
    0. （2026-08-26 修复）``POST /api/workers/register`` 自注册（幂等，失败降级 legacy）
    1. ``GET /api/workers/{worker_id}/instances`` → 本机 instances（owner 视角含 ``cli_command``）
    2. 逐 instance 跑 ``<cli> --version``
    3. 成功 → ``POST /api/workers/{worker_id}/agent-instances/{id}/heartbeat``
    4. 失败 → ``POST /api/workers/{worker_id}/agent-instances/{id}/deregister``

    URL path ``worker_id`` 由 service 层强校验 ownership，caller 不能不带
    worker_id 调通（service 必传 ``caller_worker_id``，空字符串直接 400）。
    """
    stats = {
        "checked": 0, "online": 0, "offline": 0, "skipped": 0,
        "mode": "instance", "worker_id": worker_id,
    }
    # Step 0：自注册（commit 4480967 留的 P2 follow-up）
    # 失败不阻塞：fallback 走 legacy 路径，下轮心跳重试
    _ensure_worker_registered(client, worker_id)
    try:
        resp = client.request("GET", f"/api/workers/{worker_id}/instances")
        if resp.status_code >= 400:
            log.warning("Worker %s 拉取 instances 失败（HTTP %s）：%s",
                        worker_id, resp.status_code, (resp.text or "")[:200])
            return stats
        instances = resp.json() or []
    except Exception as e:
        log.warning("Worker %s 拉取 instances 异常：%s", worker_id, e)
        return stats
    for inst in instances or []:
        iid = inst.get("id")
        agent_id = inst.get("agent_id") or ""
        cmd, model_v = _resolve_cmd_local(
            agent_id, str(inst.get("executor_type") or ""),
            str(inst.get("model") or ""),
            str(inst.get("cli_command") or ""), "",
            stats)
        if not iid or not cmd or not inst.get("enabled", True):
            stats["skipped"] += 1
            continue
        stats["checked"] += 1
        try:
            ok, msg = probe_cli(config, cmd, model=model_v)
            base = f"/api/workers/{worker_id}/agent-instances/{iid}"
            if ok:
                r = client.request("POST", f"{base}/heartbeat",
                                   json={"probe_ok": True, "probe_message": msg})
                if r.status_code in (200, 201):
                    stats["online"] += 1
                else:
                    log.warning("Instance %s (agent=%s) heartbeat 上报失败 HTTP %s",
                                iid, agent_id, r.status_code)
            else:
                r = client.request("POST", f"{base}/deregister",
                                   json={"probe_message": msg})
                if r.status_code in (200, 201):
                    stats["offline"] += 1
                else:
                    log.warning("Instance %s (agent=%s) deregister 上报失败 HTTP %s",
                                iid, agent_id, r.status_code)
        except Exception as e:
            log.warning("Instance %s (agent=%s) 心跳上报异常：%s", iid, agent_id, e)
    if stats["checked"]:
        log.info("Worker %s 实例心跳：%s", worker_id, stats)
    return stats


def _heartbeat_via_agents_legacy(client: httpx.Client, config: Any) -> dict:
    """旧路径（``worker_id`` 留空）：单 Worker 历史部署兜底。

    行为保持 2026-08-09 版本：``GET /api/agents`` 拉全表，逐 agent 跑
    ``<cli> --version``，成功 heartbeat / 失败 deregister。

    已知问题：``GET /api/agents`` 当前走 ``to_public_dict()`` 不返回 ``cli_command``
    （档 A 阻断级修复 2026-08-20），所以所有 agent 都会被 ``skipped``。本 change
    不修 —— 多 Worker 部署已走新路径；旧路径用户应升级设置 ``AGENTBOARD_WORKER_ID``。
    """
    stats = _empty_stats()
    try:
        agents = client.request("GET", "/api/agents").json() or []
    except Exception as e:
        log.warning("拉取 Agent 列表失败（心跳探测跳过本轮）：%s", e)
        return stats
    for a in agents or []:
        aid = a.get("agent_id")
        cmd, model_v = _resolve_cmd_local(
            str(aid or ""), "", str(a.get("model") or ""),
            str(a.get("cli_command") or ""), "", stats)
        if not aid or not cmd or not a.get("enabled", True):
            stats["skipped"] += 1
            continue
        stats["checked"] += 1
        try:
            ok, msg = probe_cli(config, cmd, model=model_v)
            if ok:
                r = client.request("POST", f"/api/agents/{aid}/heartbeat",
                                   json={"probe_ok": True, "probe_message": msg})
                if r.status_code in (200, 201):
                    stats["online"] += 1
            else:
                r = client.request("POST", f"/api/agents/{aid}/deregister",
                                   json={"probe_message": msg})
                if r.status_code in (200, 201):
                    stats["offline"] += 1
            if r.status_code not in (200, 201):
                log.warning("Agent %s probe 结果上报失败（HTTP %s）", aid, r.status_code)
        except Exception as e:
            log.warning("Agent %s 心跳上报异常：%s", aid, e)
    if stats["checked"]:
        log.info("Agent 心跳探测（旧路径）：%s", stats)
    return stats
