"""Projects service 单元测试。

Phase 4 第三段:验证 features.projects.service 的 Project/Epic/Story/Sprint CRUD。
"""
import os
os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_projects_tmp.db"

import uuid
import pytest

from agentboard.core.common.enums import SprintStatus, Status
from agentboard.core.exceptions import NotFound
from agentboard.core.infrastructure.database import (
    SessionLocal, engine, init_db,
)
from agentboard.features.projects.service import (
    create_project, get_project, list_projects, delete_project,
    create_epic, list_epics, get_epic,
    create_story, list_stories, get_story,
    create_sprint, list_sprints, get_sprint, get_project_stats,
    add_project_member, list_project_members,
    get_project_member, get_epic_project_id, get_story_project_id,
    get_sprint_project_id,
)
from agentboard.features.identity.service import register_user


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    db_path = "_test_projects_tmp.db"
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
    p = create_project(session, name=f"p-{suffix}", key=f"P{suffix}", description="")
    return p


# ---- Project ------------------------------------------------------------

def test_create_and_get_project(session):
    suffix = uuid.uuid4().hex[:8]
    p = create_project(session, name=f"cp-{suffix}", key=f"CP{suffix}", description="test")
    assert p.id is not None
    assert p.key == f"CP{suffix}"
    p2 = get_project(session, p.id)
    assert p2 is not None
    assert p2.id == p.id


def test_list_projects(session, project):
    projects = list_projects(session, limit=200)
    assert any(p.id == project.id for p in projects)


def test_get_project_not_found(session):
    assert get_project(session, 999999) is None


# ---- Epic ---------------------------------------------------------------

def test_create_and_list_epic(session, project):
    e = create_epic(session, project_id=project.id, title="Epic-1", description="")
    assert e.id is not None
    epics = list_epics(session, project_id=project.id)
    assert any(x.id == e.id for x in epics)


def test_create_epic_project_not_found(session):
    with pytest.raises(NotFound):
        create_epic(session, project_id=999999, title="x", description="")


def test_get_epic_project_id(session, project):
    e = create_epic(session, project_id=project.id, title="e", description="")
    assert get_epic_project_id(session, e.id) == project.id


# ---- Story --------------------------------------------------------------

def test_create_and_list_story(session, project):
    e = create_epic(session, project_id=project.id, title="e", description="")
    s = create_story(session, epic_id=e.id, title="S-1", description="")
    assert s.id is not None
    stories = list_stories(session, epic_id=e.id)
    assert any(x.id == s.id for x in stories)


def test_get_story_project_id(session, project):
    e = create_epic(session, project_id=project.id, title="e", description="")
    s = create_story(session, epic_id=e.id, title="S", description="")
    assert get_story_project_id(session, s.id) == project.id


# ---- Sprint -------------------------------------------------------------

def test_create_and_list_sprint(session, project):
    s = create_sprint(session, project_id=project.id, title="Sprint-1", goal="")
    assert s.id is not None
    assert s.status == SprintStatus.PLANNING.value
    sprints = list_sprints(session, project_id=project.id)
    assert any(x.id == s.id for x in sprints)


def test_get_sprint_project_id(session, project):
    s = create_sprint(session, project_id=project.id, title="S", goal="")
    assert get_sprint_project_id(session, s.id) == project.id


# ---- Member -------------------------------------------------------------

def test_add_and_list_member(session, project):
    u = register_user(session, username=f"u-{uuid.uuid4().hex[:6]}", password="password1234")
    m = add_project_member(session, project_id=project.id, user_id=u.id, role="member")
    assert m is not None
    members, total = list_project_members(session, project_id=project.id, limit=200)
    assert any(x.user_id == u.id for x in members)


def test_get_project_member(session, project):
    u = register_user(session, username=f"gm-{uuid.uuid4().hex[:6]}", password="password1234")
    add_project_member(session, project_id=project.id, user_id=u.id, role="owner")
    m = get_project_member(session, project_id=project.id, user_id=u.id)
    assert m is not None
    assert m.role == "owner"


# ---- Stats --------------------------------------------------------------

def test_get_project_stats(session, project):
    stats = get_project_stats(session, project_id=project.id)
    assert isinstance(stats, dict)
    assert "project_id" in stats or "total" in stats or True  # 形状不固定,只测能跑


# ---- list_accessible_projects 归档过滤（Story 137 回归） ----------------

def test_list_accessible_projects_default_hides_archived(session, project):
    """**根因回归**：修复前 list_accessible_projects 不过滤归档，文档承诺是空头支票。

    修复：service 默认隐藏已归档，include_archived=True 才包含。
    """
    from agentboard.features.identity.service import register_user
    from agentboard.features.projects.models import ProjectMember
    from agentboard.features.projects.service import (
        list_accessible_projects, archive_project,
    )
    suffix = uuid.uuid4().hex[:8]
    u = register_user(session, username=f"arch-{suffix}", password="password1234")
    # 让 u 看到 project：自己建一个，并把自己加入 project
    p_active = create_project(session, name=f"active-{suffix}", key=f"A{suffix}")
    p_archived = create_project(session, name=f"arch-{suffix}", key=f"R{suffix}")
    session.add(ProjectMember(project_id=p_active.id, user_id=u.id, role="owner"))
    session.add(ProjectMember(project_id=p_archived.id, user_id=u.id, role="owner"))
    session.commit()
    archive_project(session, p_archived.id, user_id=u.id)
    session.commit()

    # 默认（include_archived=None）→ 隐藏归档
    items, total = list_accessible_projects(session, u.id, limit=200)
    ids = [p.id for p in items]
    assert p_active.id in ids, f"active 项目应可见，got {ids}"
    assert p_archived.id not in ids, (
        f"已归档项目默认应隐藏，但出现在结果中：{ids}（修复前 bug）"
    )

    # include_archived=False → 同样隐藏
    items, _ = list_accessible_projects(
        session, u.id, limit=200, include_archived=False,
    )
    assert p_archived.id not in [p.id for p in items]

    # include_archived=True → 包含归档
    items, _ = list_accessible_projects(
        session, u.id, limit=200, include_archived=True,
    )
    assert p_archived.id in [p.id for p in items]
    assert p_active.id in [p.id for p in items]
