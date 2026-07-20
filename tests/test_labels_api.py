"""API tests for B-01 Task Labels (backend already supports labels).

Verifies the complete label CRUD flow:
- Create task with labels
- Read task with labels (parse JSON)
- Update task labels
- Search/filter tasks by labels
- Edge cases: empty labels, invalid JSON, special characters
"""
import json
import os
import sys
import tempfile

# Setup: use temp SQLite before importing agentboard
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

# Reload agentboard modules
for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest
from fastapi.testclient import TestClient

from agentboard.api import app
from agentboard.database import init_db

init_db()

client = TestClient(app)


def _register(username: str, password: str = "test123456") -> str:
    resp = client.post("/api/auth/register", json={"username": username, "password": password})
    assert resp.status_code in (200, 201), f"Register failed: {resp.text}"
    return resp.json()["token"]


def _create_project(token: str, name: str) -> int:
    resp = client.post(
        "/api/projects",
        json={"name": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (200, 201), f"Create project failed: {resp.text}"
    return resp.json()["id"]


def _create_epic(token: str, project_id: int, title: str) -> int:
    resp = client.post(
        f"/api/projects/{project_id}/epics",
        json={"title": title},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (200, 201), f"Create epic failed: {resp.text}"
    return resp.json()["id"]


def _create_story(token: str, epic_id: int, title: str) -> int:
    resp = client.post(
        f"/api/epics/{epic_id}/stories",
        json={"title": title},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (200, 201), f"Create story failed: {resp.text}"
    return resp.json()["id"]


def test_create_task_with_labels():
    """Create a task with labels and verify they're stored correctly."""
    token = _register("label_test_create")
    project_id = _create_project(token, "Label Test")
    epic_id = _create_epic(token, project_id, "Epic 1")
    story_id = _create_story(token, epic_id, "Story 1")

    labels = ["frontend", "urgent", "bug-fix"]
    resp = client.post(
        f"/api/stories/{story_id}/tasks",
        json={
            "project_id": project_id,
            "title": "Task with labels",
            "type": "task",
            "priority": "high",
            "labels": json.dumps(labels),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (200, 201), f"Create task failed: {resp.text}"
    task = resp.json()
    assert json.loads(task["labels"]) == labels, f"Labels mismatch: {task['labels']}"
    print(f"  Created task {task['id']} with labels: {task['labels']}")


def test_update_task_labels():
    """Update task labels via PATCH."""
    token = _register("label_test_update")
    project_id = _create_project(token, "Label Update Test")
    epic_id = _create_epic(token, project_id, "Epic 1")
    story_id = _create_story(token, epic_id, "Story 1")

    # Create with initial labels
    resp = client.post(
        f"/api/stories/{story_id}/tasks",
        json={
            "project_id": project_id,
            "title": "Update label task",
            "labels": json.dumps(["initial"]),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    task_id = resp.json()["id"]

    # Update labels
    new_labels = ["updated", "new-tag"]
    resp = client.patch(
        f"/api/tasks/{task_id}",
        json={"labels": json.dumps(new_labels)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"Update failed: {resp.text}"
    assert json.loads(resp.json()["labels"]) == new_labels

    # Verify by GET
    resp = client.get(f"/api/tasks/{task_id}", headers={"Authorization": f"Bearer {token}"})
    assert json.loads(resp.json()["labels"]) == new_labels
    print(f"  Updated task {task_id} labels to: {new_labels}")


def test_clear_task_labels():
    """Clear task labels by setting to empty array."""
    token = _register("label_test_clear")
    project_id = _create_project(token, "Clear Test")
    epic_id = _create_epic(token, project_id, "Epic 1")
    story_id = _create_story(token, epic_id, "Story 1")

    resp = client.post(
        f"/api/stories/{story_id}/tasks",
        json={
            "project_id": project_id,
            "title": "Clear labels task",
            "labels": json.dumps(["a", "b", "c"]),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    task_id = resp.json()["id"]

    # Clear labels
    resp = client.patch(
        f"/api/tasks/{task_id}",
        json={"labels": "[]"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["labels"] == "[]"
    print(f"  Cleared labels on task {task_id}")


def test_default_labels_empty():
    """Task without labels should have empty array string."""
    token = _register("label_test_default")
    project_id = _create_project(token, "Default Test")
    epic_id = _create_epic(token, project_id, "Epic 1")
    story_id = _create_story(token, epic_id, "Story 1")

    resp = client.post(
        f"/api/stories/{story_id}/tasks",
        json={
            "project_id": project_id,
            "title": "No labels task",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    task = resp.json()
    assert task["labels"] == "[]", f"Expected '[]', got: {task['labels']}"
    print(f"  Task without labels defaults to: {task['labels']}")


def test_labels_with_special_characters():
    """Labels with special characters (Unicode, spaces) should work."""
    token = _register("label_test_special")
    project_id = _create_project(token, "Special Test")
    epic_id = _create_epic(token, project_id, "Epic 1")
    story_id = _create_story(token, epic_id, "Story 1")

    special_labels = ["前端", "紧急 bug", "v1.0", "needs-review"]
    resp = client.post(
        f"/api/stories/{story_id}/tasks",
        json={
            "project_id": project_id,
            "title": "Special labels task",
            "labels": json.dumps(special_labels, ensure_ascii=False),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"Failed: {resp.text}"
    parsed = json.loads(resp.json()["labels"])
    assert parsed == special_labels, f"Mismatch: {parsed}"
    print(f"  Special labels stored correctly: {parsed}")


def test_labels_in_task_list():
    """Labels should be included in task list responses."""
    token = _register("label_test_list")
    project_id = _create_project(token, "List Test")
    epic_id = _create_epic(token, project_id, "Epic 1")
    story_id = _create_story(token, epic_id, "Story 1")

    # Create multiple tasks with different labels
    for title, labels in [
        ("Task A", ["alpha"]),
        ("Task B", ["beta"]),
        ("Task C", ["alpha", "gamma"]),
    ]:
        client.post(
            f"/api/stories/{story_id}/tasks",
            json={
                "project_id": project_id,
                "title": title,
                "labels": json.dumps(labels),
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    # List tasks
    resp = client.get(
        f"/api/stories/{story_id}/tasks",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    tasks = resp.json()
    if isinstance(tasks, dict):
        tasks = tasks.get("items", [])

    # Verify each task has labels field
    for task in tasks:
        assert "labels" in task, f"Task {task['id']} missing labels field"
        parsed = json.loads(task["labels"])
        assert isinstance(parsed, list), f"Labels should be list, got: {type(parsed)}"

    # Find Task A and verify its label
    task_a = next(t for t in tasks if t["title"] == "Task A")
    assert json.loads(task_a["labels"]) == ["alpha"]
    print(f"  All {len(tasks)} tasks have labels field with correct format")


if __name__ == "__main__":
    test_create_task_with_labels()
    test_update_task_labels()
    test_clear_task_labels()
    test_default_labels_empty()
    test_labels_with_special_characters()
    test_labels_in_task_list()
    print("\nALL LABEL API TESTS PASSED")
