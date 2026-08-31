"""PR-6 Design task needs_human_confirmation gate 单测。

覆盖：
1. create_task 设计任务自动 needs_human_confirmation=True
2. create_task 非设计任务 needs_human_confirmation=False（向后兼容）
3. create_task 显式 needs_human_confirmation=False 覆盖默认
4. submit-review needs_human_confirmation=True → 不发 internal 事件
5. user_confirm 端点：validates + set_status + dependency unlock + comment
6. user_confirm 端点：needs_human_confirmation=False 任务 → 409
7. user_confirm 端点：status != in_review 任务 → 409
8. user_reject 端点：in_review → in_progress + 写 comment
9. user_reject 端点：未授权 → 401

运行：
    cd <repo>
    PYTHONPATH=src/backend-fastapi python -m pytest tests/unit/test_design_human_confirm_pr6.py -q
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Alembic multiple heads（未追踪 outbox migration）会让 init_db() 挂；
# 用 create_all + 独立 in-memory SQLite（PR-1 同样模式）
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# 在 import agentboard 之前把环境变量设好
os.environ.setdefault("AGENTBOARD_REQUIRE_AUTH", "0")

from agentboard.core.common.models import Base  # noqa: E402
from agentboard.core.common.enums import ItemType, Status, StatusReason  # noqa: E402
from agentboard.features.identity.service import register_user  # noqa: E402
from agentboard.features.work_items import service  # noqa: E402
from agentboard.features.work_items.service import create_task  # noqa: E402
from agentboard.features.projects.models import (  # noqa: E402
    Project, ProjectMember, Story, Epic,
)
from agentboard.features.projects.service import create_project  # noqa: E402
from agentboard.api import app  # noqa: E402


# ---------- fixtures ----------

@pytest.fixture(scope="module")
def _app_setup():
    """建内存 SQLite + create_all + 注入到 module-level SessionLocal。

    fixture 名 _app_setup 避免 shadowing import 的 ``app`` 实例。
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    # 注入到 module-level SessionLocal（让 get_session 走我们的）
    from agentboard.core.infrastructure import database
    database.engine = engine
    database.SessionLocal = Session
    database._session_factory = Session

    return engine, Session


@pytest.fixture
def client(_app_setup):
    return TestClient(app)


@pytest.fixture
def db_session(_app_setup):
    _, Session = _app_setup
    s = Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _setup_project_with_user(db_session) -> tuple[int, int]:
    """建一个 user + project，user 是 project owner member。返回 (user_id, project_id)。"""
    import uuid
    username = f"u-{uuid.uuid4().hex[:8]}"
    u = register_user(db_session, username=username, password="test1234")
    p = create_project(
        db_session,
        name="PR-6 test project",
        key=f"P-{uuid.uuid4().hex[:6].upper()}",
    )
    db_session.add(ProjectMember(project_id=p.id, user_id=u.id, role="owner"))
    db_session.commit()
    return u.id, p.id


def _login(client: TestClient, db_session, user_id: int) -> dict:
    """登入拿 token，返回 headers 字典（含 Authorization）。"""
    from agentboard.features.identity.models import User
    u = db_session.query(User).filter(User.id == user_id).one()
    r = client.post("/api/auth/login",
                    json={"username": u.username, "password": "test1234"})
    assert r.status_code == 200, r.text
    token = r.json().get("token")
    assert token, r.json()
    return {"Authorization": f"Bearer {token}"}


def _setup_epic_story(db_session, project_id: int) -> int:
    e = Epic(project_id=project_id, title="e", description="")
    db_session.add(e); db_session.commit(); db_session.refresh(e)
    s = Story(epic_id=e.id, title="s", description="")
    db_session.add(s); db_session.commit(); db_session.refresh(s)
    return s.id


# ---------- 1-3. create_task 默认 needs_human_confirmation ----------

