"""学习事件触发器与评估器（Task 12：LearningEvaluator）。

识别四类有价值的高质量纠错与经验事件：
1. OWNER_ACCEPTED_REVIEW: Owner 被驳回后接受修改并最终过审
2. REVIEWER_REVERSED_JUDGMENT: Reviewer 误判经申诉后改判通过（基于严格的 review_records 决策序列与 Reviewer 亲自确认的撤销/采纳申诉判据）
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
        """从任务完成状态、结构化 review_records 与历史流转中严格评估学习触发条件。"""
        triggers: list[LearningTriggerEvent] = []
        hist = history or []
        cmts = comments or []
        raw_reviews = review_records or []

        project_id = task.get("project_id", 0)
        owner_id = task.get("assignee_id")
        reviewer_id = task.get("reviewer_id")
        task_id = task.get("id")
        work_type = task.get("type", "dev")

        # 1. 结构化 review_records 决策流转分析 (保证按时序稳定排序)
        has_review_rejection = False
        is_explicit_reversal = False
        latest_review_summary = ""

        if raw_reviews:
            # 严格按照 (created_at, id, attempt) 排序以保证真实流转时序
            sorted_reviews = sorted(
                raw_reviews,
                key=lambda r: (str(r.get("created_at") or ""), int(r.get("id") or 0), int(r.get("attempt") or 0)),
            )

            prev_decision = None
            for r in sorted_reviews:
                decision = str(r.get("decision") or "").lower()
                resolution = str(r.get("resolution") or "").lower()
                is_rev = bool(r.get("is_reversal") or r.get("reversal"))

                if decision in ["reject", "rejected", "changes_requested"]:
                    has_review_rejection = True
                    prev_decision = "reject"
                    latest_review_summary = str(r.get("comment") or r.get("reason") or "")
                elif decision in ["approve", "approved"]:
                    if prev_decision == "reject":
                        # 仅当上一轮为 reject 且当前为 approve 时评估流转性质
                        if is_rev or resolution in [
                            "challenge_accepted",
                            "owner_challenge_accepted",
                            "evidence_accepted",
                            "reversal",
                        ]:
                            is_explicit_reversal = True
                        elif resolution in ["code_fixed", "rework_verified", "changes_verified"]:
                            is_explicit_reversal = False
                            has_review_rejection = True
                    # 关键状态流转闭合：当前轮决议已成为 approve
                    prev_decision = "approve"

        # 2. 状态序列分析（若无 review_records，辅助分析）
        status_seq = [h.get("status") for h in hist if h.get("status")]
        for idx, s in enumerate(status_seq):
            if s == "in_review" and idx + 1 < len(status_seq) and status_seq[idx + 1] == "in_progress":
                has_review_rejection = True
                break

        # 3. 评论区显式判据分析（严格核实说话者身份，防止 Owner 申诉评论被误认）
        challenge_comment = ""
        acceptance_comment = ""
        rejection_comment = ""
        reviewer_reversed_comment = ""

        for c in cmts:
            content = str(c.get("content") or "")
            author = str(c.get("author") or c.get("author_username") or "").lower()
            author_role = str(c.get("author_role") or c.get("role") or "").lower()
            author_agent_id = c.get("author_agent_id") or c.get("agent_id")

            # 严格判断是否为 Reviewer 所发评论
            is_reviewer_author = False
            if reviewer_id is not None:
                if author_agent_id == reviewer_id or c.get("reviewer_id") == reviewer_id:
                    is_reviewer_author = True
                elif author in ["reviewer", f"agent_{reviewer_id}", f"reviewer_{reviewer_id}"]:
                    is_reviewer_author = True
                elif author_role in ["reviewer", "qa_reviewer", "review"]:
                    is_reviewer_author = True
            else:
                if author_role in ["reviewer", "qa_reviewer", "review"] or "reviewer" in author:
                    is_reviewer_author = True

            # 必须由 Reviewer 本人明确发出采纳申诉/撤销驳回标记，才认作改判
            if is_reviewer_author and any(
                k in content
                for k in ["【采纳申诉】", "challenge accepted", "reversal confirmed", "申诉证据查明属实", "核对无误，撤销驳回", "撤销驳回"]
            ):
                reviewer_reversed_comment = content
                is_explicit_reversal = True
            elif any(k in content for k in ["CHALLENGED", "【申诉】", "维持原方案"]):
                challenge_comment = content
            elif any(k in content for k in ["ACCEPTED", "【已修复】", "采纳评审", "按评审修改"]):
                acceptance_comment = content
            elif any(k in content for k in ["Reject:", "REJECT", "【驳回】"]):
                rejection_comment = content

        # 如果 task 显式设置了 review_reversal 标记
        if task.get("review_reversal") is True:
            is_explicit_reversal = True

        # 4. 判定 Owner 纠错学习 (ACCEPTED_REVIEW_FEEDBACK)
        # 条件：发生过驳回，最终过审，且不是 Reviewer 误判撤销
        if has_review_rejection and task.get("status") == "done" and not is_explicit_reversal:
            triggers.append(
                LearningTriggerEvent(
                    project_id=project_id,
                    agent_id=owner_id,
                    work_type=work_type,
                    category=LearningCategory.ACCEPTED_REVIEW_FEEDBACK,
                    summary_hint=f"Task #{task_id} 经 Review 驳回并修改后通过",
                    discussion_context=acceptance_comment
                    or latest_review_summary
                    or rejection_comment
                    or f"Review 指出的问题已在 Task #{task_id} 中修复并过审。",
                    source_task_id=task_id,
                    confidence=1.0,
                )
            )

        # 5. 判定 Reviewer 误判改判学习 (REVIEW_JUDGMENT_REVERSAL)
        # 条件：仅当通过 review_records 决策序列、显式标记或 Reviewer 明确撤销驳回时触发
        elif is_explicit_reversal and task.get("status") == "done":
            triggers.append(
                LearningTriggerEvent(
                    project_id=project_id,
                    agent_id=reviewer_id,
                    work_type=f"{work_type}_review",
                    category=LearningCategory.REVIEW_JUDGMENT_REVERSAL,
                    summary_hint=f"Task #{task_id} Reviewer 误判经申诉后改判通过",
                    discussion_context=reviewer_reversed_comment
                    or challenge_comment
                    or "Reviewer 初始判断被客观证据推翻并改判通过。",
                    source_task_id=task_id,
                    confidence=0.95,
                )
            )

        # 6. 多轮重试失败后恢复 (attempts >= 2 且 done)
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

        return triggers


learning_evaluator = LearningEvaluator()