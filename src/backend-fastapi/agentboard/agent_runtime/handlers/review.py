"""Task review and owner follow-up handlers (Unified Execution Model).

Review messages are handled by the Agent runtime through unified ExecutionCommands.
The dispatcher routes by WorkType (TASK_REVIEW / TASK_RESPOND); the handler loads
full context from REST, runs the Agent, submits the structured decision, and returns ExecutionResult.
"""
from __future__ import annotations

import logging

from agentboard.core.infrastructure import messaging as mq
from ..config import (
    ACTION_REVIEW_APPROVE,
    ACTION_REVIEW_REJECT,
    ACTION_STORY_HANDLED,
    AgentDecision,
    AgentInvoker,
)
from ..contract import ExecutionCommand, ExecutionResult, WorkType
from .base import BaseWorkHandler

log = logging.getLogger("agentboard.worker.review")


def _comment_for(decision: AgentDecision) -> str:
    return (decision.comment or decision.summary or "").strip()


class ReviewHandler(BaseWorkHandler):
    """Run a reviewer Agent and persist its approve/reject verdict."""

    work_type = WorkType.TASK_REVIEW
    name = "review"
    valid_actions = {ACTION_REVIEW_APPROVE, ACTION_REVIEW_REJECT}

    def __init__(self, client, config):
        self.client = client
        self.config = config

    def can_handle(self, work_item: dict | ExecutionCommand) -> bool:
        if isinstance(work_item, ExecutionCommand):
            return work_item.work_type in (
                WorkType.TASK_REVIEW,
                WorkType.DESIGN_REVIEW,
                WorkType.IMPLEMENTATION_REVIEW,
                WorkType.QA_REVIEW,
            )
        return work_item.get("action") in ("review_task", "design_review", "implementation_review", "qa_review")

    def fetch(self) -> list[dict]:
        return []

    def _request(self, method: str, path: str, **kwargs):
        return self.client.request(method, path, **kwargs)

    def load_context(self, work_item: dict | ExecutionCommand) -> dict:
        if isinstance(work_item, ExecutionCommand):
            task_id = work_item.entity_id
            event = work_item.context.get("event")
            work_type = work_item.work_type.value
        else:
            task_id = int(work_item["task_id"])
            event = work_item.get("event")
            work_type = work_item.get("work_type", "review_task")
        response = self._request("GET", f"/api/tasks/{task_id}/review-context")
        response.raise_for_status()
        context = response.json() or {}
        # P2 修复（Review 2026-08-26）：work_type 是 authoritative；不再塞
        # action="review_task" 抹平 DESIGN_REVIEW / IMPLEMENTATION_REVIEW / QA_REVIEW。
        # action 字段只描述 Agent 输出 decision（approve/reject），不描述 execution type。
        context.update({
            "work_type": work_type,
            "task_id": task_id,
            "review_event": event,
        })
        # P1 修复（Review 2026-08-26）：注入 ExecutionCommand 激活 PreparedExecution 路径
        try:
            wt_enum = WorkType(work_type)
        except (ValueError, KeyError):
            wt_enum = WorkType.TASK_REVIEW
        context["_command"] = ExecutionCommand(
            execution_id=f"review_{task_id}",
            work_type=wt_enum,
            entity_type="task",
            entity_id=task_id,
            context=context,
        )
        return context

    def build_prompt(self, context: dict) -> str:
        task = context.get("task") or {}
        return "\n".join([
            "你是独立 Reviewer。请根据任务、Proposal 规格、代码变更信息和历史评论做审查。",
            "只输出 JSON：{\"action\":\"approve|reject\",\"comment\":\"具体且可执行的评审意见\"}。",
            "approve 只用于已满足要求；reject 必须指出需要修复的事实和建议。",
            f"Task #{task.get('id')}: {task.get('title')}",
            f"status={task.get('status')} review_round={context.get('review_round', task.get('review_round'))}",
            f"task={task}",
            f"comments={context.get('comments') or []}",
            f"proposal_spec={context.get('proposal_spec') or ''}",
        ])

    def execute_command(self, command: ExecutionCommand, invoker: AgentInvoker) -> ExecutionResult:
        task_id = command.entity_id
        try:
            context = self.load_context(command)
            task = context.get("task") or {}
            if task.get("status") != "in_review":
                return ExecutionResult.success(
                    command.execution_id,
                    action="skip",
                    summary=f"Task #{task_id} status is {task.get('status')}, skipped review",
                )
            decision = invoker.invoke(context)
            if decision.action not in self.valid_actions:
                log.warning("task#%s reviewer returned invalid action %s",
                            task_id, decision.action)
                return ExecutionResult.failure(command.execution_id, f"invalid action {decision.action}", action="fail")
            comment = _comment_for(decision)
            if not comment:
                log.warning("task#%s reviewer returned no comment", task_id)
                return ExecutionResult.failure(command.execution_id, "reviewer returned no comment", action="fail")
            response = self._request(
                "POST", f"/api/tasks/{task_id}/review",
                json={"verdict": decision.action, "comment": comment},
            )
            if response.status_code in (200, 201, 404):
                return ExecutionResult.success(
                    execution_id=command.execution_id,
                    action=decision.action,
                    summary=comment,
                    inspected_files=decision.inspected_files,
                )
            log.warning("task#%s reviewer submission failed: HTTP %s %s",
                        task_id, response.status_code, response.text[:200])
            return ExecutionResult.failure(
                execution_id=command.execution_id,
                error=f"HTTP {response.status_code}: {response.text[:200]}",
                action=decision.action,
            )
        except Exception as exc:
            log.warning("task#%s reviewer handling failed: %s", task_id, exc)
            return ExecutionResult.failure(command.execution_id, str(exc), action="fail")

    def handle_requested(self, msg: "mq.WorkflowMessage", invoker: AgentInvoker) -> bool:
        cmd = ExecutionCommand(
            execution_id=f"review_{msg.entity_id}_{getattr(msg, 'message_id', 0)}",
            work_type=self.work_type,
            entity_type="task",
            entity_id=msg.entity_id,
            context={"event": msg.event},
        )
        res = self.execute_command(cmd, invoker)
        return res.status == "success"


