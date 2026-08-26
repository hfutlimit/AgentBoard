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
        {"author": "reviewer_agent", "content": "Missing unique index on (tenant_id, slug)."},
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
        {"status": "in_progress"},
        {"status": "done"},
    ]
    comments = [
        {"author": "reviewer_agent", "content": "Reject: Token expiry missing."},
        {"author": "owner_agent", "content": "CHALLENGED: Token expiry is handled at lines 45-50 via redis TTL."},
    ]

    triggers = learning_evaluator.evaluate_task_outcome(task, history=history, comments=comments)
    assert len(triggers) == 1
    assert triggers[0].category == LearningCategory.REVIEW_JUDGMENT_REVERSAL
    assert triggers[0].agent_id == 9


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