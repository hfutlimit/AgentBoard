"""Task review and owner follow-up handlers.

Review messages are deliberately handled by the Agent runtime rather than by
the workflow dispatcher.  The dispatcher selects the reviewer/owner queue;
the handler re-reads the REST context so a redelivered message cannot make a
decision from stale event payload.
"""
from __future__ import annotations

import logging

from agentboard.core.infrastructure import messaging as mq
from ..config import (
    ACTION_REVIEW_APPROVE,
    ACTION_REVIEW_REJECT,
    ACTION_STORY_HANDLED,
    AgentDecision,
)

log = logging.getLogger("agentboard.worker.review")


def _comment_for(decision: AgentDecision) -> str:
    return (decision.comment or decision.summary or "").strip()


class ReviewHandler:
    """Run a reviewer Agent and persist its approve/reject verdict."""

    name = "review"
    valid_actions = {ACTION_REVIEW_APPROVE, ACTION_REVIEW_REJECT}

    def __init__(self, client, config):
        self.client = client
        self.config = config

    def can_handle(self, work_item: dict) -> bool:
        return work_item.get("action") == "review_task"

    def fetch(self) -> list[dict]:
        return []

    def _request(self, method: str, path: str, **kwargs):
        return self.client.request(method, path, **kwargs)

    def load_context(self, work_item: dict) -> dict:
        task_id = int(work_item["task_id"])
        response = self._request("GET", f"/api/tasks/{task_id}/review-context")
        response.raise_for_status()
        context = response.json() or {}
        context.update({
            "action": "review_task",
            "task_id": task_id,
            "review_event": work_item.get("event"),
        })
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

    def handle_requested(self, msg: "mq.WorkflowMessage", invoker) -> bool:
        try:
            context = self.load_context({
                "task_id": msg.entity_id,
                "event": msg.event,
            })
            task = context.get("task") or {}
            if task.get("status") != "in_review":
                return True
            decision = invoker.invoke(context)
            if decision.action not in self.valid_actions:
                log.warning("task#%s reviewer returned invalid action %s",
                            msg.entity_id, decision.action)
                return False
            comment = _comment_for(decision)
            if not comment:
                log.warning("task#%s reviewer returned no comment", msg.entity_id)
                return False
            response = self._request(
                "POST", f"/api/tasks/{msg.entity_id}/review",
                json={"verdict": decision.action, "comment": comment},
            )
            if response.status_code in (200, 201, 404):
                return True
            log.warning("task#%s reviewer submission failed: HTTP %s %s",
                        msg.entity_id, response.status_code, response.text[:200])
            return False
        except Exception as exc:
            log.warning("task#%s reviewer handling failed: %s", msg.entity_id, exc)
            return False


class OwnerResponseHandler:
    """Wake the exact task owner after review settlement."""

    name = "owner_response"
    valid_actions = {ACTION_STORY_HANDLED}

    def __init__(self, client, config):
        self.client = client
        self.config = config

    def can_handle(self, work_item: dict) -> bool:
        return work_item.get("action") == "owner_response"

    def fetch(self) -> list[dict]:
        return []

    def load_context(self, work_item: dict) -> dict:
        task_id = int(work_item["task_id"])
        response = self.client.request(
            "GET", f"/api/tasks/{task_id}/review-context",
        )
        response.raise_for_status()
        context = response.json() or {}
        context.update({
            "action": "owner_response",
            "task_id": task_id,
            "review_event": work_item.get("event"),
        })
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

    def handle_result(self, msg: "mq.WorkflowMessage", invoker) -> bool:
        try:
            context = self.load_context({
                "task_id": msg.entity_id,
                "event": msg.event,
            })
            target_agent = context.get("owner_agent_id")
            current_agent = getattr(self.config, "agent_id", "")
            if not target_agent or target_agent != current_agent:
                return True
            task = context.get("task") or {}
            if task.get("status") not in ("in_progress", "done", "blocked"):
                return True
            decision = invoker.invoke(context)
            if decision.action not in self.valid_actions:
                log.warning("task#%s owner returned action %s", msg.entity_id, decision.action)
            return True
        except Exception as exc:
            log.warning("task#%s owner follow-up failed: %s", msg.entity_id, exc)
            return False


__all__ = ["ReviewHandler", "OwnerResponseHandler"]