class OwnerResponseHandler(BaseWorkHandler):
    """Wake the exact task owner after review settlement."""

    work_type = WorkType.TASK_RESPOND
    name = "owner_response"
    valid_actions = {ACTION_STORY_HANDLED}

    def __init__(self, client, config):
        self.client = client
        self.config = config

    def can_handle(self, work_item: dict | ExecutionCommand) -> bool:
        if isinstance(work_item, ExecutionCommand):
            return work_item.work_type == self.work_type
        return work_item.get("action") == "owner_response"

    def fetch(self) -> list[dict]:
        return []

    def load_context(self, work_item: dict | ExecutionCommand) -> dict:
        if isinstance(work_item, ExecutionCommand):
            task_id = work_item.entity_id
            event = work_item.context.get("event")
        else:
            task_id = int(work_item["task_id"])
            event = work_item.get("event")
        response = self.client.request(
            "GET", f"/api/tasks/{task_id}/review-context",
        )
        response.raise_for_status()
        context = response.json() or {}
        # P2 修复：work_type authoritative；不再塞 action=owner_response 抹平
        context.update({
            "task_id": task_id,
            "review_event": event,
        })
        # P1 修复：注入 ExecutionCommand 激活 PreparedExecution 路径
        context["_command"] = ExecutionCommand(
            execution_id=f"owner_respond_{task_id}",
            work_type=WorkType.TASK_RESPOND,
            entity_type="task",
            entity_id=task_id,
            context=context,
        )
        return context

    def build_prompt(self, context: dict) -> str:
        task = context.get("task") or {}
        return "\n".join([
            "你是当前 Task 的 Owner Agent。请读取最新评审上下文并处理后续工作。",
            "若评审驳回，按评论修复并重新提交评审；若评审通过，只确认结果。",
            "最后只输出 JSON：{\"action\":\"story_handled\",\"summary\":\"本轮处理结果\"}。",
            f"Task #{task.get('id')}: {task.get('title')}",
            f"status={task.get('status')} review_event={context.get('review_event')}",
            f"comments={context.get('comments') or []}",
            f"proposal_spec={context.get('proposal_spec') or ''}",
        ])

    def execute_command(self, command: ExecutionCommand, invoker: AgentInvoker) -> ExecutionResult:
        task_id = command.entity_id
        try:
            context = self.load_context(command)
            target_agent = context.get("owner_agent_id")
            current_agent = getattr(self.config, "agent_id", "")
            if not target_agent or target_agent != current_agent:
                return ExecutionResult.success(
                    command.execution_id,
                    action="skip",
                    summary=f"Owner agent mismatch (target: {target_agent}, current: {current_agent})",
                )
            task = context.get("task") or {}
            if task.get("status") not in ("in_progress", "done", "blocked"):
                return ExecutionResult.success(
                    command.execution_id,
                    action="skip",
                    summary=f"Task #{task_id} status is {task.get('status')}, skipped owner follow-up",
                )
            decision = invoker.invoke(context)
            if decision.action not in self.valid_actions:
                log.warning("task#%s owner returned action %s", task_id, decision.action)
            return ExecutionResult.success(
                execution_id=command.execution_id,
                action=decision.action,
                summary=decision.summary or f"Task #{task_id} owner follow-up complete",
                inspected_files=decision.inspected_files,
            )
        except Exception as exc:
            log.warning("task#%s owner follow-up failed: %s", task_id, exc)
            return ExecutionResult.failure(command.execution_id, str(exc), action="fail")

    def handle_result(self, msg: "mq.WorkflowMessage", invoker: AgentInvoker) -> bool:
        cmd = ExecutionCommand(
            execution_id=f"respond_{msg.entity_id}_{getattr(msg, 'message_id', 0)}",
            work_type=self.work_type,
            entity_type="task",
            entity_id=msg.entity_id,
            context={"event": msg.event},
        )
        res = self.execute_command(cmd, invoker)
        return res.status == "success"


__all__ = ["ReviewHandler", "OwnerResponseHandler"]