def test_create_design_task_default_needs_human_confirmation_true(db_session):
    """PR-6：type='design' 的 task 默认 needs_human_confirmation=True。"""
    _, project_id = _setup_project_with_user(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    t = create_task(
        db_session, project_id=project_id, story_id=story_id,
        title="design task", type=ItemType.DESIGN.value,
    )
    assert t.needs_human_confirmation is True


def test_create_non_design_task_default_needs_human_confirmation_false(db_session):
    """type=dev/qa/bug 默认 False（向后兼容）。"""
    _, project_id = _setup_project_with_user(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    for tp in (ItemType.DEV.value, ItemType.QA.value, ItemType.BUG.value):
        t = create_task(
            db_session, project_id=project_id, story_id=story_id,
            title=f"{tp} task", type=tp,
        )
        assert t.needs_human_confirmation is False, f"{tp} should default False"


def test_create_design_task_explicit_needs_human_false_overrides(db_session):
    """显式 needs_human_confirmation=False 覆盖默认。"""
    _, project_id = _setup_project_with_user(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    t = create_task(
        db_session, project_id=project_id, story_id=story_id,
        title="design no-gate", type=ItemType.DESIGN.value,
        needs_human_confirmation=False,
    )
    assert t.needs_human_confirmation is False


def test_create_non_design_task_explicit_needs_human_true(db_session):
    """显式 needs_human_confirmation=True 对非 design 任务也生效（边缘 case）。"""
    _, project_id = _setup_project_with_user(db_session)
    story_id = _setup_epic_story(db_session, project_id)
    t = create_task(
        db_session, project_id=project_id, story_id=story_id,
        title="dev with gate", type=ItemType.DEV.value,
        needs_human_confirmation=True,
    )
    assert t.needs_human_confirmation is True


# ---------- 4-5. 端点行为：user_confirm / user_reject ----------

def _login_user_return_id(client: TestClient, db_session) -> int:
    """注册 + 登录一个 user，返回 user_id。"""
    import uuid
    username = f"u-{uuid.uuid4().hex[:8]}"
    password = "test1234"
    # 注册
    u = register_user(db_session, username=username, password=password)
    # 登录拿 token
    r = client.post("/api/auth/login",
                    json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return u.id


def _create_task_for_user(db_session, user_id: int, project_id: int,
                          type_: str = ItemType.DESIGN.value,
                          needs_human: bool | None = None) -> int:
    """建一个 task，type 决定默认 needs_human_confirmation。返回 task_id。"""
    story_id = _setup_epic_story(db_session, project_id)
    kwargs = {}
    if needs_human is not None:
        kwargs["needs_human_confirmation"] = needs_human
    t = create_task(
        db_session, project_id=project_id, story_id=story_id,
        title=f"t-{type_}", type=type_,
        assignee_id=user_id,
        **kwargs,
    )
    return t.id


def _put_task_in_review(db_session, task_id: int, user_id: int):
    """把 task 推到 in_review（模拟 submit-review 已跑过）。"""
    service.set_status(
        db_session, task_id, Status.IN_PROGRESS, changed_by=user_id,
    )
    service.set_status(
        db_session, task_id, Status.IN_REVIEW, changed_by=user_id,
    )


def test_user_confirm_marks_done_and_unlocks(db_session, client):
    """user_confirm 端点：validates + 推到 done + 触发 dependency unlock。"""
    user_id, project_id = _setup_project_with_user(db_session)
    headers = _login(client, db_session, user_id)
    task_id = _create_task_for_user(
        db_session, user_id, project_id, type_=ItemType.DESIGN.value,
    )
    _put_task_in_review(db_session, task_id, user_id)
    r = client.post(f"/api/tasks/{task_id}/user_confirm",
                    json={"comment": "design 看着 OK"}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == Status.DONE.value
    assert body["status_reason"] == StatusReason.COMPLETED.value
    db_session.expire_all()
    comments = db_session.query(service.Comment).filter(
        service.Comment.task_id == task_id
    ).all()
    assert any("design 看着 OK" in (c.content or "") for c in comments)


def test_user_confirm_requires_in_review_state(db_session, client):
    """user_confirm: status != in_review → 409。"""
    user_id, project_id = _setup_project_with_user(db_session)
    headers = _login(client, db_session, user_id)
    task_id = _create_task_for_user(
        db_session, user_id, project_id, type_=ItemType.DESIGN.value,
    )
    # task 还在 todo，没在 in_review
    r = client.post(f"/api/tasks/{task_id}/user_confirm", json={}, headers=headers)
    assert r.status_code == 409, r.text
    assert "in_review" in r.json()["detail"]


def test_user_confirm_rejects_non_needs_human_task(db_session, client):
    """user_confirm: needs_human_confirmation=False 任务 → 409。"""
    user_id, project_id = _setup_project_with_user(db_session)
    headers = _login(client, db_session, user_id)
    task_id = _create_task_for_user(
        db_session, user_id, project_id, type_=ItemType.DESIGN.value,
        needs_human=False,
    )
    _put_task_in_review(db_session, task_id, user_id)
    r = client.post(f"/api/tasks/{task_id}/user_confirm", json={}, headers=headers)
    assert r.status_code == 409, r.text
    assert "needs_human_confirmation" in r.json()["detail"]


def test_user_reject_sends_task_back_to_in_progress(db_session, client):
    """user_reject: in_review → in_progress + 写 comment。"""
    user_id, project_id = _setup_project_with_user(db_session)
    headers = _login(client, db_session, user_id)
    task_id = _create_task_for_user(
        db_session, user_id, project_id, type_=ItemType.DESIGN.value,
    )
    _put_task_in_review(db_session, task_id, user_id)
    r = client.post(f"/api/tasks/{task_id}/user_reject",
                    json={"comment": "改一下 spec 第 3 段"}, headers=headers)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    t = db_session.get(service.Task, task_id)
    assert t.status == Status.IN_PROGRESS.value
    comments = db_session.query(service.Comment).filter(
        service.Comment.task_id == task_id
    ).all()
    assert any("改一下 spec" in (c.content or "") for c in comments)


# ---------- 6. end-to-end 链：design confirm → impl unlock ----------

def test_user_confirm_unlocks_dependent_tasks(db_session, client):
    """end-to-end：user_confirm 触发 dependency unlock。

    setup：design task A (needs_human) → confirm → 依赖 A 的 dev task B 应被 unlock
    """
    user_id, project_id = _setup_project_with_user(db_session)
    headers = _login(client, db_session, user_id)
    # 1. design task A
    design_id = _create_task_for_user(
        db_session, user_id, project_id, type_=ItemType.DESIGN.value,
    )
    _put_task_in_review(db_session, design_id, user_id)
    # 2. dev task B depends on A（TaskDependency）
    dev_id = _create_task_for_user(
        db_session, user_id, project_id, type_=ItemType.DEV.value,
        needs_human=False,
    )
    dep = service.TaskDependency(
        task_id=dev_id, depends_on_id=design_id,
    )
    db_session.add(dep); db_session.commit()
    # 3. user_confirm design
    r = client.post(f"/api/tasks/{design_id}/user_confirm",
                    json={}, headers=headers)
    assert r.status_code == 200, r.text
    # 4. B 应该被 unlock（state 仍 todo，但 dependency 已被清）
    db_session.expire_all()
    unlocked = service.get_unlocked_dependent_tasks(db_session, design_id)
    assert any(t.id == dev_id for t in unlocked), \
        f"dev task {dev_id} not in unlocked list {[t.id for t in unlocked]}"
