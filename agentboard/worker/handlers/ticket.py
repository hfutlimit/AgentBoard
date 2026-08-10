"""TicketHandler：Proposal → Ticket 转化域（Epic 123 Step 2）。

职责：pending 转换请求的发现 → CAS 认领 → 上下文（提案重放 + 工单指令）→
agent 决策（ticket_created / fail）落库。对应原 ``worker.py`` 中
``fetch_ticket_requests`` / ``claim_ticket_request`` /
``build_ticket_context`` / ``_fail_ticket_request`` / ``_lookup_ticket_request`` /
``handle_ticket_request``。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import ACTION_TICKET_CREATED, AgentDecision

log = logging.getLogger("agentboard.worker.ticket")


def build_ticket_prompt(context: dict) -> str:
    """转化模式提示词：指示 agent 用 AgentBoard MCP 生成 ticket（文档 #59）。"""
    ttype = str(context.get("ticket_type") or "")
    parent_epic = context.get("parent_epic_id")
    parent_story = context.get("parent_story_id")
    lines = [
        "你是需求落单助手。下面的提案已通过多轮澄清收敛（converged_spec 即最终需求规格）。",
        "",
        f"## 任务：把提案 #{context.get('proposal_id')} 生成为「{ttype}」类型工单",
        "",
        "请调用 **AgentBoard MCP 工具 `proposal_create_ticket`** 完成创建，参数：",
        f"- proposal_id: {context.get('proposal_id')}",
        f"- type: {ttype}",
    ]
    if parent_epic is not None:
        lines.append(f"- epic_id: {parent_epic}")
    if parent_story is not None:
        lines.append(f"- story_id: {parent_story}")
    if context.get("ticket_title"):
        lines.append(f"- title: {context.get('ticket_title')}")
    lines += [
        "",
        "## 决策协议（必须严格遵守）",
        "调用成功后，在输出的最后打印 JSON：",
        '{"action":"ticket_created"}',
        "若调用失败（工具报错），打印：",
        '{"action":"fail","error":"原因"}',
        "不要省略参数、不要修改 type。若你所在环境没有 AgentBoard MCP 连接，",
        "直接打印 {\"action\":\"fail\",\"error\":\"缺少 AgentBoard MCP 连接\"}。",
        "",
        f"## 提案 #{context.get('proposal_id')}：{context.get('title')}",
        "",
        str(context.get("content") or "(无正文)"),
    ]
    spec = str(context.get("converged_spec") or "").strip()
    if spec:
        lines += ["", "## 最终需求规格（converged_spec，工单 description 的权威来源）", spec]
    return "\n".join(lines)


