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
from agentboard.features.projects.service import create_project, add_project_member


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
def owner_and_project():
    db = SessionLocal()
    try:
        user = register_user(db, username=f"owner_{uuid.uuid4().hex[:8]}", password="password123")
        p = create_project(db, name=f"Proj_{uuid.uuid4().hex[:6]}")
        add_project_member(db, project_id=p.id, user_id=user.id, role="owner")
        token = auth.make_token(user.id)
        return user, p.id, {"Authorization": f"Bearer {token}"}
    finally:
        db.close()


@pytest.fixture
def other_user_header():
    db = SessionLocal()
    try:
        user = register_user(db, username=f"other_{uuid.uuid4().hex[:8]}", password="password123")
        token = auth.make_token(user.id)
        return {"Authorization": f"Bearer {token}"}
    finally:
        db.close()


def test_preview_agent_behavior_endpoint(client, owner_and_project):
    _, project_id, owner_headers = owner_and_project
    payload = {
        "work_type": "proposal_clarify",
        "payload": {
            "preparation": {"sync_code": True, "inspect_code": True},
            "additional_instructions": "Custom test instruction."
        },
        "context_summary": "Proposal 100: Add Export to CSV."
    }
    res = client.post(
        f"/api/projects/{project_id}/agents/behavior/preview",
        json=payload,
        headers=owner_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["work_type"] == "proposal_clarify"
    assert "核心职责：需求澄清" in data["rendered_prompt"]
    assert "Custom test instruction." in data["rendered_prompt"]


def test_project_behavior_crud_and_authorization(client, owner_and_project, other_user_header):
    _, project_id, owner_headers = owner_and_project

    # 1. 权限拦截：非项目成员尝试读取/修改行为配置应被 403 拒绝
    unauth_get = client.get(f"/api/projects/{project_id}/behavior", headers=other_user_header)
    assert unauth_get.status_code == 403

    unauth_put = client.put(
        f"/api/projects/{project_id}/behavior",
        json={"preparation": {"sync_code": False}},
        headers=other_user_header,
    )
    assert unauth_put.status_code == 403

    # 2. 项目 Owner 正常读取
    res = client.get(f"/api/projects/{project_id}/behavior?work_type=implementation", headers=owner_headers)
    assert res.status_code == 200
    assert res.json()["preset"] == "agentboard-default"
    assert res.json()["preparation"]["sync_code"] is True

    # 3. 项目 Owner 正常更新
    put_body = {
        "preparation": {"sync_code": False, "checkout_branch": False, "read_documents": True, "load_memory": True, "inspect_code": True},
        "collaboration": {"read_comments": True, "leave_summary": True, "reply_to_review": True},
        "learning": {"accepted_correction": True, "judgment_reversal": True, "qa_defect": True},
        "document_sources": [{"type": "project_documents"}],
        "additional_instructions": "Follow PEP8 style guide."
    }
    put_res = client.put(f"/api/projects/{project_id}/behavior", json=put_body, headers=owner_headers)
    assert put_res.status_code == 200

    # 4. 再次获取验证生效
    get_res = client.get(f"/api/projects/{project_id}/behavior?work_type=implementation", headers=owner_headers)
    assert get_res.status_code == 200
    assert get_res.json()["preparation"]["sync_code"] is False
    assert get_res.json()["additional_instructions"] == "Follow PEP8 style guide."

    # 5. 重置项目行为
    del_res = client.delete(f"/api/projects/{project_id}/behavior", headers=owner_headers)
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] is True


def test_learnings_endpoints_and_authorization(client, owner_and_project, other_user_header):
    _, project_id, owner_headers = owner_and_project

    # 1. 未授权用户查询拦截
    unauth_get = client.get(f"/api/projects/{project_id}/learnings", headers=other_user_header)
    assert unauth_get.status_code == 403

    # 2. 授权成员查询
    res = client.get(f"/api/projects/{project_id}/learnings", headers=owner_headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    # 3. 录入经验
    post_body = {
        "category": "accepted_review_feedback",
        "summary": "Check foreign keys before insert",
        "lesson": "Ensure parent record exists in sqlite",
        "work_type": "dev",
        "tags": ["database", "foreign_key"]
    }
    post_res = client.post(f"/api/projects/{project_id}/learnings", json=post_body, headers=owner_headers)
    assert post_res.status_code == 201
    assert post_res.json()["status"] == "ok"

    # 4. 查询验证
    list_res = client.get(f"/api/projects/{project_id}/learnings", headers=owner_headers)
    assert len(list_res.json()) >= 1
    assert list_res.json()[0]["summary"] == "Check foreign keys before insert"