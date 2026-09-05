"""Regression coverage for persisting the Epic ``blocked`` state.

Run with:
    PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_epic_blocked_status.py -q
"""
import os
import sys
import tempfile

from fastapi.testclient import TestClient


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src", "backend-fastapi"))

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _module in list(sys.modules):
    if _module == "agentboard" or _module.startswith("agentboard."):
        del sys.modules[_module]

from agentboard import api, service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402


init_db()


def test_patch_epic_to_blocked_persists_and_returns_200():
    with SessionLocal() as session:
        project = service.create_project(session, name="Blocked Epic API")
        epic = service.create_epic(session, project_id=project.id, title="Blocked state")

    response = TestClient(api.app).patch(
        f"/api/epics/{epic.id}", json={"status": "blocked"}
    )

    assert response.status_code == 200
    assert response.json()["id"] == epic.id
    assert response.json()["status"] == "blocked"

    with SessionLocal() as session:
        assert service.get_epic(session, epic.id).status == "blocked"
