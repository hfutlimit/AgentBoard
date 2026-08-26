import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from agentboard.agent_runtime.learning.evaluator import (
    LearningCategory,
    LearningEvaluator,
    learning_evaluator,
)


def test_evaluator_triggers_on_owner_accepted_review():
    task = {
        "id": 101,
        "project_id": 3,
        "assignee_id": 7,
        "status": "done",
        "type": "dev",
        "title": "Add DB Index",
    }
    history = [
        {"status": "todo"},
        {"status": "in_progress"},
        {"status": "in_review"},
        {"status": "in_progress"},  # rejected once
        {"status": "in_review"},
        {"status": "done"},
    ]
    comments = [
        {"author": "reviewer_agent", "content": "Reject: Missing unique index on (tenant_id, slug)."},
        {"author": "owner_agent", "content": "ACCEPTED: Added composite unique index and migration."},
    ]

    triggers = learning_evaluator.evaluate_task_outcome(task, history=history, comments=comments)
    assert len(triggers) == 1
    assert triggers[0].category == LearningCategory.ACCEPTED_REVIEW_FEEDBACK
    assert triggers[0].agent_id == 7
    assert triggers[0].source_task_id == 101


def test_evaluator_triggers_on_reviewer_reversed_judgment():
    task = {
        "id": 102,
        "project_id": 3,
        "reviewer_id": 9,
        "status": "done",
        "type": "dev",
        "title": "Fix Auth Cache",
    }
    history = [
        {"status": "in_review"},
        {"status": "done"},
    ]
    comments = [
        {"author": "reviewer_agent", "content": "Reject: Token expiry missing."},
        {"author": "owner_agent", "content": "CHALLENGED: Token expiry is handled at lines 45-50 via redis TTL."},
        {"author": "reviewer_agent", "content": "APPROVE: Verified lines 45-50, challenge accepted."},
    ]

    triggers = learning_evaluator.evaluate_task_outcome(task, history=history, comments=comments)
    assert len(triggers) == 1
    assert triggers[0].category == LearningCategory.REVIEW_JUDGMENT_REVERSAL
    assert triggers[0].agent_id == 9


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
        {"author": "reviewer_agent", "content": "Reject: Float rounding error on money calculations."},
        {"author": "owner_agent", "content": "CHALLENGED: Float should be fine for 2 decimal places."},
        {"author": "reviewer_agent", "content": "Reject: Financial rules strictly mandate Decimal."},
        {"author": "owner_agent", "content": "ACCEPTED: Replaced float with decimal.Decimal, tests passing."},
    ]

    triggers = learning_evaluator.evaluate_task_outcome(task, history=history, comments=comments)
    assert len(triggers) == 1
    # 必须是 ACCEPTED_REVIEW_FEEDBACK，不能是 REVIEW_JUDGMENT_REVERSAL
    assert triggers[0].category == LearningCategory.ACCEPTED_REVIEW_FEEDBACK
    assert triggers[0].agent_id == 7


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
        {"author": "owner_agent", "content": "Task completed without revision."},
    ]

    triggers = learning_evaluator.evaluate_task_outcome(task, history=history, comments=comments)
    assert len(triggers) == 0