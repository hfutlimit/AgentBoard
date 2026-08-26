"""ClarifyHandler：需求澄清域（Epic 123 Step 2）。

职责：queued/answered 提案的发现 → CAS 认领 → 全量重放上下文 →
agent 决策（ask / finalize / fail）落库。对应原 ``worker.py`` 中
``fetch_work`` / ``claim`` / ``build_context`` / ``_apply_ask`` /
``_apply_finalize`` / ``mark_failed`` / ``handle`` 的澄清部分。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import (
    ACTION_ASK,
    ACTION_FINALIZE,
    AgentDecision,
    AgentInvoker,
    AgentOutputError,
    WorkerError,
)
from ..contract import ExecutionCommand, ExecutionResult, WorkType
from .base import BaseWorkHandler

log = logging.getLogger("agentboard.worker.clarify")


def build_clarify_prompt(context: dict) -> str:
    """需求澄清提示词：一次调用、一次决策、纯 JSON 收口。

    协议刻意做成「一次调用、一次决策、纯 JSON 收口」：Agent 无需记忆，
    每轮都拿到完整历史，输出严格 JSON，Worker 只负责落库。
    """
    project_dir = context.get("project_dir") or "(未知项目目录)"
    lines = [
        "你是需求澄清分析师。请阅读下面的需求提案与全部历史问答，判断需求是否已足够清晰。",
        "",
        "## 铁律（必须遵守）",
        f"你的工作目录已 cd 到项目根：`{project_dir}`。**提问前必须**用 MCP 工具",
        "（read_file / glob / list_dir）读代码、配置文件、相关文件，让问题落到具体文件、",
        "接口、字段；不要问那些看一眼代码就能得到答案的通用问题。",
        "完成阅读后，在输出 JSON 里加 `inspected_files` 数组，列出本次读过的",
        "相对路径（例：`[\"src/frontend/projects/admin-portal/src/app/dashboard/projects-list.component.ts\"]`），",
        "Worker 会记录它。**禁止**编造文件路径——只写真正读过的。",
        "",
        "## 决策协议（必须严格遵守）",
        "在输出的最后打印一个 JSON 对象，且只能是以下三种之一：",
        '1. 仍需澄清：{"action":"ask","questions":["问题1","问题2"],"summary":"本轮聚焦点","inspected_files":[...]}',
        '2. 已经收敛：{"action":"finalize","converged_spec":"最终需求规格(Markdown)","inspected_files":[...]}',
        '3. 无法处理：{"action":"fail","error":"原因","inspected_files":[...]}',
        "问题要具体、可回答，不要重复历史中已问过或已答明确的内容。",
        "",
        f"## 提案 #{context.get('proposal_id')}：{context.get('title')}",
        "",
        str(context.get("content") or "(无正文)"),
        "",
        f"## 当前轮次：{context.get('current_round', 0)}",
    ]
    history = context.get("history") or []
    if history:
        lines += ["", "## 历史问答（全量重放）"]
        for h in history:
            mark = "（用户标记不确定）" if h.get("unsure") else ""
            ans = h.get("answer") or ("(尚未作答)" if not h.get("answered") else "(空答案)")
            lines.append(f"- [第{h.get('round')}轮] Q: {h.get('question')}")
            lines.append(f"  A: {ans}{mark}")
    else:
        lines += ["", "## 历史问答", "(暂无，这是第一轮澄清)"]
    return "\n".join(lines)


class ClarifyHandler(BaseWorkHandler):
    """需求澄清 Handler：queued/answered 提案 → 澄清循环。"""

    work_type = WorkType.PROPOSAL_CLARIFY
    name = "clarify"
    valid_actions = {ACTION_ASK, ACTION_FINALIZE, "fail"}

    def __init__(self, client: httpx.Client, config: Any):
        self.client = client
        self.config = config

    # ---------- HTTP 辅助（镜像 ProposalWorker 的 _request/_get_json） ----------

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
        # 提案：无 action 字段、无 ticket_request_id / story_id 标记
        return not work_item.get("action") and not work_item.get("ticket_request_id") \
            and not work_item.get("story_id")

    def fetch(self) -> list[dict]:
        """拉取本轮待处理提案：queued（首轮）在前，answered（续轮）在后。"""
        items: list[dict] = []
        seen: set[int] = set()
        limit = max(1, self.config.batch_size)
        try:
            queued = self._get_json("/api/proposals/pending", params={"limit": limit})
        except Exception as e:
            log.warning("拉取 queued 提案失败：%s", e)
            queued = []
        for p in queued or []:
            if p.get("id") not in seen:
                seen.add(p["id"])
                items.append(p)
        remaining = limit - len(items)
        if remaining > 0:
            try:
                answered = self._get_json(
                    "/api/proposals", params={"status": "answered", "limit": remaining},
                )
            except Exception as e:
                log.warning("拉取 answered 提案失败：%s", e)
                answered = []
            for p in answered or []:
                if p.get("id") not in seen:
                    seen.add(p["id"])
                    items.append(p)
        return items

    def claim(self, work_item: dict) -> bool:
        """queued/answered → analyzing（CAS）。竞争失败返回 False 并静默跳过。"""
        pid = work_item.get("id")
        r = self._request("POST", f"/api/proposals/{pid}/claim",
                          json={"agent": self.config.agent})
        if r.status_code == 200:
            return True
        if r.status_code == 409:
            log.info("提案 #%s 认领竞争失败（已被其它 Worker 抢到或状态已变）", pid)
            return False
        if r.status_code == 404:
            log.info("提案 #%s 已不存在，跳过", pid)
            return False
        log.warning("提案 #%s 认领异常：%s %s", pid, r.status_code, r.text[:200])
        return False

    def load_context(self, work_item: dict) -> dict:
        """提案正文 + 全部历史轮次问答（全量重放，幂等）。"""
        pid = work_item.get("id")
        proposal = self._get_json(f"/api/proposals/{pid}")
        rounds = self._get_json(f"/api/proposals/{pid}/rounds")
        history: list[dict] = []
        open_questions: list[dict] = []
        for r in rounds or []:
            for q in r.get("questions", []) or []:
                answered = bool(q.get("answered_at"))
                item = {
                    "round": r.get("round_no"),
                    "question_id": q.get("id"),
                    "seq": q.get("seq"),
                    "question": q.get("question"),
                    "answer": q.get("answer") or "",
                    "unsure": bool(q.get("unsure")),
                    "answered": answered,
                }
                history.append(item)
                if not answered:
                    open_questions.append(item)
        ctx = {
            "proposal_id": proposal.get("id"),
            "project_id": proposal.get("project_id"),
            "title": proposal.get("title"),
            "content": proposal.get("content") or "",
            "status": proposal.get("status"),
            "current_round": proposal.get("current_round", 0),
            "converged_spec": proposal.get("converged_spec") or "",
            "rounds": rounds or [],
            "history": history,
            "open_questions": open_questions,
            "answered_count": sum(1 for h in history if h["answered"]),
            "total_questions": len(history),
            "max_rounds": self.config.max_rounds,
            "project_dir": self._resolve_project_dir(proposal.get("project_id")),
        }
        # P1 修复（Review 2026-08-26）：把 ExecutionCommand 塞进 context，让
        # invokers.build_prompt 走 PreparedExecution 路径（Behavior + Context + Prompt pipeline）
        from ..contract import ExecutionCommand, WorkType
        ctx["_command"] = ExecutionCommand(
            execution_id=f"proposal_clarify_{pid}",
            work_type=WorkType.PROPOSAL_CLARIFY,
            entity_type="proposal",
            entity_id=int(pid or 0),
            context=ctx,
        )
        return ctx

    def _resolve_project_dir(self, project_id: Any) -> str:
        """从与 SubprocessAgentInvoker 同一份本地映射文件查 project_dir。"""
        if not project_id:
            return ""
        try:
            from pathlib import Path
            from ..invokers import _resolve_project_cwd
            cwd = _resolve_project_cwd({"project_id": int(project_id)}, None)
            return str(cwd or "")
        except Exception:
            return ""

    def build_prompt(self, context: dict) -> str:
        """需求澄清提示词（委托模块级 build_clarify_prompt）。"""
        return build_clarify_prompt(context)

    def handle_decision(self, work_item: dict, decision: AgentDecision,
                        context: dict) -> str:
        """落决策：ask → 回写问题（awaiting）；finalize → 收敛；fail → 标记失败。"""
        pid = work_item.get("id")
        self._log_inspected(decision, label="clarify")
        if decision.action == ACTION_ASK:
            return self._apply_ask(pid, decision)
        if decision.action == ACTION_FINALIZE:
            return self._apply_finalize(pid, decision)
        # fail
        return self.mark_failed(pid, decision.error or "Agent 主动判定无法处理")

    def _log_inspected(self, decision: AgentDecision, label: str) -> None:
        """统一格式 log agent 自报看过的文件，便于审计 agent 是否真看了代码。"""
        files = decision.inspected_files or []
        n = len(files)
        if n == 0:
            log.info("[%s] agent 未报 inspected_files（可能未读代码）", label)
            return
        sample = ", ".join(files[:5]) + (" ..." if n > 5 else "")
        log.info("[%s] agent 报告读了 %d 个文件：%s", label, n, sample)

    def _apply_ask(self, proposal_id: int, decision: AgentDecision) -> str:
        """回写一轮 open questions，推进 awaiting。"""
        body: dict[str, Any] = {
            "questions": decision.questions,
            "summary": decision.summary,
            "agent": self.config.agent,
        }
        if decision.round is not None:
            body["round"] = decision.round
        r = self._request("POST", f"/api/proposals/{proposal_id}/questions", json=body)
        if r.status_code not in (200, 201):
            raise WorkerError(f"回写问题失败：{r.status_code} {r.text[:200]}")
        return "asked"

    def _apply_finalize(self, proposal_id: int, decision: AgentDecision) -> str:
        """写入 converged_spec 并推进 converged。"""
        r = self._request("PATCH", f"/api/proposals/{proposal_id}",
                          json={"converged_spec": decision.converged_spec})
        if r.status_code != 200:
            raise WorkerError(f"写入 converged_spec 失败：{r.status_code} {r.text[:200]}")
        r = self._request("PUT", f"/api/proposals/{proposal_id}/status",
                          json={"status": "converged"})
        if r.status_code != 200:
            raise WorkerError(f"推进 converged 失败：{r.status_code} {r.text[:200]}")
        return "converged"

    def mark_failed(self, proposal_id: int, error: str) -> str:
        """把提案落到 failed 并带上可读原因（failed 可回退 queued 重投）。"""
        r = self._request("PUT", f"/api/proposals/{proposal_id}/status",
                          json={"status": "failed", "error": error[:2000]})
        if r.status_code != 200:
            log.error("提案 #%s 标记 failed 失败：%s %s",
                      proposal_id, r.status_code, r.text[:200])
        return "failed"

    def execute_command(self, command: ExecutionCommand, invoker: AgentInvoker) -> ExecutionResult:
        """统一执行模型实现：构建重放上下文 -> invoke -> 决策落库 -> 返回 ExecutionResult。"""
        pid = command.entity_id
        proposal = command.context if "content" in command.context else {"id": pid}
        if not self.claim(proposal):
            return ExecutionResult.skipped(command.execution_id, "claim skipped")
        try:
            context = self.load_context(proposal)
        except Exception as e:
            log.exception("提案 #%s 构建上下文失败", pid)
            self.mark_failed(pid, f"构建重放上下文失败：{e}")
            return ExecutionResult.failure(command.execution_id, str(e), action="fail")

        current_round = int(context.get("current_round") or 0)
        try:
            decision = invoker.invoke(context)
        except Exception as e:
            log.warning("提案 #%s Agent 调用失败：%s", pid, e)
            self.mark_failed(pid, str(e))
            return ExecutionResult.failure(command.execution_id, str(e), action="fail")

        if decision.action == ACTION_ASK and current_round >= self.config.max_rounds:
            msg = (f"已达最大澄清轮次 {self.config.max_rounds}（当前第 {current_round} 轮）"
                   f"仍未收敛，转人工介入")
            log.warning("提案 #%s %s", pid, msg)
            self.mark_failed(pid, msg)
            return ExecutionResult.failure(command.execution_id, msg, action="fail")

        try:
            outcome = self.handle_decision(proposal, decision, context)
            return ExecutionResult.success(
                execution_id=command.execution_id,
                action=decision.action,
                summary=decision.summary or (decision.converged_spec if decision.action == ACTION_FINALIZE else ""),
                output={"outcome": outcome, "questions": decision.questions, "converged_spec": decision.converged_spec},
                inspected_files=decision.inspected_files,
            )
        except Exception as e:
            log.exception("提案 #%s 决策落库异常", pid)
            self.mark_failed(pid, f"决策落库异常：{e}")
            return ExecutionResult.failure(command.execution_id, str(e), action="fail")

    # ---------- 便捷：单提案完整处理（供 Worker 主循环复用） ----------

    def handle(self, proposal: dict, invoker: AgentInvoker) -> str:
        """处理一个提案（认领 + 重放 + 决策 + 落库），返回结果码。"""
        pid = proposal.get("id", 0)
        cmd = ExecutionCommand(
            execution_id=f"proposal_{pid}",
            work_type=self.work_type,
            entity_type="proposal",
            entity_id=pid,
            context=proposal,
        )
        res = self.execute_command(cmd, invoker)
        if res.status == "success":
            return res.output.get("outcome", res.action)
        if res.action == "skipped":
            return "skipped"
        return "failed"
