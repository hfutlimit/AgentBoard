"""Worker Portal task execution list regression coverage."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agentboard import service
from agentboard.domains.common.models import Base
from agentboard.domains.projects.models import Agent, Project
from agentboard.domains.scheduling.models import AgentRun, AgentSchedule
from agentboard.domains.work_items.models import Task
from agentboard import worker_portal


def _seed_session() -> tuple[Session, int, int]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    project = Project(name="Worker Records", key="WRK", description="")
    session.add(project)
    session.flush()
    session.add_all([
        Agent(agent_id="codex", name="Codex Worker", model="gpt-5.6"),
        Agent(agent_id="minimax", name="MiniMax Worker", model="MiniMax-M2.7"),
    ])
    task_a = Task(
        project_id=project.id, title="Review worker output",
        description="Inspect the generated patch and report findings.", spec="",
    )
    task_b = Task(
        project_id=project.id, title="Fallback spec task",
        description="", spec="Use the task spec when description is empty.",
    )
    session.add_all([task_a, task_b])
    session.flush()
    schedule_a = AgentSchedule(
        project_id=project.id, title="Codex execution", schedule_type="once",
        agent="codex", task_id=task_a.id,
    )
    schedule_b = AgentSchedule(
        project_id=project.id, title="MiniMax execution", schedule_type="once",
        agent="minimax", task_id=task_b.id,
    )
    session.add_all([schedule_a, schedule_b])
    session.flush()
    run_a = AgentRun(
        schedule_id=schedule_a.id, task_id=task_a.id, status="success",
        summary="Review complete", output="x" * 1200,
    )
    run_b = AgentRun(
        schedule_id=schedule_b.id, task_id=task_b.id, status="failed",
        error_message="model timeout", output="timeout details",
    )
    session.add_all([run_a, run_b])
    session.commit()
    return session, run_a.id, run_b.id


def test_enriched_records_filter_by_agent_and_include_useful_context():
    session, run_a, _ = _seed_session()
    try:
        result = service.list_run_records(session, agent="codex", limit=20)
    finally:
        session.close()

    assert result["total"] == 1
    item = result["items"][0]
    assert item["id"] == run_a
    assert item["agent"] == "codex"
    assert item["agent_name"] == "Codex Worker"
    assert item["model"] == "gpt-5.6"
    assert item["task_title"] == "Review worker output"
    assert item["task_description"].startswith("Inspect the generated patch")
    assert item["summary"] == "Review complete"
    assert item["has_output"] is True
    assert len(item["output_preview"]) == 1000
    assert "output" not in item


def test_enriched_records_support_status_and_text_search():
    session, _, run_b = _seed_session()
    try:
        result = service.list_run_records(
            session, status="failed", q="task spec", limit=20,
        )
    finally:
        session.close()

    assert result["total"] == 1
    item = result["items"][0]
    assert item["id"] == run_b
    assert item["agent"] == "minimax"
    assert item["model"] == "MiniMax-M2.7"
    assert item["task_description"] == "Use the task spec when description is empty."
    assert item["error_message"] == "model timeout"


def test_create_run_snapshots_agent_and_model():
    session, _, _ = _seed_session()
    try:
        schedule = session.query(AgentSchedule).filter(AgentSchedule.agent == "codex").one()
        run = service.create_run(session, schedule_id=schedule.id, task_id=schedule.task_id)
        assert run.agent == "codex"
        assert run.model == "gpt-5.6"

        # Later Agent configuration changes must not rewrite historical context.
        agent = session.query(Agent).filter(Agent.agent_id == "codex").one()
        agent.model = "future-model"
        session.commit()
        records = service.list_run_records(session, agent="codex", limit=10)
        created = next(item for item in records["items"] if item["id"] == run.id)
        assert created["model"] == "gpt-5.6"
    finally:
        session.close()


def test_worker_portal_proxies_execution_filters_and_full_output(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_get(self, path: str, **kwargs):
        calls.append((path, kwargs))
        if path == "/api/runs/7":
            return {"id": 7, "status": "success", "output": "full output"}
        return {"items": [], "total": 0}

    monkeypatch.setattr(worker_portal.AgentBoardProxy, "get", fake_get)
    client = TestClient(worker_portal.create_app("http://server", "test-token"))

    response = client.get(
        "/api/executions?agent=codex&status=success&q=review&limit=25&offset=5"
    )
    assert response.status_code == 200
    assert calls[0] == ("/api/runs", {"params": {
        "agent": "codex", "status": "success", "q": "review",
        "limit": 25, "offset": 5,
    }})

    detail = client.get("/api/executions/7")
    assert detail.status_code == 200
    assert detail.json()["output"] == "full output"
    assert calls[1][0] == "/api/runs/7"
