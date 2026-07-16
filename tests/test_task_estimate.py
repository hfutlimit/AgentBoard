"""Epic 32 Story 49.3: Task estimate (预估工时) feature tests.

Tests creating, updating, and reading tasks with estimate via the REST API.
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
    r = c.post("/api/projects", json={"name": "Estimate Test Project"})
    assert r.status_code == 201, r.text
    project_id = r.json()["id"]

    r = c.post(f"/api/projects/{project_id}/epics", json={"title": "Test Epic"})
    assert r.status_code == 201, r.text
    epic_id = r.json()["id"]

    r = c.post(f"/api/epics/{epic_id}/stories", json={"title": "Test Story"})
    assert r.status_code == 201, r.text
    story_id = r.json()["id"]

    return project_id, story_id


def test_create_task_with_estimate(api_url):
    """Task created with estimate should persist and return the value."""
    with httpx.Client(base_url=api_url, timeout=30) as c:
        project_id, story_id = _setup_project_and_story(c)

        r = c.post(f"/api/stories/{story_id}/tasks", json={
            "project_id": project_id,
            "title": "Task with estimate",
            "estimate": 2.5,
        })
        assert r.status_code == 201, r.text
        task = r.json()
        assert task.get("estimate") == 2.5, f"Expected 2.5, got {task.get('estimate')}"

        r = c.get(f"/api/tasks/{task['id']}")
        assert r.status_code == 200
        assert r.json().get("estimate") == 2.5


def test_create_task_without_estimate(api_url):
    """Task created without estimate should have null estimate."""
    with httpx.Client(base_url=api_url, timeout=30) as c:
        project_id, story_id = _setup_project_and_story(c)

        r = c.post(f"/api/stories/{story_id}/tasks", json={
            "project_id": project_id,
            "title": "Task without estimate",
        })
        assert r.status_code == 201, r.text
        assert r.json().get("estimate") is None


def test_update_task_estimate(api_url):
    """PATCH should set, update, and clear estimate."""
    with httpx.Client(base_url=api_url, timeout=30) as c:
        project_id, story_id = _setup_project_and_story(c)

        r = c.post(f"/api/stories/{story_id}/tasks", json={
            "project_id": project_id,
            "title": "Task to update",
        })
        assert r.status_code == 201
        task_id = r.json()["id"]
        assert r.json().get("estimate") is None

        r = c.patch(f"/api/tasks/{task_id}", json={"estimate": 4})
        assert r.status_code == 200, r.text
        assert r.json().get("estimate") == 4

        r = c.patch(f"/api/tasks/{task_id}", json={"estimate": 1.5})
        assert r.status_code == 200
        assert r.json().get("estimate") == 1.5

        r = c.patch(f"/api/tasks/{task_id}", json={"estimate": None})
        assert r.status_code == 200, r.text
        assert r.json().get("estimate") is None


def test_estimate_in_task_list(api_url):
    """Task list should include estimate field."""
    with httpx.Client(base_url=api_url, timeout=30) as c:
        project_id, story_id = _setup_project_and_story(c)

        c.post(f"/api/stories/{story_id}/tasks", json={
            "project_id": project_id, "title": "Task A with estimate", "estimate": 3,
        })
        c.post(f"/api/stories/{story_id}/tasks", json={
            "project_id": project_id, "title": "Task B no estimate",
        })

        r = c.get(f"/api/stories/{story_id}/tasks")
        assert r.status_code == 200, r.text
        tasks = r.json()
        assert len(tasks) >= 2

        task_a = next(t for t in tasks if t["title"] == "Task A with estimate")
        assert task_a.get("estimate") == 3

        task_b = next(t for t in tasks if t["title"] == "Task B no estimate")
        assert task_b.get("estimate") is None


def test_estimate_with_other_fields(api_url):
    """estimate should work alongside priority and type updates."""
    with httpx.Client(base_url=api_url, timeout=30) as c:
        project_id, story_id = _setup_project_and_story(c)

        r = c.post(f"/api/stories/{story_id}/tasks", json={
            "project_id": project_id,
            "title": "Multi-field task",
            "priority": "high",
            "type": "bug",
            "estimate": 8,
        })
        assert r.status_code == 201, r.text
        task = r.json()
        assert task.get("estimate") == 8
        assert task["priority"] == "high"
        assert task["type"] == "bug"

        r = c.patch(f"/api/tasks/{task['id']}", json={
            "priority": "highest",
            "estimate": 12.5,
        })
        assert r.status_code == 200
        updated = r.json()
        assert updated.get("estimate") == 12.5
        assert updated["priority"] == "highest"
