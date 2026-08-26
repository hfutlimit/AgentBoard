"""TicketHandler：Proposal → Ticket 转化域（Epic 123 Step 2）。

职责：pending 转换请求的发现 → 上下文（提案重放 + 工单指令）→
agent 决策（ticket_created / fail）落库。对应原 ``worker.py`` 中
``fetch_ticket_requests`` / ``build_ticket_context`` /
``_fail_ticket_request`` / ``_lookup_ticket_request`` /
``handle_ticket_request``。

注意（2026-08-12 double-claim 修复）：认领已收敛到 ``service.execute_ticket_request``
内部 CAS（pending→processing→done），Handler **不再** 预认领。
"""
from __future__ import annotations

import logging
from typing import Any

from ..config import ACTION_TICKET_CREATED, AgentDecision, AgentInvoker
from ..contract import ExecutionCommand, ExecutionResult, ExecutionStatus, WorkType
from .base import BaseWorkHandler

log = logging.getLogger("agentboard.worker.ticket")


def build_ticket_prompt(context: dict) -> str:
    """转化模式提示词：指示 agent 用 AgentBoard MCP 生成 ticket（文档 #59）。"""
    ttype = str(context.get("ticket_type") or "")
    parent_epic = context.get("parent_epic_id")
    parent_story = context.get("parent_story_id")
    project_dir = context.get("project_dir") or "(未知项目目录)"
    lines = [
        "你是需求落单助手。下面的提案已通过多轮澄清收敛（converged_spec 即最终需求规格）。",
        "",
        f"## 铁律（必须遵守）",
        f"你的工作目录已 cd 到项目根：`{project_dir}`。**创建工单前**用 MCP 工具",
        "（read_file / glob / list_dir）确认相关代码 / 接口存在并理解上下文，",
        "在输出 JSON 里加 `inspected_files` 数组（相对路径）。**禁止**编造。",
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
        '{"action":"ticket_created","inspected_files":[...]}',
        "若调用失败（工具报错），打印：",
        '{"action":"fail","error":"原因","inspected_files":[...]}',
        "不要省略参数、不要修改 type。若你所在环境没有 AgentBoard MCP 连接，",
        "直接打印 {\"action\":\"fail\",\"error\":\"缺少 AgentBoard MCP 连接\",\"inspected_files\":[]}。",
        "",
        f"## 提案 #{context.get('proposal_id')}：{context.get('title')}",
        "",
        str(context.get("content") or "(无正文)"),
    ]
    spec = str(context.get("converged_spec") or "").strip()
    if spec:
        lines += ["", "## 最终需求规格（converged_spec，工单 description 的权威来源）", spec]
    return "\n".join(lines)


