"""Story 434 / 2026-09-09：ghost worker_work 行的行为契约。

ghost 行 = entity_type/entity_id 无法解析出真实 Proposal/Task 的队列行（历史数据，
早于 ``Offer`` 强制引用完整性之前写入）。它们不该再被发布或被 claim，也不该以无信息
的 500 结束。本文件钉住四件事：

1. claim 一个 ghost 行 → 409 且 detail 带机器可读 reason=ghost_work_row（修复前是
   用存储列重建 Offer 触发 Pydantic ValidationError，对外表现为 500）；
2. relay 的候选查询把 ghost 行排除在外（ghost_free_condition 与 GHOST_WHERE_SQL 同义）；
3. admin 清理端点默认 dry-run、删除必须显式 confirm、只删 ghost 不动合法行、并回报
   会被留下的悬空 worker_discussions；
4. 两个 admin 端点在 REQUIRE_AUTH=1 下只认 admin。
"""
from __future__ import annotations

import uuid

from agentboard.features.identity.models import User
from agentboard.features.identity.service import register_user
from agentboard.features.projects.models import Agent
from agentboard.features.scheduling.worker_work import (
    GHOST_CLEANUP_CONFIRM, GHOST_SELECT_SQL, is_ghost_row)
from agentboard.features.scheduling.worker_work_models import WorkerWork, WorkerDiscussion
from conftest import auth_headers, login_token, setup_story, setup_user_project


def _task(db, project_id: int, owner_id: int) -> int:
    """Create a real Task through the service layer (status CHECKs apply)."""
    from agentboard.features.work_items.service import create_task

    story_id = setup_story(db, project_id)
    task = create_task(db, project_id=project_id, story_id=story_id, title="t",
                       type="dev", owner_user_id=owner_id, needs_human_confirmation=False)
    return task.id


def _ghost(db, project_id: int, *, entity_type: str = "", entity_id: int = 0,
           state: str = "available", kind: str = "dev") -> int:
    """Insert a queue row straight into the table, bypassing the Offer contract."""
    row = WorkerWork(work_key=f"ghost:{uuid.uuid4().hex[:12]}", project_id=project_id,
                     entity_type=entity_type, entity_id=entity_id, kind=kind,
                     iteration=0, state=state, active_slot=None, input_hash="",
                     attempt_history="[]")
    db.add(row)
    db.commit()
    return row.id


def _legit(db, project_id: int, task_id: int) -> int:
    row = WorkerWork(work_key=f"real:{uuid.uuid4().hex[:12]}", project_id=project_id,
                     entity_type="task", entity_id=task_id, kind="dev", iteration=0,
                     state="available", active_slot=None, input_hash="",
                     attempt_history="[]")
    db.add(row)
    db.commit()
    return row.id


