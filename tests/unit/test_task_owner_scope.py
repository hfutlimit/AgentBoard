"""归属收敛（仅本人 agent 可处理）行为测试。

覆盖 2026-09-01 需求：proposal/task 只允许 owner 的 agent 处理，
防止别人的 agent 抢走我创建的 task。对应 plan §9 执行侧门槛。
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard.core.common.models import Base, utc_now
from agentboard.features.identity.service import register_user
from agentboard.features.projects.models import (
    Agent, AgentInstance, Epic, Project, ProjectMember, Story, Worker,
)
from agentboard.features.scheduling.service import (
    dispatch_implementation_task, list_runnable_candidates,
)
from agentboard.features.work_items import service as task_service
from agentboard.features.work_items.models import Task
from agentboard.features.work_items.service import (
    claim_development_task, InvalidValue,
)
from agentboard.core.common.enums import ItemType, Priority


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.rollback(); s.close(); engine.dispose()


def _member(s, p, u):
    s.add(ProjectMember(project_id=p.id, user_id=u.id, role="member")); s.commit()


def _project(s):
    p = Project(key=f"P{uuid.uuid4().hex[:6].upper()}", name="owner scope test")
    s.add(p); s.commit(); s.refresh(p)
    return p


def _story(s, p):
    e = Epic(project_id=p.id, title="e", description="")
    s.add(e); s.commit(); s.refresh(e)
    st = Story(epic_id=e.id, title="st", description="")
    s.add(st); s.commit(); s.refresh(st)
    return st.id


def _online_agent(s, agent_id, tool, user_id, worker_id):
    w = s.query(Worker).filter(Worker.worker_id == worker_id).first()
    if w is None:
        w = Worker(worker_id=worker_id, hostname="h", status="active", last_heartbeat=utc_now())
        s.add(w); s.commit()
    a = Agent(agent_id=agent_id, name=agent_id, user_id=user_id, cli_command="",
              model="", enabled=True, online=True, last_heartbeat=utc_now(), roles="[]")
    s.add(a); s.commit()
    inst = AgentInstance(worker_id=worker_id, agent_id=agent_id, cli_command="",
                         model="", auth_key="", enabled=True, online=True,
                         last_heartbeat=utc_now(), executor_type=tool)
    s.add(inst); s.commit()
    return a


def test_cross_owner_claim_rejected(db_session):
    """owner=u1 的 task，u2 的 agent 不能认领。"""
    p = _project(db_session)
    u1 = register_user(db_session, username=f"a-{uuid.uuid4().hex[:6]}", password="password1234")
    u2 = register_user(db_session, username=f"b-{uuid.uuid4().hex[:6]}", password="password1234")
    _member(db_session, p, u1); _member(db_session, p, u2)
    t = task_service.create_task(db_session, project_id=p.id, story_id=None,
                                 title="owned by u1", type=ItemType.DEV.value,
                                 priority=Priority.MEDIUM.value, created_by_user_id=u1.id)
    with pytest.raises(InvalidValue):
        claim_development_task(db_session, t.id, user_id=u2.id)


def test_ownerless_task_claim_fail_closed(db_session):
    """created_by_user_id 为 NULL 的 task 不可认领（决策 c：人工补 owner）。"""
    p = _project(db_session)
    u = register_user(db_session, username=f"c-{uuid.uuid4().hex[:6]}", password="password1234")
    _member(db_session, p, u)
    t = task_service.create_task(db_session, project_id=p.id, story_id=None,
                                 title="no owner", type=ItemType.DEV.value,
                                 priority=Priority.MEDIUM.value)  # 无 owner
    with pytest.raises(InvalidValue):
        claim_development_task(db_session, t.id, user_id=u.id)


def test_ownerless_task_not_dispatched(db_session):
    """无 owner 的 task 候选池为空 → 派发保持 todo。"""
    p = _project(db_session)
    u = register_user(db_session, username=f"d-{uuid.uuid4().hex[:6]}", password="password1234")
    _member(db_session, p, u)
    story_id = _story(db_session, p)
    _online_agent(db_session, "codex-x", "codex", u.id, "pc-01")
    t = task_service.create_task(db_session, project_id=p.id, story_id=story_id,
                                 title="orphan", type=ItemType.DEV.value,
                                 priority=Priority.MEDIUM.value)
    assert list_runnable_candidates(db_session, t, "task") == []
    result = dispatch_implementation_task(db_session, t.id)
    assert result is None
    db_session.refresh(t)
    assert t.status == "todo"


def test_dispatch_only_owner_agent_selected(db_session):
    """两个都是项目成员，各有一个在线 codex agent；
    task owner=u1 时只有 u1 的 agent 入选，u2 的 agent 不被选。"""
    p = _project(db_session)
    u1 = register_user(db_session, username=f"e-{uuid.uuid4().hex[:6]}", password="password1234")
    u2 = register_user(db_session, username=f"f-{uuid.uuid4().hex[:6]}", password="password1234")
    _member(db_session, p, u1); _member(db_session, p, u2)
    story_id = _story(db_session, p)
    _online_agent(db_session, "codex-u2", "codex", u2.id, "pc-02")
    a1 = _online_agent(db_session, "codex-u1", "codex", u1.id, "pc-01")
    t = task_service.create_task(db_session, project_id=p.id, story_id=story_id,
                                 title="owned u1", type=ItemType.DEV.value,
                                 priority=Priority.MEDIUM.value, created_by_user_id=u1.id)
    cands = list_runnable_candidates(db_session, t, "task")
    owner_ids = {c[0].user_id for c in cands}
    assert owner_ids == {u1.id}, f"候选应仅 owner u1 的 agent，实得 {owner_ids}"
    assert a1.id in {c[0].id for c in cands}
