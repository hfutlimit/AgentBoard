import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from agentboard.agent_runtime.learning.evaluator import LearningCategory, LearningTriggerEvent
from agentboard.agent_runtime.learning.extractor import LearningExtractor, learning_extractor


def test_learning_extractor_produces_structured_lesson():
    event = LearningTriggerEvent(
        project_id=3,
        agent_id=7,
        work_type="dev",
        category=LearningCategory.ACCEPTED_REVIEW_FEEDBACK,
        summary_hint="Task #101 composite unique index added after review",
        discussion_context="Reviewer pointed out missing unique index on (tenant_id, slug); migration added.",
        source_task_id=101,
        confidence=1.0,
    )

    lesson = learning_extractor.extract(event)
    assert lesson.category == "accepted_review_feedback"
    assert "migration" in lesson.tags or "review" in lesson.tags
    assert len(lesson.lesson) > 10
    assert lesson.confidence == 1.0