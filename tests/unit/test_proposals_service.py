"""Proposals service 单元测试。

Phase 4 第四段:验证 features.proposals.service 的 Proposal 基础 CRUD。
"""
import os
os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_proposals_tmp.db"

import uuid
import pytest

from agentboard.core.infrastructure.database import (
    SessionLocal, engine, init_db,
)
from agentboard.features.projects.service import create_project
from agentboard.features.proposals.service import (
    create_proposal, get_proposal, list_proposals, get_proposal_project_id,
)


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    db_path = "_test_proposals_tmp.db"
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
    return create_project(session, name=f"p-{suffix}", key=f"PP{suffix}", description="")


def test_create_and_get_proposal(session, project):
    p = create_proposal(session, project_id=project.id, title="test", content="desc")
    assert p.id is not None
    assert p.title == "test"
    p2 = get_proposal(session, p.id)
    assert p2 is not None
    assert p2.id == p.id


def test_list_proposals(session, project):
    create_proposal(session, project_id=project.id, title="p1", content="c1")
    create_proposal(session, project_id=project.id, title="p2", content="c2")
    proposals = list_proposals(session, project_id=project.id, limit=200)
    assert len(proposals) >= 2


def test_get_proposal_project_id(session, project):
    p = create_proposal(session, project_id=project.id, title="p", content="c")
    assert get_proposal_project_id(session, p.id) == project.id


def test_get_proposal_not_found(session):
    assert get_proposal(session, 999999) is None
