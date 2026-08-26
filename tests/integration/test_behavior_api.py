import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DB_PATH = "_test_behavior_api_tmp.db"
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///./{DB_PATH}"

import pytest
from fastapi.testclient import TestClient
from agentboard.api import app
from agentboard import auth
from agentboard.core.infrastructure.database import SessionLocal, engine, init_db
from agentboard.features.identity.service import register_user
from agentboard.features.projects.service import create_project


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass
    init_db()
    yield
    engine.dispose()
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_header_token():
    db = SessionLocal()
    try:
        user = register_user(db, username=f"u_{uuid.uuid4().hex[:8]}", password="password123")
        token = auth.make_token(user.id)
        return {"Authorization": f"Bearer {token}"}
    finally:
        db.close()


@pytest.fixture
def project_id():
    db = SessionLocal()
    try:
        p = create_project(db, name=f"Proj_{uuid.uuid4().hex[:6]}")
        return p.id
    finally:
        db.close()


def test_preview_agent_behavior_endpoint(client, project_id):
    payload = {
        "work_type": "proposal_clarify",
        "payload": {
            "preparation": {"sync_code": True, "inspect_code": True},
            "additional_instructions": "Custom test instruction."
        },
        "context_summary": "Proposal 100: Add Export to CSV."
    }
    res = client.post(f"/api/projects/{project_id}/agents/behavior/preview", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["work_type"] == "proposal_clarify"
    assert "核心职责：需求澄清" in data["rendered_prompt"]
    assert "Custom test instruction." in data["rendered_prompt"]
    assert "Proposal 100: Add Export to CSV." not in data["rendered_prompt"]


def test_project_behavior_crud_endpoints(client, auth_header_token, project_id):
    # 1. 获取项目生效行为 (默认)
    res = client.get(f"/api/projects/{project_id}/behavior?work_type=implementation")
    assert res.status_code == 200
    assert res.json()["preset"] == "agentboard-default"
    assert res.json()["preparation"]["sync_code"] is True

    # 2. 修改项目行为覆盖
    put_body = {
        "preparation": {"sync_code": False, "checkout_branch": False, "read_documents": True, "load_memory": True, "inspect_code": True},
        "collaboration": {"read_comments": True, "leave_summary": True, "reply_to_review": True},
        "learning": {"accepted_correction": True, "judgment_reversal": True, "qa_defect": True},
        "document_sources": [{"type": "project_documents"}],
        "additional_instructions": "Follow PEP8 style guide."
    }
    put_res = client.put(f"/api/projects/{project_id}/behavior", json=put_body, headers=auth_header_token)
    assert put_res.status_code == 200

    # 3. 再次获取，验证 sync_code 变为 False 且 additional_instructions 生效
    get_res = client.get(f"/api/projects/{project_id}/behavior?work_type=implementation")
    assert get_res.status_code == 200
    assert get_res.json()["preparation"]["sync_code"] is False
    assert get_res.json()["additional_instructions"] == "Follow PEP8 style guide."

    # 4. 重置项目行为
    del_res = client.delete(f"/api/projects/{project_id}/behavior", headers=auth_header_token)
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] is True

    # 5. 验证已恢复默认
    get_res2 = client.get(f"/api/projects/{project_id}/behavior?work_type=implementation")
    assert get_res2.json()["preparation"]["sync_code"] is True


def test_learnings_endpoints(client, auth_header_token, project_id):
    # 1. 初始查询
    res = client.get(f"/api/projects/{project_id}/learnings")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    # 2. 录入一条经验
    post_body = {
        "category": "accepted_review_feedback",
        "summary": "Check foreign keys before insert",
        "lesson": "Ensure parent record exists in sqlite",
        "work_type": "dev",
        "tags": ["database", "foreign_key"]
    }
    post_res = client.post(f"/api/projects/{project_id}/learnings", json=post_body, headers=auth_header_token)
    assert post_res.status_code == 200
    assert post_res.json()["summary"] == "Check foreign keys before insert"

    # 3. 查询验证列表包含该经验
    list_res = client.get(f"/api/projects/{project_id}/learnings")
    assert len(list_res.json()) >= 1
    assert list_res.json()[0]["summary"] == "Check foreign keys before insert"