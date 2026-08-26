import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from agentboard.agent_runtime.behavior.context_builder import (
    ExecutionContextBuilder,
    execution_context_builder,
)
from agentboard.agent_runtime.contract import ExecutionCommand, WorkType


def test_context_builder_assembles_full_context():
    cmd = ExecutionCommand(
        execution_id="exec-123",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=42,
        context={
            "project_id": 3,
            "title": "Implement Redis Cache",
            "description": "Add caching layer for user profiles",
            "spec": "Use redis-py client with TTL=300s",
            "comments": [
                {"id": 1, "author_username": "alice", "content": "Make sure fallback works on connection error."}
            ],
            "documents": [
                {"id": 10, "title": "Cache Architecture", "type": "design", "content": "Redis clustering setup..."}
            ],
            "learnings": [
                {"id": 5, "category": "accepted_review_feedback", "summary": "Handle timeout gracefully", "lesson": "Always configure socket_timeout."}
            ],
        },
    )

    ctx = execution_context_builder.build(cmd)

    assert ctx.execution_id == "exec-123"
    assert ctx.work_type == WorkType.IMPLEMENTATION
    assert len(ctx.comments) == 1
    assert ctx.comments[0].author == "alice"
    assert len(ctx.documents) == 1
    assert ctx.documents[0].title == "Cache Architecture"
    assert len(ctx.learnings) == 1
    assert ctx.learnings[0].summary == "Handle timeout gracefully"
    assert "Implement Redis Cache" in ctx.raw_context_summary
    assert "Make sure fallback works" in ctx.raw_context_summary