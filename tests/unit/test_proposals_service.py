"""Proposals service 单元测试。

Phase 4 第四段:验证 features.proposals.service 的 Proposal 基础 CRUD。
"""
import os
os.environ["AGENTBOARD_DB_URL"] = "sqlite:///./_test_proposals_tmp.db"

import uuid
import pytest

from agentboard.core.exceptions import IllegalTransition, InvalidValue
from agentboard.core.infrastructure.database import (
    SessionLocal, engine, init_db,
)
from agentboard.features.projects.service import create_project
from agentboard.features.proposals.models import ProposalQuestion
from agentboard.features.proposals.service import (
    add_proposal_questions, answer_proposal_question, claim_proposal,
    create_proposal, create_ticket_request, execute_ticket_request,
    get_proposal, list_proposals, get_proposal_project_id,
    set_proposal_status, update_proposal,
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


# ---- Story 389：auto_create_ticket 状态边界 + 取消清理 claim ----

def _converged_proposal(session, project, *, auto_create_ticket=False):
    """pending → queued → analyzing → converged 的最短合法路径。"""
    p = create_proposal(
        session, project_id=project.id, title=f"ac-{uuid.uuid4().hex[:6]}",
        content="- [ ] X", auto_create_ticket=auto_create_ticket,
    )
    set_proposal_status(session, p.id, "queued")
    claim_proposal(session, p.id, agent="t")
    set_proposal_status(session, p.id, "converged")
    return p


def test_auto_create_ticket_persisted_on_create(session, project):
    p = create_proposal(
        session, project_id=project.id, title="ac-on",
        content="c", auto_create_ticket=True,
    )
    assert p.auto_create_ticket is True
    assert get_proposal(session, p.id).auto_create_ticket is True
    p_off = create_proposal(
        session, project_id=project.id, title="ac-off", content="c",
    )
    assert p_off.auto_create_ticket is False


@pytest.mark.parametrize("status", [
    "pending", "queued", "analyzing", "awaiting", "answered", "failed",
])
def test_auto_create_ticket_modifiable_before_converge(session, project, status):
    """收敛前所有允许状态均可反复修改选项并持久化。"""
    p = create_proposal(session, project_id=project.id, title="ac-m", content="c")
    if status != "pending":
        set_proposal_status(session, p.id, "queued")
        if status == "failed":
            set_proposal_status(session, p.id, "failed")
        else:
            claim_proposal(session, p.id, agent="t")  # → analyzing
            if status in ("awaiting", "answered"):
                add_proposal_questions(
                    session, proposal_id=p.id, questions=["q?"], agent="t",
                )
                if status == "answered":
                    answer_proposal_question(
                        session,
                        session.query(ProposalQuestion)
                        .filter(ProposalQuestion.proposal_id == p.id)
                        .first().id,
                        answer="a",
                    )
            elif status == "queued":
                set_proposal_status(session, p.id, "queued")
    updated = update_proposal(session, p.id, auto_create_ticket=True)
    assert updated.auto_create_ticket is True
    updated = update_proposal(session, p.id, auto_create_ticket=False)
    assert updated.auto_create_ticket is False


@pytest.mark.parametrize("status", ["converged", "ticket_preparing", "ticket_created"])
def test_auto_create_ticket_locked_after_converge(session, project, status):
    """收敛 / 建单阶段服务端拒绝修改（422 语义：InvalidValue）。"""
    from agentboard.features.projects.models import Epic
    session.add(Epic(project_id=project.id, title="Lock Epic"))
    session.commit()
    p = _converged_proposal(session, project)
    if status != "converged":
        p.converged_spec = "- [ ] X"
        session.commit()
        create_ticket_request(session, p.id, type="story",
                              epic_id=_first_epic_id(session, project.id))
        if status == "ticket_created":
            execute_ticket_request(
                session, p.id, type="story",
                epic_id=_first_epic_id(session, project.id),
            )
    with pytest.raises(InvalidValue):
        update_proposal(session, p.id, auto_create_ticket=True)


def test_auto_create_ticket_rejected_after_cancel(session, project):
    p = create_proposal(session, project_id=project.id, title="ac-c", content="c")
    set_proposal_status(session, p.id, "cancelled")
    with pytest.raises(InvalidValue):
        update_proposal(session, p.id, auto_create_ticket=True)


def test_cancelled_proposal_clears_claim(session, project):
    """取消时清除 Worker claim（claimed_by/claimed_at）。"""
    p = create_proposal(session, project_id=project.id, title="ac-cl", content="c")
    set_proposal_status(session, p.id, "queued")
    claim_proposal(session, p.id, agent="worker-a")
    cancelled = set_proposal_status(session, p.id, "cancelled")
    assert cancelled.status == "cancelled"
    assert cancelled.claimed_by == ""
    assert cancelled.claimed_at is None
    # 取消后禁止编辑 / 回答 / 重新入队（终态，非法迁移被状态机拒绝）
    with pytest.raises(InvalidValue):
        update_proposal(session, p.id, title="nope")
    with pytest.raises(IllegalTransition):
        set_proposal_status(session, p.id, "queued")


def _first_epic_id(session, project_id) -> int:
    from agentboard.features.projects.models import Epic
    e = session.query(Epic).filter(Epic.project_id == project_id).first()
    assert e is not None
    return e.id
