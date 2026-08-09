"""Epic 122 S1（多 Agent 协作闭环 · M1）：Agent 注册表 + Story 评审闭环回归护栏。

覆盖（对应 plan #52 WBS 步骤 1-3 验收）：
1. service.register_agent：注册 + 幂等更新 + 非法 roles JSON；
2. list_agents：online/role 过滤；
3. agent_heartbeat / agent_deregister：在线态维护 + 归属校验；
4. assign_reviewer：无在线 reviewer 拒绝；随机指派（reviewer_id + pending_review）；幂等；CAS 并发恰一赢家；
5. review_story：非 reviewer 拒绝；未处 pending_review 拒绝；approve → ready + 评论落库；reject → round+1 停留 pending_review；round 达上限 → blocked；缺失评论拒绝；
6. update_story：允许 pending_review/ready，拒绝非法状态（Task/Epic 共用枚举不受污染）；
7. API 直调：register/heartbeat/deregister/list、assign-reviewer、review（Bearer token 认证）。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic122_agent_review.py -q
"""
import json
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import api, auth, service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402

init_db()  # 跑完整 alembic 迁移链（含 o5p6q7r8s9t0）


def _seed():
    """1 项目 + 2 个成员用户（author/reviewer）各注册 reviewer/developer Agent。"""
    with SessionLocal() as s:
        p = service.create_project(s, name="AgentReview P")
        author = service.register_user(s, username="ep122-author", password="password123")
        reviewer = service.register_user(s, username="ep122-reviewer", password="password123")
        outsider = service.register_user(s, username="ep122-outsider", password="password123")
        service.add_project_member(s, project_id=p.id, user_id=author.id, role="member")
        service.add_project_member(s, project_id=p.id, user_id=reviewer.id, role="member")
        service.register_agent(
            s, agent_id="wb-reviewer-1", name="ReviewerBot",
            roles='["reviewer"]', capabilities='["backend"]', user_id=reviewer.id,
        )
        service.register_agent(
            s, agent_id="wb-dev-1", name="DevBot",
            roles='["developer"]', user_id=author.id,
        )
        service.agent_heartbeat(s, "wb-reviewer-1", user_id=reviewer.id)
        s.commit()
        return p.id, author.id, reviewer.id, outsider.id


@pytest.fixture(scope="module")
def seeded():
    return _seed()


def _token(user_id: int) -> dict:
    return {"Authorization": f"Bearer {auth.make_token(user_id)}"}


# ---------- 1. Agent 注册表 ----------
def test_register_agent_create_and_idempotent_update(seeded):
    with SessionLocal() as s:
        a1 = service.register_agent(
            s, agent_id="wb-x-1", name="XBot", roles='["reviewer"]', user_id=seeded[2],
        )
        a1_id = a1.id
        # 幂等：同 agent_id 更新而非新建
        a2 = service.register_agent(
            s, agent_id="wb-x-1", name="XBot v2", roles='["reviewer","developer"]', user_id=seeded[2],
        )
        assert a2.id == a1_id
        assert a2.name == "XBot v2"
        assert json.loads(a2.roles) == ["reviewer", "developer"]
        s.delete(a2); s.commit()


def test_register_agent_bad_roles_json(seeded):
    with SessionLocal() as s:
        with pytest.raises(service.InvalidValue):
            service.register_agent(s, agent_id="wb-bad", name="Bad", roles="not-json")


def test_list_agents_filter_online_and_role(seeded):
    with SessionLocal() as s:
        rows = service.list_agents(s, online=True)
        assert {r.agent_id for r in rows} == {"wb-reviewer-1"}  # 仅 reviewer 在线
        rows = service.list_agents(s, role="reviewer")
        assert all("reviewer" in json.loads(r.roles) for r in rows)
        assert "wb-reviewer-1" in {r.agent_id for r in rows}
        rows = service.list_agents(s, online=True, role="developer")
        assert rows == []


