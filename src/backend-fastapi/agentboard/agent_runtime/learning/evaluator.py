"""学习事件触发器与评估器（Task 12：LearningEvaluator）。

识别四类有价值的高质量纠错与经验事件：
1. OWNER_ACCEPTED_REVIEW: Owner 被驳回后接受修改并最终过审
2. REVIEWER_REVERSED_JUDGMENT: Reviewer 误判被申诉后改判通过
3. QA_DEFECT: QA 发现真实缺陷并被确认
4. REPEATED_FAILURE_RECOVERED: 多轮重试失败后最终恢复

普通一次过审任务不触发提取，以保持经验库高信噪比。
"""
from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field

from ..contract import WorkType


class LearningCategory(str, Enum):
    ACCEPTED_REVIEW_FEEDBACK = "accepted_review_feedback"
    REVIEW_JUDGMENT_REVERSAL = "review_judgment_reversal"
    QA_DEFECT = "qa_defect"
    EXECUTION_FAILURE_RECOVERED = "execution_failure"
    PROJECT_CONVENTION = "project_convention"


class LearningTriggerEvent(BaseModel):
    """学习触发事件描述。"""
    project_id: int
    agent_id: int | None = None
    work_type: WorkType | str | None = None
    category: LearningCategory
    summary_hint: str
    discussion_context: str
    source_run_id: int | None = None
    source_task_id: int | None = None
    source_review_id: int | None = None
    confidence: float = 1.0


class LearningEvaluator:
    """学习触发评估器。"""

    def evaluate_task_outcome(
        self,
        task: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        comments: list[dict[str, Any]] | None = None,
    ) -> list[LearningTriggerEvent]:
        """从任务完成状态与历史中评估是否满足学习触发条件。"""
        triggers: list[LearningTriggerEvent] = []
        hist = history or []
        cmts = comments or []
        project_id = task.get("project_id", 0)
        agent_id = task.get("assignee_id") or task.get("reviewer_id")
        task_id = task.get("id")
        work_type = task.get("type", "dev")

        # 检查是否有驳回后修改通过记录 (in_review -> in_progress -> in_review -> done)
        status_seq = [h.get("status") for h in hist if h.get("status")]
        has_review_rejection = False
        rejection_comment = ""
        acceptance_comment = ""

        for idx, s in enumerate(status_seq):
            if s == "in_review" and idx + 1 < len(status_seq) and status_seq[idx + 1] == "in_progress":
                has_review_rejection = True
                break

        # 检查评论区是否有 ACCEPTED 或 CHALLENGED
        for c in cmts:
            content = str(c.get("content") or "")
            if "ACCEPTED" in content or "【已修复】" in content or "采纳评审" in content:
                acceptance_comment = content
            elif "CHALLENGED" in content or "【申诉】" in content or "维持原方案" in content:
                rejection_comment = content

        # 1. 触发 Owner 纠错学习
        if has_review_rejection and task.get("status") == "done" and not rejection_comment:
            triggers.append(
                LearningTriggerEvent(
                    project_id=project_id,
                    agent_id=agent_id,
                    work_type=work_type,
                    category=LearningCategory.ACCEPTED_REVIEW_FEEDBACK,
                    summary_hint=f"Task #{task_id} 经 Review 驳回并修改后通过",
                    discussion_context=acceptance_comment or f"Reviewer 指出的问题已在 Task #{task_id} 中修复。",
                    source_task_id=task_id,
                    confidence=1.0,
                )
            )

        # 2. 触发 Reviewer 误判改判学习
        if rejection_comment and task.get("status") == "done":
            triggers.append(
                LearningTriggerEvent(
                    project_id=project_id,
                    agent_id=task.get("reviewer_id"),
                    work_type=f"{work_type}_review",
                    category=LearningCategory.REVIEW_JUDGMENT_REVERSAL,
                    summary_hint=f"Task #{task_id} Reviewer 误判经申诉后改判通过",
                    discussion_context=rejection_comment,
                    source_task_id=task_id,
                    confidence=0.9,
                )
            )

        # 3. 多轮重试失败后恢复 (attempts >= 2 且 done)
        attempts = int(task.get("attempts") or 1)
        if attempts >= 2 and task.get("status") == "done" and not has_review_rejection:
            triggers.append(
                LearningTriggerEvent(
                    project_id=project_id,
                    agent_id=agent_id,
                    work_type=work_type,
                    category=LearningCategory.EXECUTION_FAILURE_RECOVERED,
                    summary_hint=f"Task #{task_id} 经历 {attempts} 轮重试后成功恢复",
                    discussion_context=f"任务在经历多次失败后最终完成: {task.get('title')}",
                    source_task_id=task_id,
                    confidence=0.8,
                )
            )

        return triggers


learning_evaluator = LearningEvaluator()