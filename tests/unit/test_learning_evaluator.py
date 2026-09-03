import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from agentboard.processors.learning.evaluator import (
    LearningCategory,
    LearningEvaluator,
    learning_evaluator,
)


def test_evaluator_triggers_on_owner_accepted_review_with_records():
    task = {
        "id": 101,
        "project_id": 3,
        "assignee_id": 7,
        "status": "done",
        "type": "dev",
        "title": "Add DB Index",
    }
    review_records = [
        {"id": 1, "decision": "reject", "reason": "Missing unique index", "created_at": "2026-08-26T10:00:00"},
        {"id": 2, "decision": "approve", "resolution": "code_fixed", "created_at": "2026-08-26T10:30:00"},
    ]

    triggers = learning_evaluator.evaluate_task_outcome(task, review_records=review_records)
    assert len(triggers) == 1
    assert triggers[0].category == LearningCategory.ACCEPTED_REVIEW_FEEDBACK
    assert triggers[0].agent_id == 7
    assert triggers[0].source_task_id == 101


def test_evaluator_triggers_on_reviewer_reversed_judgment_with_records():
    task = {
        "id": 102,
        "project_id": 3,
        "reviewer_id": 9,
        "status": "done",
        "type": "dev",
        "title": "Fix Auth Cache",
    }
    review_records = [
        {"id": 2, "decision": "approve", "resolution": "owner_challenge_accepted", "created_at": "2026-08-26T10:30:00"},
        {"id": 1, "decision": "reject", "reason": "Token expiry missing", "created_at": "2026-08-26T10:00:00"},
    ]  # Out of order input should be correctly sorted

    triggers = learning_evaluator.evaluate_task_outcome(task, review_records=review_records)
    assert len(triggers) == 1
    assert triggers[0].category == LearningCategory.REVIEW_JUDGMENT_REVERSAL
    assert triggers[0].agent_id == 9


def test_evaluator_rejects_owner_saying_challenge_accepted():
    """测试防止 Owner 说 'challenge accepted' 误触 Reviewer 学习."""
    task = {
        "id": 106,
        "project_id": 3,
        "assignee_id": 7,
        "reviewer_id": 9,
        "status": "done",
        "type": "dev",
    }
    history = [
        {"status": "in_review"},
        {"status": "in_progress"},
        {"status": "in_review"},
        {"status": "done"},
    ]
    comments = [
        {"author": "reviewer", "author_role": "reviewer", "content": "Reject: Missing unit tests."},
        {"author": "owner", "author_role": "owner", "content": "challenge accepted — I will fix and add unit tests."},
        {"author": "owner", "author_role": "owner", "content": "ACCEPTED: Added unit tests."},
        {"author": "reviewer", "author_role": "reviewer", "content": "APPROVE: Tests look good."},
    ]

    triggers = learning_evaluator.evaluate_task_outcome(task, history=history, comments=comments)
    assert len(triggers) == 1
    # 必须是 Owner 的纠错学习，绝不可因为 Owner 的发言含有 'challenge accepted' 误判为 Reviewer 误判
    assert triggers[0].category == LearningCategory.ACCEPTED_REVIEW_FEEDBACK
    assert triggers[0].agent_id == 7


def test_evaluator_handles_owner_challenge_then_concede_and_fix():
    """测试时序：Reviewer 驳回 -> Owner 误申诉 -> Reviewer 坚持 -> Owner 认错修复过审.
    必须判定为 Owner 的纠错学习 (ACCEPTED_REVIEW_FEEDBACK)，绝不可误判为 Reviewer 改判！
    """
    task = {
        "id": 104,
        "project_id": 3,
        "assignee_id": 7,
        "reviewer_id": 9,
        "status": "done",
        "type": "dev",
        "title": "Handle Decimal Precision",
    }
    history = [
        {"status": "in_review"},
        {"status": "in_progress"},
        {"status": "in_review"},
        {"status": "done"},
    ]
    comments = [
        {"author": "reviewer", "author_role": "reviewer", "content": "Reject: Float rounding error on money calculations."},
        {"author": "owner", "author_role": "owner", "content": "CHALLENGED: Float should be fine for 2 decimal places."},
        {"author": "reviewer", "author_role": "reviewer", "content": "Reject: Financial rules strictly mandate Decimal."},
        {"author": "owner", "author_role": "owner", "content": "ACCEPTED: Replaced float with decimal.Decimal, tests passing."},
    ]

    triggers = learning_evaluator.evaluate_task_outcome(task, history=history, comments=comments)
    assert len(triggers) == 1
    assert triggers[0].category == LearningCategory.ACCEPTED_REVIEW_FEEDBACK
    assert triggers[0].agent_id == 7


def test_evaluator_reviewer_reversal_via_explicit_comment():
    task = {
        "id": 105,
        "project_id": 3,
        "reviewer_id": 9,
        "status": "done",
        "type": "dev",
    }
    comments = [
        {"author": "reviewer", "author_role": "reviewer", "content": "Reject: Missing CSRF check."},
        {"author": "owner", "author_role": "owner", "content": "CHALLENGED: CSRF is handled in CsrfMiddleware."},
        {"author": "reviewer", "author_role": "reviewer", "content": "核对无误，撤销驳回，申诉证据查明属实，审批通过。"},
    ]

    triggers = learning_evaluator.evaluate_task_outcome(task, comments=comments)
    assert len(triggers) == 1
    assert triggers[0].category == LearningCategory.REVIEW_JUDGMENT_REVERSAL


def test_evaluator_does_not_trigger_on_first_try_success():
    task = {
        "id": 103,
        "project_id": 3,
        "assignee_id": 7,
        "status": "done",
        "type": "dev",
        "attempts": 1,
    }
    history = [
        {"status": "todo"},
        {"status": "in_progress"},
        {"status": "done"},
    ]
    comments = [
        {"author": "owner", "author_role": "owner", "content": "Task completed without revision."},
    ]

    triggers = learning_evaluator.evaluate_task_outcome(task, history=history, comments=comments)
    assert len(triggers) == 0