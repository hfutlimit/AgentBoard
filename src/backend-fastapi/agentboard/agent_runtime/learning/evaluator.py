"""学习事件触发器与评估器（Task 12：LearningEvaluator）。

识别四类有价值的高质量纠错与经验事件：
1. OWNER_ACCEPTED_REVIEW: Owner 被驳回后接受修改并最终过审
2. REVIEWER_REVERSED_JUDGMENT: Reviewer 误判经申诉后改判通过（基于严格的决策序列与申诉被采纳判据）
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
        review_records: list[dict[str, Any]] | None = None,
    ) -> list[LearningTriggerEvent]:
        """从任务完成状态、历史状态机流转与评论时序中准确评估学习触发条件。"""
        triggers: list[LearningTriggerEvent] = []
        hist = history or []
        cmts = comments or []
        reviews = review_records or []

        project_id = task.get("project_id", 0)
        owner_id = task.get("assignee_id")
        reviewer_id = task.get("reviewer_id")
        task_id = task.get("id")
        work_type = task.get("type", "dev")

        # 1. 状态序列分析
        status_seq = [h.get("status") for h in hist if h.get("status")]
        has_review_rejection = False
        has_qa_rejection = False
        for idx, s in enumerate(status_seq):
            if s == "in_review" and idx + 1 < len(status_seq) and status_seq[idx + 1] == "in_progress":
                has_review_rejection = True
            elif s == "qa_review" and idx + 1 < len(status_seq) and status_seq[idx + 1] == "in_progress":
                has_qa_rejection = True

        # 2. 评论与决策时序分析 (按出现顺序)
        # 寻找最新的驳回、申诉与修复标记
        last_challenge_idx = -1
        last_acceptance_idx = -1
        last_rejection_idx = -1

        challenge_comment = ""
        acceptance_comment = ""
        rejection_comment = ""
        has_qa_keyword = False
        qa_comment_context = ""

        for idx, c in enumerate(cmts):
            content = str(c.get("content") or "")
            if any(k in content for k in ["CHALLENGED", "【申诉】", "维持原方案", "证据查明"]):
                last_challenge_idx = idx
                challenge_comment = content
            elif any(k in content for k in ["ACCEPTED", "【已修复】", "采纳评审", "按评审修改"]):
                last_acceptance_idx = idx
                acceptance_comment = content
            elif any(k in content for k in ["Reject:", "REJECT", "【驳回】", "审查不通过"]):
                last_rejection_idx = idx
                rejection_comment = content
            if any(k in content for k in ["QA_DEFECT", "【QA缺陷】", "BUG", "缺陷确认"]):
                has_qa_keyword = True
                qa_comment_context = content

        # 3. 判定是否为显式 Reversal（Reviewer 改判）
        # 条件：
        # - task 最终已完成 (done)
        # - 发生过驳回与申诉 (last_challenge_idx >= 0)
        # - 在申诉之后，没有后续的 Owner 认错修改标记 (last_acceptance_idx < last_challenge_idx)
        # - 或显式标记了 review_reversal / reviewer 承认申诉
        is_explicit_reversal = False
        if task.get("review_reversal") is True:
            is_explicit_reversal = True
        elif (
            task.get("status") == "done"
            and last_challenge_idx >= 0
            and last_challenge_idx > last_acceptance_idx
            and (not has_review_rejection or last_challenge_idx > last_rejection_idx)
        ):
            is_explicit_reversal = True

        # 4. 判定 Owner 纠错学习 (ACCEPTED_REVIEW_FEEDBACK)
        # 条件：经历驳回并在修改后过审，且最终结论并非 Reviewer 误判
        if (
            has_review_rejection
            and task.get("status") == "done"
            and not is_explicit_reversal
        ):
            triggers.append(
                LearningTriggerEvent(
                    project_id=project_id,
                    agent_id=owner_id,
                    work_type=work_type,
                    category=LearningCategory.ACCEPTED_REVIEW_FEEDBACK,
                    summary_hint=f"Task #{task_id} 经 Review 驳回并修改后通过",
                    discussion_context=acceptance_comment or rejection_comment or f"Review 指出的问题已在 Task #{task_id} 中修复并过审。",
                    source_task_id=task_id,
                    confidence=1.0,
                )
            )

        # 5. 判定 Reviewer 改判学习 (REVIEW_JUDGMENT_REVERSAL)
        # 条件：仅当明确确认为 Reviewer 误判改判时触发
        elif is_explicit_reversal and task.get("status") == "done":
            triggers.append(
                LearningTriggerEvent(
                    project_id=project_id,
                    agent_id=reviewer_id,
                    work_type=f"{work_type}_review",
                    category=LearningCategory.REVIEW_JUDGMENT_REVERSAL,
                    summary_hint=f"Task #{task_id} Reviewer 误判经申诉后改判通过",
                    discussion_context=challenge_comment or "Reviewer 初始判断被客观证据推翻并改判通过。",
                    source_task_id=task_id,
                    confidence=0.95,
                )
            )

        # 6. 多轮重试失败后恢复 (attempts >= 2 且 done 且无 review 驳回)
        attempts = int(task.get("attempts") or 1)
        if (
            attempts >= 2
            and task.get("status") == "done"
            and not has_review_rejection
            and not is_explicit_reversal
        ):
            triggers.append(
                LearningTriggerEvent(
                    project_id=project_id,
                    agent_id=owner_id,
                    work_type=work_type,
                    category=LearningCategory.EXECUTION_FAILURE_RECOVERED,
                    summary_hint=f"Task #{task_id} 经历 {attempts} 轮重试后成功恢复",
                    discussion_context=f"任务在经历多次失败后最终完成: {task.get('title')}",
                    source_task_id=task_id,
                    confidence=0.8,
                )
            )

        # 7. 判定 QA 发现缺陷并修复 (QA_DEFECT)
        if (
            (has_qa_rejection or has_qa_keyword)
            and task.get("status") == "done"
            and not is_explicit_reversal
        ):
            triggers.append(
                LearningTriggerEvent(
                    project_id=project_id,
                    agent_id=owner_id,
                    work_type=work_type,
                    category=LearningCategory.QA_DEFECT,
                    summary_hint=f"Task #{task_id} QA 发现缺陷并完成修复",
                    discussion_context=qa_comment_context or f"QA 在 Task #{task_id} 中发现的缺陷已修复过审。",
                    source_task_id=task_id,
                    confidence=0.9,
                )
            )

        return triggers


learning_evaluator = LearningEvaluator()