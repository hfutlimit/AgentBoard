"""WorkItems service 单元测试。

Phase 4 第二段:验证 features.work_items.service 的 Task CRUD + 状态机集成。
"""
import os
os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_work_items_tmp.db"

import uuid
import pytest

from agentboard.core.common.enums import ItemType, Priority, Status, StatusReason
from agentboard.core.exceptions import IllegalTransition, InvalidValue, NotFound
from agentboard.core.infrastructure.database import (
    SessionLocal, engine, init_db,
)
from agentboard.features.projects.models import Project
from agentboard.features.work_items.service import (
    claim_development_task, create_task, get_task, list_tasks,
    list_task_status_history, query_task_count, set_status,
    submit_task_for_review,
)
from agentboard.features.identity.service import register_user


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    db_path = "_test_work_items_tmp.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db()
    yield
    engine.dispose()


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def project(session):
    suffix = uuid.uuid4().hex[:8]
    p = Project(name=f"wi-test-{suffix}", key=f"WI{suffix}", description="")
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def test_create_and_get_task(session, project):
    t = create_task(
        session, project_id=project.id, story_id=None, title="hello",
        type=ItemType.DEV.value, priority=Priority.MEDIUM.value,
    )
    assert t.id is not None
    assert t.title == "hello"
    assert t.status == Status.TODO.value
    assert t.priority == Priority.MEDIUM.value

    t2 = get_task(session, t.id)
    assert t2 is not None
    assert t2.id == t.id


def test_create_task_invalid_type_raises(session, project):
    with pytest.raises(InvalidValue):
        create_task(
            session, project_id=project.id, story_id=None, title="x",
            type="invalid-type", priority=Priority.MEDIUM.value,
        )


def test_create_task_project_not_found(session):
    with pytest.raises(NotFound):
        create_task(
            session, project_id=999999, story_id=None, title="x",
            type=ItemType.DEV.value, priority=Priority.MEDIUM.value,
        )


def test_list_tasks_filter_by_story(session, project):
    for i in range(3):
        create_task(
            session, project_id=project.id, story_id=None, title=f"t{i}",
            type=ItemType.DEV.value, priority=Priority.MEDIUM.value,
        )
    tasks = list_tasks(session, story_id=None, limit=200)
    assert len(tasks) >= 3


def test_query_task_count(session, project):
    n_before = query_task_count(session)
    create_task(
        session, project_id=project.id, story_id=None, title="count-test",
        type=ItemType.DEV.value, priority=Priority.MEDIUM.value,
    )
    n_after = query_task_count(session)
    assert n_after == n_before + 1


def test_set_status_via_state_machine(session, project):
    t = create_task(
        session, project_id=project.id, story_id=None, title="sm-test",
        type=ItemType.DEV.value, priority=Priority.MEDIUM.value,
    )
    set_status(session, t.id, Status.IN_PROGRESS.value)
    session.refresh(t)
    assert t.status == Status.IN_PROGRESS.value

    # 写历史
    history = list_task_status_history(session, t.id)
    assert len(history) >= 1
    assert history[0].from_status == Status.TODO.value
    assert history[0].to_status == Status.IN_PROGRESS.value


def test_set_status_illegal_raises(session, project):
    t = create_task(
        session, project_id=project.id, story_id=None, title="illegal",
        type=ItemType.DEV.value, priority=Priority.MEDIUM.value,
    )
    # todo → in_review 不合法
    with pytest.raises(IllegalTransition):
        set_status(session, t.id, Status.IN_REVIEW.value)


def test_set_status_blocked_requires_reason(session, project):
    t = create_task(
        session, project_id=project.id, story_id=None, title="blocked-test",
        type=ItemType.DEV.value, priority=Priority.MEDIUM.value,
    )
    # 无 reason → InvalidValue(由 SM validator 抛)
    with pytest.raises(InvalidValue):
        set_status(session, t.id, Status.BLOCKED.value)
    # 传 reason
    set_status(
        session, t.id, Status.BLOCKED.value,
        status_reason=StatusReason.LEGACY.value,
    )
    session.refresh(t)
    assert t.status == Status.BLOCKED.value
    assert t.previous_status == Status.TODO.value


def test_claim_development_task(session, project):
    u = register_user(session, username=f"dev-{uuid.uuid4().hex[:6]}", password="password1234")
    t = create_task(
        session, project_id=project.id, story_id=None, title="to-claim",
        type=ItemType.DEV.value, priority=Priority.MEDIUM.value,
        created_by_user_id=u.id,
    )
    claimed = claim_development_task(session, t.id, user_id=u.id)
    assert claimed.status == Status.IN_PROGRESS.value
    assert claimed.assignee_id == u.id


def test_claim_twice_raises(session, project):
    u1 = register_user(session, username=f"d1-{uuid.uuid4().hex[:6]}", password="password1234")
    u2 = register_user(session, username=f"d2-{uuid.uuid4().hex[:6]}", password="password1234")
    t = create_task(
        session, project_id=project.id, story_id=None, title="race",
        type=ItemType.DEV.value, priority=Priority.MEDIUM.value,
        created_by_user_id=u1.id,
    )
    claim_development_task(session, t.id, user_id=u1.id)
    with pytest.raises(InvalidValue):
        claim_development_task(session, t.id, user_id=u2.id)


def test_submit_for_review(session, project):
    u = register_user(session, username=f"sub-{uuid.uuid4().hex[:6]}", password="password1234")
    t = create_task(
        session, project_id=project.id, story_id=None, title="submit",
        type=ItemType.DEV.value, priority=Priority.MEDIUM.value,
        created_by_user_id=u.id,
    )
    claim_development_task(session, t.id, user_id=u.id)
    submitted = submit_task_for_review(session, t.id, user_id=u.id)
    assert submitted.status == Status.IN_REVIEW.value


def test_submit_by_other_user_denied(session, project):
    u1 = register_user(session, username=f"o1-{uuid.uuid4().hex[:6]}", password="password1234")
    u2 = register_user(session, username=f"o2-{uuid.uuid4().hex[:6]}", password="password1234")
    t = create_task(
        session, project_id=project.id, story_id=None, title="deny",
        type=ItemType.DEV.value, priority=Priority.MEDIUM.value,
        created_by_user_id=u1.id,
    )
    claim_development_task(session, t.id, user_id=u1.id)
    with pytest.raises(InvalidValue):
        submit_task_for_review(session, t.id, user_id=u2.id)


def test_submit_by_admin_allowed(session, project):
    u = register_user(session, username=f"adm-{uuid.uuid4().hex[:6]}", password="password1234")
    t = create_task(
        session, project_id=project.id, story_id=None, title="admin-sub",
        type=ItemType.DEV.value, priority=Priority.MEDIUM.value,
        created_by_user_id=u.id,
    )
    claim_development_task(session, t.id, user_id=u.id)
    # 任意其他用户只要 is_admin=True 也能 submit
    submitted = submit_task_for_review(session, t.id, user_id=999, is_admin=True)
    assert submitted.status == Status.IN_REVIEW.value