def test_agent_heartbeat_and_deregister(seeded):
    with SessionLocal() as s:
        service.agent_heartbeat(s, "wb-dev-1", user_id=seeded[1])
        a = service.get_agent_by_agent_id(s, "wb-dev-1")
        assert a.online is True and a.last_heartbeat is not None
        # 归属校验：他人心跳被拒
        with pytest.raises(service.InvalidValue):
            service.agent_heartbeat(s, "wb-dev-1", user_id=seeded[2])
        # 注销下线
        service.agent_deregister(s, "wb-dev-1", user_id=seeded[1])
        a = service.get_agent_by_agent_id(s, "wb-dev-1")
        assert a.online is False
        # 恢复在线供后续用例
        service.agent_heartbeat(s, "wb-dev-1", user_id=seeded[1])
        s.commit()


# ---------- 2. Story 评审闭环（2026-08-09 已下线，评审下沉 Task 层） ----------
def test_assign_reviewer_no_candidate_rejected(seeded):
    with SessionLocal() as s:
        epic = service.create_epic(s, project_id=seeded[0], title="Epic NoCand")
        st = service.create_story(s, epic_id=epic.id, title="Story NoCand")
        # Story 级评审已下线：无论是否有候选都拒绝（评审在 Task 层进行）
        with pytest.raises(service.InvalidValue, match="评审已下线"):
            service.assign_reviewer(s, st.id)
        s.commit()


def test_assign_reviewer_assigns_and_idempotent(seeded):
    """Story 级评审已下线（2026-08-09）：assign_reviewer 一律拒绝。"""
    with SessionLocal() as s:
        epic = service.create_epic(s, project_id=seeded[0], title="Epic Assign")
        st = service.create_story(s, epic_id=epic.id, title="Story Assign")
        with pytest.raises(service.InvalidValue, match="评审已下线"):
            service.assign_reviewer(s, st.id)


def test_review_story_approve(seeded):
    """Story 级评审已下线（2026-08-09）：review_story 一律拒绝。"""
    with SessionLocal() as s:
        epic = service.create_epic(s, project_id=seeded[0], title="Epic Approve")
        st = service.create_story(s, epic_id=epic.id, title="Story Approve")
        with pytest.raises(service.InvalidValue, match="评审已下线"):
            service.review_story(s, story_id=st.id, reviewer_user_id=seeded[2],
                                 verdict="approve", comment="LGTM")


def test_review_story_reject_round_guard(seeded):
    """Story 级评审已下线（2026-08-09）：review_story 一律拒绝。"""
    with SessionLocal() as s:
        epic = service.create_epic(s, project_id=seeded[0], title="Epic Reject")
        st = service.create_story(s, epic_id=epic.id, title="Story Reject")
        with pytest.raises(service.InvalidValue, match="评审已下线"):
            service.review_story(s, story_id=st.id, reviewer_user_id=seeded[2],
                                 verdict="reject", comment="round 1")


def test_review_story_wrong_state_rejected(seeded):
    """Story 级评审已下线（2026-08-09）：review_story 一律拒绝。"""
    with SessionLocal() as s:
        epic = service.create_epic(s, project_id=seeded[0], title="Epic WrongState")
        st = service.create_story(s, epic_id=epic.id, title="Story WrongState")
        with pytest.raises(service.InvalidValue, match="评审已下线"):
            service.review_story(s, story_id=st.id, reviewer_user_id=seeded[2],
                                 verdict="approve", comment="early")


def test_update_story_accepts_confirmed_status_only(seeded):
    """Story 状态机：confirmed 合法，pending_review/ready 已下线拒绝。"""
    with SessionLocal() as s:
        epic = service.create_epic(s, project_id=seeded[0], title="Epic Update")
        st = service.create_story(s, epic_id=epic.id, title="Story Update")
        service.update_story(s, st.id, status="confirmed")
        assert s.get(service.Story, st.id).status == "confirmed"
        with pytest.raises(service.InvalidValue):
            service.update_story(s, st.id, status="pending_review")
        with pytest.raises(service.InvalidValue):
            service.update_story(s, st.id, status="ready")
        with pytest.raises(service.InvalidValue):
            service.update_story(s, st.id, status="not-a-status")


