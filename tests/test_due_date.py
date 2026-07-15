"""B-03: Task due_date feature tests.

Tests creating, updating, and reading tasks with due_date via the REST API.
Uses a real uvicorn server with a temporary SQLite database.
"""
import os
import sys
import socket
import subprocess
import tempfile
import time

import httpx
import pytest

# Independent temporary database
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard.database import init_db

init_db()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc


def _wait_ready(base: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(base + "/api/meta", timeout=1)
            if r.status_code == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"API server at {base} did not start in time")


@pytest.fixture(scope="module")
def api_url():
    port = _free_port()
    proc = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _setup_project_and_story(c: httpx.Client) -> tuple[int, int]:
    """Create a project, epic, and story. Returns (project_id, story_id)."""
    r = c.post("/api/projects", json={"name": "DueDate Test Project"})
    assert r.status_code == 201, r.text
    project_id = r.json()["id"]

    r = c.post(f"/api/projects/{project_id}/epics", json={"title": "Test Epic"})
    assert r.status_code == 201, r.text
    epic_id = r.json()["id"]

    r = c.post(f"/api/epics/{epic_id}/stories", json={"title": "Test Story"})
    assert r.status_code == 201, r.text
    story_id = r.json()["id"]

    return project_id, story_id


def test_create_task_with_due_date(api_url):
    """Task created with due_date should persist and return the date."""
    with httpx.Client(base_url=api_url, timeout=30) as c:
        project_id, story_id = _setup_project_and_story(c)
        due_date = "2026-08-15"

        r = c.post(f"/api/stories/{story_id}/tasks", json={
            "project_id": project_id,
            "title": "Task with due date",
            "due_date": due_date,
        })
        assert r.status_code == 201, r.text
        task = r.json()
        assert task["due_date"] == due_date, f"Expected {due_date}, got {task.get('due_date')}"

        # Verify via GET
        r = c.get(f"/api/tasks/{task['id']}")
        assert r.status_code == 200
        assert r.json()["due_date"] == due_date


def test_create_task_without_due_date(api_url):
    """Task created without due_date should have null due_date."""
    with httpx.Client(base_url=api_url, timeout=30) as c:
        project_id, story_id = _setup_project_and_story(c)

        r = c.post(f"/api/stories/{story_id}/tasks", json={
            "project_id": project_id,
            "title": "Task without due date",
        })
        assert r.status_code == 201, r.text
        task = r.json()
        assert task["due_date"] is None


def test_update_task_due_date(api_url):
    """PATCH should update due_date."""
    with httpx.Client(base_url=api_url, timeout=30) as c:
        project_id, story_id = _setup_project_and_story(c)

        # Create without due_date
        r = c.post(f"/api/stories/{story_id}/tasks", json={
            "project_id": project_id,
            "title": "Task to update",
        })
        assert r.status_code == 201
        task_id = r.json()["id"]
        assert r.json()["due_date"] is None

        # Set due_date
        r = c.patch(f"/api/tasks/{task_id}", json={"due_date": "2026-09-01"})
        assert r.status_code == 200, r.text
        assert r.json()["due_date"] == "2026-09-01"

        # Update due_date
        r = c.patch(f"/api/tasks/{task_id}", json={"due_date": "2026-10-15"})
        assert r.status_code == 200
        assert r.json()["due_date"] == "2026-10-15"

        # Clear due_date (set to null)
        r = c.patch(f"/api/tasks/{task_id}", json={"due_date": None})
        assert r.status_code == 200, r.text
        assert r.json()["due_date"] is None


def test_due_date_in_task_list(api_url):
    """Task list should include due_date field."""
    with httpx.Client(base_url=api_url, timeout=30) as c:
        project_id, story_id = _setup_project_and_story(c)

        c.post(f"/api/stories/{story_id}/tasks", json={
            "project_id": project_id,
            "title": "Task A with due",
            "due_date": "2026-08-01",
        })
        c.post(f"/api/stories/{story_id}/tasks", json={
            "project_id": project_id,
            "title": "Task B no due",
        })

        r = c.get(f"/api/stories/{story_id}/tasks")
        assert r.status_code == 200, r.text
        tasks = r.json()
        assert len(tasks) >= 2

        task_a = next(t for t in tasks if t["title"] == "Task A with due")
        assert task_a["due_date"] == "2026-08-01"

        task_b = next(t for t in tasks if t["title"] == "Task B no due")
        assert task_b["due_date"] is None


def test_due_date_with_other_fields(api_url):
    """due_date should work alongside priority and type updates."""
    with httpx.Client(base_url=api_url, timeout=30) as c:
        project_id, story_id = _setup_project_and_story(c)

        r = c.post(f"/api/stories/{story_id}/tasks", json={
            "project_id": project_id,
            "title": "Multi-field task",
            "priority": "high",
            "type": "bug",
            "due_date": "2026-07-20",
        })
        assert r.status_code == 201, r.text
        task = r.json()
        assert task["due_date"] == "2026-07-20"
        assert task["priority"] == "high"
        assert task["type"] == "bug"

        # Update multiple fields including due_date
        r = c.patch(f"/api/tasks/{task['id']}", json={
            "priority": "highest",
            "due_date": "2026-07-25",
        })
        assert r.status_code == 200
        updated = r.json()
        assert updated["due_date"] == "2026-07-25"
        assert updated["priority"] == "highest"