def test_claiming_a_ghost_row_is_refused_with_a_machine_readable_reason(
        client, db_session, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_WORKER_OWNED_ENABLED", "1")
    uid, pid = setup_user_project(db_session)
    work_id = _ghost(db_session, pid)
    headers = auth_headers(login_token(client, db_session, uid))
    db_session.add(Agent(agent_id="ghost-a", name="ghost-a", user_id=uid, roles="[]"))
    db_session.commit()

    response = client.post(f"/api/worker-work/{work_id}/claim", headers=headers, json={
        "project_id": pid, "kind": "dev", "worker_id": "w-1", "agent_id": "ghost-a",
        "token": uuid.uuid4().hex})

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "ghost_work_row", detail
    assert detail["entity_type"] == "" and detail["entity_id"] == 0, detail
    assert "cleanup-ghost" in detail["detail"], detail


def test_ghost_rows_are_the_only_ones_the_relay_would_skip(db_session):
    from agentboard.features.scheduling.worker_work import ghost_free_condition

    uid, pid = setup_user_project(db_session)
    task_id = _task(db_session, pid, uid)
    ghost = _ghost(db_session, pid)
    ghost_empty_type = _ghost(db_session, pid, entity_type="story", entity_id=7)
    ghost_zero_id = _ghost(db_session, pid, entity_type="task", entity_id=0)
    legit = _legit(db_session, pid, task_id)

    publishable = {row.id for row in db_session.query(WorkerWork).filter(
        ghost_free_condition(), WorkerWork.state == "available").all()}

    assert publishable == {legit}, publishable
    for work_id in (ghost, ghost_empty_type, ghost_zero_id):
        assert is_ghost_row(db_session.get(WorkerWork, work_id))
    # The shared SQL predicate and the ORM predicate must agree, otherwise the
    # admin cleanup and the relay filter drift apart again.
    from sqlalchemy import text

    sql_matched = {int(r["id"]) for r in db_session.execute(
        text(GHOST_SELECT_SQL), {"limit": 100}).mappings().all()}
    assert sql_matched == {ghost, ghost_empty_type, ghost_zero_id}, sql_matched


def test_cleanup_endpoint_defaults_to_dry_run_and_requires_confirm(client, db_session):
    uid, pid = setup_user_project(db_session)
    task_id = _task(db_session, pid, uid)
    ghost = _ghost(db_session, pid)
    legit = _legit(db_session, pid, task_id)
    db_session.add(WorkerDiscussion(project_id=pid, task_id=task_id, source_work_id=ghost,
                                     review_kind="dev_review", owner_agent="a",
                                     reviewer_agent="b", review_round=0, messages="[]"))
    db_session.commit()
    headers = auth_headers(login_token(client, db_session, uid))

    default = client.post("/api/admin/worker-work/cleanup-ghost", headers=headers, json={})
    assert default.status_code == 200, default.text
    body = default.json()
    assert body["dry_run"] is True and body["matched"] == 1 and body["deleted"] == 0, body
    assert body["linked_discussions"] == 1, body
    assert body["ids"] == [ghost], body
    assert db_session.get(WorkerWork, ghost) is not None  # dry run must not delete

    unconfirmed = client.post("/api/admin/worker-work/cleanup-ghost", headers=headers,
                               json={"dry_run": False})
    assert unconfirmed.status_code == 422, unconfirmed.text
    assert GHOST_CLEANUP_CONFIRM in unconfirmed.text
    assert db_session.get(WorkerWork, ghost) is not None

    applied = client.post("/api/admin/worker-work/cleanup-ghost", headers=headers,
                          json={"dry_run": False, "confirm": GHOST_CLEANUP_CONFIRM})
    assert applied.status_code == 200, applied.text
    assert applied.json()["deleted"] == 1, applied.text
    db_session.expire_all()
    assert db_session.get(WorkerWork, ghost) is None
    assert db_session.get(WorkerWork, legit) is not None  # valid rows are untouched


def test_cleanup_endpoint_limit_is_bounded(client, db_session):
    uid, pid = setup_user_project(db_session)
    for _ in range(3):
        _ghost(db_session, pid)
    headers = auth_headers(login_token(client, db_session, uid))

    assert client.post("/api/admin/worker-work/cleanup-ghost", headers=headers,
                       json={"limit": 2}).json()["matched"] == 2
    for bad in (0, 10001, -1):
        assert client.post("/api/admin/worker-work/cleanup-ghost", headers=headers,
                           json={"limit": bad}).status_code == 422, bad


def test_ghost_endpoints_are_admin_only_when_auth_is_required(client, db_session, monkeypatch):
    uid, pid = setup_user_project(db_session)  # first user is the bootstrap admin
    ghost = _ghost(db_session, pid)
    outsider = register_user(db_session, username="ghost-outsider", password="test1234")
    db_session.commit()
    assert db_session.get(User, uid).is_admin and not db_session.get(User, outsider.id).is_admin
    outsider_headers = auth_headers(login_token(client, db_session, outsider.id))
    admin_headers = auth_headers(login_token(client, db_session, uid))
    monkeypatch.setenv("AGENTBOARD_REQUIRE_AUTH", "1")

    # Anonymous callers are rejected even earlier by the global auth gate (401),
    # which is still fail-closed; an authenticated non-admin gets 403.
    assert client.post("/api/admin/worker-work/cleanup-ghost",
                       json={}).status_code == 401
    assert client.get("/api/admin/worker-work/inspect-ghost",
                      headers=outsider_headers).status_code == 403
    assert client.post("/api/admin/worker-work/cleanup-ghost", headers=outsider_headers,
                       json={"dry_run": False, "confirm": GHOST_CLEANUP_CONFIRM}
                       ).status_code == 403
    assert db_session.get(WorkerWork, ghost) is not None  # refused before any delete

    allowed = client.get("/api/admin/worker-work/inspect-ghost", headers=admin_headers)
    assert allowed.status_code == 200, allowed.text
    rows = allowed.json()["rows"]
    assert rows and rows[0]["is_ghost"] is True, rows
    # The raw columns are the point of this endpoint: an empty-string type shows
    # up as len 0 / empty hex, which is how you tell '' from NULL when diagnosing.
    assert rows[0]["entity_type_len"] == 0 and rows[0]["entity_type_hex"] == "", rows

    cleaned = client.post("/api/admin/worker-work/cleanup-ghost", headers=admin_headers,
                          json={"dry_run": False, "confirm": GHOST_CLEANUP_CONFIRM})
    assert cleaned.status_code == 200 and cleaned.json()["deleted"] == 1, cleaned.text