def test_task_status_unpolluted_by_story_review_statuses(seeded):
    """Story 评审态不得泄漏进 Task 状态机（共用 Status 枚举不受污染）。"""
    with SessionLocal() as s:
        epic = service.create_epic(s, project_id=seeded[0], title="Epic Pollute")
        st = service.create_story(s, epic_id=epic.id, title="Story Pollute")
        t = service.create_task(s, project_id=seeded[0], story_id=st.id, title="Task A")
        with pytest.raises(service.InvalidValue):
            service.update_task(s, t.id, status="pending_review")
        with pytest.raises(service.InvalidValue):
            service.update_task(s, t.id, status="ready")


# ---------- 3. API 直调 ----------
def _client():
    from fastapi.testclient import TestClient
    return TestClient(api.app)


def test_api_agent_register_list_heartbeat_deregister(seeded):
    client = _client()
    hdr = _token(seeded[1])
    r = client.post("/api/agents/register", json={
        "agent_id": "wb-api-1", "name": "ApiBot", "roles": '["reviewer"]',
    }, headers=hdr)
    assert r.status_code == 201
    body = r.json()
    assert body["agent_id"] == "wb-api-1" and body["roles"] == '["reviewer"]'
    assert body["user_id"] == seeded[1]
    # 幂等注册
    r2 = client.post("/api/agents/register", json={
        "agent_id": "wb-api-1", "name": "ApiBot v2",
    }, headers=hdr)
    assert r2.status_code == 201 and r2.json()["name"] == "ApiBot v2"
    # 心跳
    r3 = client.post("/api/agents/wb-api-1/heartbeat", headers=hdr)
    assert r3.status_code == 200 and r3.json()["online"] is True
    # 列表过滤
    r4 = client.get("/api/agents", params={"online": "true"})
    assert r4.status_code == 200
    assert "wb-api-1" in {x["agent_id"] for x in r4.json()}
    # 注销
    r5 = client.post("/api/agents/wb-api-1/deregister", headers=hdr)
    assert r5.status_code == 200 and r5.json()["online"] is False
    # 404
    assert client.post("/api/agents/nope/heartbeat", headers=hdr).status_code == 404


def test_api_assign_reviewer_and_review_flow(seeded):
    """Story 级评审已下线（2026-08-09）：assign-reviewer / review 端点返回 422。"""
    client = _client()
    auth_hdr = _token(seeded[1])
    reviewer_hdr = _token(seeded[2])
    r = client.post(f"/api/projects/{seeded[0]}/epics", json={
        "title": "Epic ApiFlow", "description": "",
    }, headers=auth_hdr)
    epic_id = r.json()["id"]
    r = client.post(f"/api/epics/{epic_id}/stories", json={
        "title": "Story ApiFlow", "description": "",
    }, headers=auth_hdr)
    sid = r.json()["id"]
    # assign-reviewer → 422（评审已下线）
    r = client.post(f"/api/stories/{sid}/assign-reviewer", headers=auth_hdr)
    assert r.status_code == 422
    assert "评审已下线" in r.json().get("detail", "")
    # review → 422（评审已下线）
    r = client.post(f"/api/stories/{sid}/review", json={
        "verdict": "approve", "comment": "flow",
    }, headers=reviewer_hdr)
    assert r.status_code == 422
    # confirm 端点正常（新闸门）
    r = client.post(f"/api/stories/{sid}/confirm", headers=auth_hdr)
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"


def test_api_list_stories_global_bad_status(seeded):
    client = _client()
    r = client.get("/api/stories", params={"status": "bogus"})
    assert r.status_code == 422
