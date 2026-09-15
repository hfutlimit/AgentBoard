"""Worker 维护职责（Epic 123 Step 2 · 拆分自原 worker.py）。

崩溃恢复（租约回收）+ agent 失败自动重投 + 自愈重投，全部下沉到服务端
端点执行（DB 为事实源），与消费主循环解耦。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("agentboard.processors.maintenance")


def reclaim_stale(client: httpx.Client, config: Any) -> list[int]:
    """把租约过期的 analyzing 提案回退 queued（原持有 Worker 已崩溃）。

    判定与回退整体下沉到服务端 ``POST /api/proposals/reclaim-stale``：
    一次批量条件 UPDATE 完成，多 Worker 同时回收天然幂等（rowcount 仲裁）。
    """
    r = client.request(
        "POST", "/api/proposals/reclaim-stale",
        json={"lease_seconds": config.lease_seconds},
    )
    if r.status_code != 200:
        log.warning("回收超租约提案失败：%s %s", r.status_code, r.text[:200])
        return []
    try:
        ids = (r.json() or {}).get("reclaimed") or []
    except Exception as e:
        log.warning("回收响应解析失败：%s", e)
        return []
    for pid in ids:
        log.warning("提案 #%s 租约超时（analyzing 停滞 >%ss），已回退 queued 重投",
                    pid, config.lease_seconds)
    return list(ids)


def reclaim_stale_stories(client: httpx.Client, config: Any) -> list[int]:
    """把租约过期的 todo Story 回退 confirmed（原持有 Worker 已崩溃）。

    判定与回退下沉到服务端 ``POST /api/stories/reclaim-stale``：批量条件
    UPDATE（claimed_by 非空 + claimed_at 超租约），多 Worker 并发回收天然幂等。
    只收 worker 认领时写入租约的行 —— 用户手工置 todo 的 Story 不受影响。
    """
    r = client.request(
        "POST", "/api/stories/reclaim-stale",
        json={"lease_seconds": config.lease_seconds},
    )
    if r.status_code != 200:
        log.warning("回收超租约 Story 失败：%s %s", r.status_code, r.text[:200])
        return []
    try:
        ids = (r.json() or {}).get("reclaimed") or []
    except Exception as e:
        log.warning("回收响应解析失败：%s", e)
        return []
    for sid in ids:
        log.warning("Story #%s 认领租约超时（todo 停滞 >%ss），已回退 confirmed 重投",
                    sid, config.lease_seconds)
    return list(ids)


def reclaim_stale_tasks(client: httpx.Client, config: Any) -> list[int]:
    """把租约过期的 in_progress Task 回退 todo（原持有 Worker 已崩溃）。

    服务端额外要求 ``updated_at < cutoff``：认领后有后续流转的行一律保护，
    in_progress 是人机共享状态，宁漏收不误收。
    """
    r = client.request(
        "POST", "/api/tasks/reclaim-stale",
        json={"lease_seconds": config.lease_seconds},
    )
    if r.status_code != 200:
        log.warning("回收超租约 Task 失败：%s %s", r.status_code, r.text[:200])
        return []
    try:
        ids = (r.json() or {}).get("reclaimed") or []
    except Exception as e:
        log.warning("回收响应解析失败：%s", e)
        return []
    for tid in ids:
        log.warning("Task #%s 认领租约超时（in_progress 停滞 >%ss），已回退 todo 重投",
                    tid, config.lease_seconds)
    return list(ids)


def reclaim_stale_ticket_requests(client: httpx.Client, config: Any) -> list[int]:
    """回收处理中超时的转换请求（processing 停滞 → failed，proposal 回退
    converged）。与提案租约回收互补；走 admin-only 端点。"""
    r = client.request(
        "POST", "/api/admin/ticket-requests/reclaim-stale",
        json={"lease_seconds": config.lease_seconds},
    )
    if r.status_code != 200:
        log.warning("回收超时转换请求失败：%s %s", r.status_code, r.text[:200])
        return []
    try:
        ids = (r.json() or {}).get("reclaimed") or []
    except Exception as e:
        log.warning("回收响应解析失败：%s", e)
        return []
    for rid in ids:
        log.warning("ticket 请求 #%s 处理超时（processing 停滞 >%ss），"
                    "已回退 proposal → converged", rid, config.lease_seconds)
    return list(ids)


def recover_failed(client: httpx.Client, config: Any) -> list[int]:
    """把「Agent 不可用」导致的 failed 提案自动回退 queued 重投。

    与 reclaim_stale（analyzing 租约超时）互补，共同构成自动闭环的自愈回路。
    """
    r = client.request(
        "POST", "/api/proposals/recover-failed",
        json={"window_seconds": 120, "max_retries": 5},
    )
    if r.status_code != 200:
        log.warning("回收 agent 失败提案异常：%s %s", r.status_code, r.text[:200])
        return []
    try:
        ids = (r.json() or {}).get("recovered") or []
    except Exception as e:
        log.warning("回收响应解析失败：%s", e)
        return []
    for pid in ids:
        log.info("提案 #%s agent 不可用导致 failed，已自动回退 queued 重投", pid)
    return list(ids)


def scan_stale_runs(client: httpx.Client, config: Any) -> dict:
    """进度停滞扫描：AgentRun 长时间没上报 progress → 告警 / 接管。

    两趟下沉到服务端 ``POST /api/scheduling/scan-stale-runs``：

    1. ``is_stale=True``（UI 告警，不动业务状态）；
    2. 超过更久仍停滞 → ``soft_takeover_run``（run 标 failed + Task 回退 todo
       + 释放 assignment），交回候选池等下一轮仲裁。

    阈值用**服务端默认**（warn 30min / takeover 60min）—— 不在 Worker 侧复制
    一份常量，避免两边漂移。

    这是 ``report_task_progress`` 的对应恢复端。之前它只有人工端点、没有任何
    周期触发器，等于安全网没挂绳（2026-09-14 外部 review P0）。

    Returns
    -------
    服务端的响应 dict，键 ``warned`` / ``taken_over`` / ``scanned_at``。
    当端点非 200 / 响应无法解析时返回 ``{"error": <code-or-message>,
    "warned": [], "taken_over": []}`` 而不是 ``{}``，让 coordinator 把
    ``error`` 落进 stats（"scan_error": 1）以便运维侧能区分"扫描正常但没
    抓到停滞 run"和"worker→server 链路本身坏了"。
    """
    r = client.request("POST", "/api/scheduling/scan-stale-runs")
    if r.status_code != 200:
        log.warning("扫描进度停滞 run 失败：%s %s", r.status_code, r.text[:200])
        return {"error": r.status_code, "warned": [], "taken_over": []}
    try:
        result = r.json() or {}
    except Exception as e:
        log.warning("进度停滞扫描响应解析失败：%s", e)
        return {"error": f"parse_error: {e}", "warned": [], "taken_over": []}
    warned = result.get("warned") or []
    taken_over = result.get("taken_over") or []
    if warned:
        log.warning("进度停滞告警：%d 个 AgentRun 标记 is_stale（尚无任务变更）",
                    len(warned))
    if taken_over:
        log.error("进度停滞接管：%d 个 AgentRun 已 soft takeover"
                  "（run→failed，Task 回退 todo，assignment 已释放）",
                  len(taken_over))
    return result


def sweep(client: httpx.Client, config: Any, fetch_work, publisher: Any) -> int:
    """自愈重投：把仍滞留在 queued/answered 的工作项重新投递。

    只投递 queued/answered（analyzing 说明有人正在干），叠加服务端 CAS，
    重复投递不会造成重复处理。
    """
    count = 0
    for proposal in fetch_work():
        pid = proposal.get("id")
        if pid and publisher.publish(pid, proposal.get("current_round") or 0,
                                     "sweep"):
            count += 1
    if count:
        log.info("自愈重投 %s 个滞留工作项", count)
    return count