class TicketHandler(BaseWorkHandler):
    """Proposal → Ticket 转化 Handler（文档 #59 四类工单）。"""

    work_type = WorkType.PROPOSAL_CONVERT
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

    def can_handle(self, work_item: dict | ExecutionCommand) -> bool:
        if isinstance(work_item, ExecutionCommand):
            return work_item.work_type == self.work_type
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
        ctx = {
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
            "project_dir": self._resolve_project_dir(proposal.get("project_id")),
        }
        # P1 修复（Review 2026-08-26）：注入 ExecutionCommand 激活 PreparedExecution 路径
        from ..contract import ExecutionCommand, WorkType
        rid = work_item.get("id") or 0
        ctx["_command"] = ExecutionCommand(
            execution_id=f"proposal_convert_{rid}",
            work_type=WorkType.PROPOSAL_CONVERT,
            entity_type="proposal",
            entity_id=int(rid),
            context=ctx,
        )
        return ctx

    def _resolve_project_dir(self, project_id: Any) -> str:
        if not project_id:
            return ""
        try:
            from ..invokers import _resolve_project_cwd
            return str(_resolve_project_cwd({"project_id": int(project_id)}, None) or "")
        except Exception:
            return ""

    def build_prompt(self, context: dict) -> str:
        """转化模式提示词（委托模块级 build_ticket_prompt）。"""
        return build_ticket_prompt(context)

    def _log_inspected(self, decision: AgentDecision, label: str) -> None:
        files = decision.inspected_files or []
        n = len(files)
        if n == 0:
            log.info("[%s] agent 未报 inspected_files（可能未读代码）", label)
            return
        sample = ", ".join(files[:5]) + (" ..." if n > 5 else "")
        log.info("[%s] agent 报告读了 %d 个文件：%s", label, n, sample)

    def handle_decision(self, work_item: dict, decision: AgentDecision,
                        context: dict) -> str:
        """落决策：ticket_created 信任成功；fail 标记失败（含单条回查兜底）。

        Review 2026-08-26 P1 #1 修复：原代码返回 "created" / "failed" / "skipped"，
        但 ``execute_command`` 里的成功判别是 ``if outcome == "success":``。
        "created" != "success"，导致正常成功路径走 ``ExecutionResult.failure``
        错误地把 ticket created 报成 failure。

        修法：统一 outcome 字符串集为 ``TicketOutcome`` enum + 跟 execute_command
        共享同一份语义。Handler 不直接返回 ExecutionResult（避免 AgentDecision
        → string outcome → ExecutionResult 多一次不必要映射），
        但保持返回值是字符串以便外部 caller（compat facade）继续 work。
        """
        from .outcome import TicketOutcome
        rid = work_item.get("id")
        self._log_inspected(decision, label="ticket")
        if decision.action == ACTION_TICKET_CREATED:
            log.info("ticket 请求 #%s agent 报告已创建（信任其 decision）", rid)
            return TicketOutcome.CREATED.value
        # agent 主动放弃（含 execute 409 竞争失败）：单条 list 回查，不盲目判失败
        # —— 若他人已完成则视为成功；仍 pending 说明 execute 未被认领（例如
        # 服务端 502 / MCP 超时），标记 failed 让前端可重试，避免无限循环拉起 agent。
        log.warning("ticket 请求 #%s agent 未创建：%s", rid, decision.error or "无原因")
        cur = self._lookup_ticket_request(work_item)
        if cur and cur.get("status") == "done":
            return TicketOutcome.CREATED.value
        if cur and cur.get("status") == "failed":
            return TicketOutcome.FAILED.value
        if cur and cur.get("status") == "pending":
            fail_result = self._fail_ticket_request(
                work_item, decision.error or "agent 未创建（请求仍 pending）",
            )
            # _fail_ticket_request 现在也返回 TicketOutcome enum value
            return fail_result
        return TicketOutcome.SKIPPED.value

    def _fail_ticket_request(self, work_item: dict, error: str) -> str:
        rid = work_item.get("id")
        r = self._request(
            "POST", f"/api/ticket-requests/{rid}/fail",
            json={"error": error[:2000]},
        )
        if r.status_code != 200:
            log.error("ticket 请求 #%s 标记 failed 失败：%s %s",
                      rid, r.status_code, r.text[:200])
        # Review 2026-08-26 P1 #1：返回 enum value，跟 handle_decision + execute_command 对齐
        from .outcome import TicketOutcome
        return TicketOutcome.FAILED.value

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

    def execute_command(self, command: ExecutionCommand, invoker: AgentInvoker) -> ExecutionResult:
        """统一执行模型实现：构建上下文 -> invoke -> 校验并返回 ExecutionResult。"""
        request = command.context if "proposal_id" in command.context else {"id": command.entity_id, "proposal_id": command.entity_id}
        rid = request.get("id", command.entity_id)
        try:
            context = self.load_context(request)
        except Exception as e:
            log.exception("ticket 请求 #%s 构建上下文失败", rid)
            result = ExecutionResult.from_exception(
                command.execution_id, e, action="fail", summary=f"构建上下文失败：{e}",
            )
            if result.status is not ExecutionStatus.FAILED_TRANSIENT:
                self._fail_ticket_request(request, f"构建上下文失败：{e}")
            return result
        try:
            decision = invoker.invoke(context)
        except Exception as e:
            log.warning("ticket 请求 #%s Agent 调用失败：%s", rid, e)
            result = ExecutionResult.from_exception(command.execution_id, e, action="fail")
            if result.status is not ExecutionStatus.FAILED_TRANSIENT:
                self._fail_ticket_request(request, str(e))
            return result
        outcome = self.handle_decision(request, decision, context)
        # Review 2026-08-26 P1 #1：比对 enum value 而非字面量字符串，
        # 避免"created" 永远 != "success" 把成功路径误判为 failure
        from .outcome import TicketOutcome
        if outcome == TicketOutcome.CREATED.value:
            return ExecutionResult.success(
                execution_id=command.execution_id,
                action=decision.action,
                summary=decision.summary or "ticket created",
                inspected_files=decision.inspected_files,
            )
        if outcome == TicketOutcome.SKIPPED.value:
            return ExecutionResult.skipped(
                execution_id=command.execution_id,
                summary=decision.summary or "ticket creation skipped",
                action="skip",
            )
        # FAILED / SKIPPED 都映射到 failure result（SKIPPED 实际上属
        # agent 主动放弃 + 兜底回查无明确结果；按 failure 处理让前端可重试）
        return ExecutionResult.failure(
            execution_id=command.execution_id,
            error=decision.error or "ticket creation failed",
            action=decision.action,
            summary=decision.summary,
        )

    # ---------- 便捷：单请求完整处理 ----------

    def handle(self, request: dict, invoker: AgentInvoker) -> str:
        """处理一个转换请求：拉起 agent 生成 ticket → 校验结果。"""
        rid = request.get("id", 0)
        cmd = ExecutionCommand(
            execution_id=f"ticket_{rid}",
            work_type=self.work_type,
            entity_type="proposal",
            entity_id=rid,
            context=request,
        )
        res = self.execute_command(cmd, invoker)
        return "success" if res.status == "success" else "failed"