class TicketHandler:
    """Proposal → Ticket 转化 Handler（文档 #59 四类工单）。"""

    name = "ticket"
    valid_actions = {ACTION_TICKET_CREATED, "fail"}

    def __init__(self, client: httpx.Client, config: Any):
        self.client = client
        self.config = config

    # ---------- HTTP 辅助 ----------

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        return self.client.request(method, path, **kw)

    def _get_json(self, path: str, **kw) -> Any:
        r = self._request("GET", path, **kw)
        r.raise_for_status()
        return r.json()

    # ---------- Handler 协议 ----------

    def can_handle(self, work_item: dict) -> bool:
        return bool(work_item.get("ticket_request_id") or work_item.get("proposal_id")
                    and work_item.get("type") in ("epic", "story", "task", "bug"))

    def fetch(self) -> list[dict]:
        """拉取待认领转换请求（status=pending）。"""
        try:
            return (
                self._get_json(
                    "/api/admin/ticket-requests/pending",
                    params={"limit": self.config.batch_size},
                ) or []
            )
        except Exception as e:
            log.warning("拉取 pending ticket 请求失败：%s", e)
            return []

    def claim(self, work_item: dict) -> bool:
        """pending → processing（CAS）。竞争失败静默跳过。"""
        rid = work_item.get("id")
        r = self._request("POST", f"/api/ticket-requests/{rid}/claim", json={})
        if r.status_code == 200:
            return True
        if r.status_code == 409:
            log.info("ticket 请求 #%s 认领竞争失败（已被其它 Worker 处理）", rid)
            return False
        log.warning("ticket 请求 #%s 认领异常：%s %s", rid, r.status_code, r.text[:200])
        return False

    def load_context(self, work_item: dict) -> dict:
        """提案全量重放 + 工单指令（语义与 MCP proposal_get 一致，多出 ticket 字段）。"""
        pid = work_item.get("proposal_id")
        proposal = self._get_json(f"/api/proposals/{pid}")
        rounds = self._get_json(f"/api/proposals/{pid}/rounds")
        history: list[dict] = []
        for r in rounds or []:
            for q in r.get("questions", []) or []:
                history.append({
                    "round": r.get("round_no"),
                    "question_id": q.get("id"),
                    "question": q.get("question"),
                    "answer": q.get("answer") or "",
                    "unsure": bool(q.get("unsure")),
                    "answered": bool(q.get("answered_at")),
                })
        return {
            "action": "create_ticket",
            "proposal_id": proposal.get("id"),
            "project_id": proposal.get("project_id"),
            "title": proposal.get("title"),
            "content": proposal.get("content") or "",
            "status": proposal.get("status"),
            "converged_spec": proposal.get("converged_spec") or "",
            "history": history,
            "ticket_request_id": work_item.get("id"),
            "ticket_type": work_item.get("type"),
            "parent_epic_id": work_item.get("parent_epic_id"),
            "parent_story_id": work_item.get("parent_story_id"),
            "ticket_title": work_item.get("title") or "",
        }

    def build_prompt(self, context: dict) -> str:
        """转化模式提示词（委托模块级 build_ticket_prompt）。"""
        return build_ticket_prompt(context)

    def handle_decision(self, work_item: dict, decision: AgentDecision,
                        context: dict) -> str:
        """落决策：ticket_created 信任成功；fail 标记失败（含单条回查兜底）。"""
        rid = work_item.get("id")
        if decision.action == ACTION_TICKET_CREATED:
            log.info("ticket 请求 #%s agent 报告已创建（信任其 decision）", rid)
            return "created"
        # agent 主动放弃（含 execute 409 竞争失败）：单条 list 回查，不盲目判失败
        # —— 若他人已完成则视为成功；否则跳过，由 reclaim-stale 超时兜底 + 用户重试。
        log.warning("ticket 请求 #%s agent 未创建：%s", rid, decision.error or "无原因")
        cur = self._lookup_ticket_request(work_item)
        if cur and cur.get("status") == "done":
            return "created"
        if cur and cur.get("status") == "failed":
            return "failed"
        return "skipped"

    def _fail_ticket_request(self, work_item: dict, error: str) -> str:
        rid = work_item.get("id")
        r = self._request(
            "POST", f"/api/ticket-requests/{rid}/fail",
            json={"error": error[:2000]},
        )
        if r.status_code != 200:
            log.error("ticket 请求 #%s 标记 failed 失败：%s %s",
                      rid, r.status_code, r.text[:200])
        return "failed"

    def _lookup_ticket_request(self, work_item: dict) -> dict | None:
        """单条 list 查 ticket request 当前状态（轻量级观测，不轮询）。"""
        pid = work_item.get("proposal_id")
        rid = work_item.get("id")
        try:
            reqs = self._get_json(f"/api/proposals/{pid}/ticket-requests") or []
            return next((r for r in reqs if r.get("id") == rid), None)
        except Exception as e:
            log.debug("ticket 请求 #%s 单次回查失败：%s", rid, e)
            return None

    # ---------- 便捷：单请求完整处理 ----------

    def handle(self, request: dict, invoker) -> str:
        """处理一个转换请求：认领 → 拉起 agent → 信任其 decision。"""
        rid = request.get("id")
        if not self.claim(request):
            return "skipped"
        try:
            context = self.load_context(request)
        except Exception as e:
            log.exception("ticket 请求 #%s 构建上下文失败", rid)
            return self._fail_ticket_request(request, f"构建上下文失败：{e}")
        try:
            decision = invoker.invoke(context)
        except Exception as e:
            log.warning("ticket 请求 #%s Agent 调用失败：%s", rid, e)
            return self._fail_ticket_request(request, str(e))
        return self.handle_decision(request, decision, context)
