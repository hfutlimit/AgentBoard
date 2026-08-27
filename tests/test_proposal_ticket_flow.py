"""Proposal → Ticket 异步转化（2026-08-08 文档 #59）service 层单测。

覆盖：
1. 状态机：新状态合法迁移（converged→ticket_preparing→ticket_created；
   ticket_preparing→converged 回退）；非法迁移拒绝；
2. create_proposal 初始 pending；用户编辑澄清流内容 → 回退 pending；
   worker 写 converged_spec 不回退；
3. create_ticket_request：converged 前提、converged_spec 非空、层级校验
   （story 必挂 epic；task/bug 必挂 epic+story；跨项目拒绝）、幂等复用、
   failed 请求重置 pending 重试、proposal → ticket_preparing；
4. execute_ticket_request：四类实体创建 + 回填 + ticket_created；幂等 done 复用；
   processing 冲突拒绝；层级错误拒绝；
5. claim_ticket_request CAS（pending→processing，重复认领失败）；
6. fail_ticket_request → failed + proposal 回退 converged；
7. reclaim_stale_ticket_requests：processing 超时回收联动回退；
8. list_pending_ticket_requests / list_ticket_requests。

运行：PYTHONPATH=. python -m pytest tests/test_proposal_ticket_flow.py -q
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard import service
from agentboard.models import Base
from agentboard.domains.proposals.models import (
    ProposalStatus, PROPOSAL_TRANSITIONS, TICKET_REQUEST_PENDING,
    TICKET_REQUEST_PROCESSING, TICKET_REQUEST_DONE, TICKET_REQUEST_FAILED,
)


def _env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as s:
        u = service.register_user(s, username="ticket-user", password="password123")
        p1 = service.create_project(s, name="P1", key="P1")
        service.add_project_member(s, project_id=p1.id, user_id=u.id, role="owner")
        p2 = service.create_project(s, name="P2", key="P2")
        service.add_project_member(s, project_id=p2.id, user_id=u.id, role="owner")
        e1 = service.create_epic(s, project_id=p1.id, title="E1")
        e2 = service.create_epic(s, project_id=p2.id, title="E2")
        st1 = service.create_story(s, epic_id=e1.id, title="S1")
        st2 = service.create_story(s, epic_id=e2.id, title="S2")
        # 已收敛提案（converged + converged_spec）：pending → queued → analyzing → converged
        pr = service.create_proposal(s, project_id=p1.id, title="Proposal A",
                                     content="need clarity")
        service.set_proposal_status(s, pr.id, "queued")      # 点击「开始 grill」
        service.set_proposal_status(s, pr.id, "analyzing")   # worker 认领
        service.set_proposal_status(s, pr.id, "converged")   # 澄清收敛
        service.update_proposal(s, pr.id, converged_spec="# 需求\n- [ ] 子任务一")
        ids = (p1.id, p2.id, e1.id, e2.id, st1.id, st2.id, pr.id)
    return sessions, ids


def _ids(ids, name):
    return ids[{"p1": 0, "p2": 1, "e1": 2, "e2": 3, "st1": 4, "st2": 5, "pr": 6}[name]]


# ---------- 1. 状态机 ----------

def test_new_status_transitions_legal():
    sessions, _ = _env()
    with sessions() as s:
        # converged → ticket_preparing → ticket_created
        p = service.get_proposal(s, _ids(_, "pr"))
        assert ProposalStatus(p.status) is ProposalStatus.CONVERGED
        assert ProposalStatus.TICKET_PREPARING in PROPOSAL_TRANSITIONS[ProposalStatus.CONVERGED]
        assert ProposalStatus.TICKET_CREATED in PROPOSAL_TRANSITIONS[ProposalStatus.TICKET_PREPARING]
        # ticket_preparing → converged（失败回退）
        assert ProposalStatus.CONVERGED in PROPOSAL_TRANSITIONS[ProposalStatus.TICKET_PREPARING]
        # 终态不可迁移
        assert PROPOSAL_TRANSITIONS[ProposalStatus.TICKET_CREATED] == set()
        # pending → queued（开始 grill）
        assert ProposalStatus.QUEUED in PROPOSAL_TRANSITIONS[ProposalStatus.PENDING]


def test_illegal_transition_rejected():
    sessions, ids = _env()
    with sessions() as s:
        pr = _ids(ids, "pr")
        try:
            service.set_proposal_status(s, pr, "ticket_created")  # converged 直跳终态
            raise AssertionError("expected IllegalTransition")
        except service.IllegalTransition:
            pass


# ---------- 2. 初始态与编辑回退 ----------

def test_create_proposal_initial_pending():
    sessions, ids = _env()
    p1 = _ids(ids, "p1")
    with sessions() as s:
        p = service.create_proposal(s, project_id=p1, title="New", content="x")
        assert p.status == ProposalStatus.PENDING.value


def test_cancel_before_qa_completion_is_terminal():
    sessions, ids = _env()
    p1 = _ids(ids, "p1")
    with sessions() as s:
        p = service.create_proposal(s, project_id=p1, title="Cancel me", content="x")
        service.set_proposal_status(s, p.id, "queued")
        service.set_proposal_status(s, p.id, "analyzing")
        service.add_proposal_questions(
            s, proposal_id=p.id, questions=["still needed?"], agent="codex",
        )
        cancelled = service.set_proposal_status(s, p.id, "cancelled")
        assert cancelled.status == ProposalStatus.CANCELLED.value
        assert cancelled.claimed_by == ""
        assert cancelled.claimed_at is None
        try:
            service.answer_proposal_question(
                s, service.list_proposal_rounds(s, p.id)[0]["questions"][0]["id"],
                answer="too late",
            )
            raise AssertionError("expected InvalidValue")
        except service.InvalidValue:
            pass


def test_round_exposes_actual_agent_identity_and_name():
    sessions, ids = _env()
    p1 = _ids(ids, "p1")
    with sessions() as s:
        service.register_agent(s, agent_id="codex", name="Codex Worker")
        p = service.create_proposal(s, project_id=p1, title="Who asked", content="x")
        service.set_proposal_status(s, p.id, "queued")
        service.set_proposal_status(s, p.id, "analyzing")
        service.add_proposal_questions(
            s, proposal_id=p.id, questions=["Which API?"], agent="codex",
        )
        round_ = service.list_proposal_rounds(s, p.id)[0]
        assert round_["agent"] == "codex"
        assert round_["agent_name"] == "Codex Worker"


def test_edit_rolls_back_to_pending():
    sessions, ids = _env()
    p1 = _ids(ids, "p1")
    with sessions() as s:
        p = service.create_proposal(s, project_id=p1, title="Edit me", content="x")
        service.set_proposal_status(s, p.id, "queued")
        service.update_proposal(s, p.id, title="Edited")
        assert p.status == ProposalStatus.PENDING.value
        # worker 写 converged_spec 不回退（仍在 queued → 需先置 analyzing）
        service.set_proposal_status(s, p.id, "queued")
        service.set_proposal_status(s, p.id, "analyzing")
        service.update_proposal(s, p.id, converged_spec="# spec")
        assert p.status == ProposalStatus.ANALYZING.value


# ---------- 3. create_ticket_request ----------

def test_create_ticket_request_story():
    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        req = service.create_ticket_request(
            s, pr, type="story", epic_id=e1, title="Story title",
        )
        assert req.status == TICKET_REQUEST_PENDING
        assert req.parent_epic_id == e1
        p = service.get_proposal(s, pr)
        assert p.status == ProposalStatus.TICKET_PREPARING.value


def test_auto_ticket_request_is_resolved_by_agent_without_duplicate_request():
    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        req = service.create_ticket_request(s, pr, type="auto")
        result = service.execute_ticket_request(
            s, pr, request_id=req.id, type="story", epic_id=e1,
        )
        assert result["request"]["id"] == req.id
        assert result["request"]["type"] == "auto"
        assert result["request"]["resolved_type"] == "story"
        assert result["proposal"]["ticket_type"] == "story"
        assert len(service.list_ticket_requests(s, pr)) == 1


def test_create_ticket_request_idempotent():
    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        r1 = service.create_ticket_request(s, pr, type="story", epic_id=e1)
        r2 = service.create_ticket_request(s, pr, type="story", epic_id=e1)
        assert r1.id == r2.id


def test_create_ticket_request_failed_resets_to_pending():
    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        r1 = service.create_ticket_request(s, pr, type="story", epic_id=e1)
        service.fail_ticket_request(s, r1.id, error="boom")
        r2 = service.create_ticket_request(s, pr, type="story", epic_id=e1)
        assert r2.id == r1.id
        assert r2.status == TICKET_REQUEST_PENDING
        assert r2.error == ""
        p = service.get_proposal(s, pr)
        assert p.status == ProposalStatus.TICKET_PREPARING.value


def test_create_ticket_request_hierarchy_validation():
    sessions, ids = _env()
    pr, p2, e1, e2, st1, st2 = (_ids(ids, k) for k in ("pr", "p2", "e1", "e2", "st1", "st2"))
    with sessions() as s:
        # story 缺 epic_id
        try:
            service.create_ticket_request(s, pr, type="story")
            raise AssertionError("expected InvalidValue")
        except service.InvalidValue:
            pass
        # task 缺 story_id
        try:
            service.create_ticket_request(s, pr, type="task", epic_id=e1)
            raise AssertionError("expected InvalidValue")
        except service.InvalidValue:
            pass
        # 跨项目 epic
        try:
            service.create_ticket_request(s, pr, type="story", epic_id=e2)
            raise AssertionError("expected InvalidValue")
        except service.InvalidValue as e:
            assert "不属于" in str(e)
        # story 不属于指定 epic
        try:
            service.create_ticket_request(s, pr, type="task", epic_id=e1, story_id=st2)
            raise AssertionError("expected InvalidValue")
        except service.InvalidValue as e:
            assert "不属于 epic" in str(e)
        # 非法类型
        try:
            service.create_ticket_request(s, pr, type="sprint")
            raise AssertionError("expected InvalidValue")
        except service.InvalidValue:
            pass


# ---------- 4. execute_ticket_request ----------

def test_execute_story_ticket():
    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        result = service.execute_ticket_request(
            s, pr, type="story", epic_id=e1, title="S-Title",
        )
        ticket = result["ticket"]
        assert ticket["title"] == "S-Title"
        assert ticket["epic_id"] == e1
        p = service.get_proposal(s, pr)
        assert p.status == ProposalStatus.TICKET_CREATED.value
        assert p.ticket_type == "story"
        assert p.ticket_id == ticket["id"]
        assert p.story_id == ticket["id"]  # story 快捷字段兼容
        assert result["request"]["status"] == TICKET_REQUEST_DONE


def _converged_proposal(s, p1: int, title: str) -> int:
    p = service.create_proposal(s, project_id=p1, title=title, content="x")
    service.set_proposal_status(s, p.id, "queued")
    service.set_proposal_status(s, p.id, "analyzing")
    service.set_proposal_status(s, p.id, "converged")
    service.update_proposal(s, p.id, converged_spec="# 需求\n- [ ] 子任务一")
    return p.id


def test_execute_task_and_bug_tickets():
    sessions, ids = _env()
    p1, e1, st1 = _ids(ids, "p1"), _ids(ids, "e1"), _ids(ids, "st1")
    with sessions() as s:
        # task
        pr_task = _converged_proposal(s, p1, "T")
        result = service.execute_ticket_request(
            s, pr_task, type="task", epic_id=e1, story_id=st1, title="Task-1",
        )
        ticket = result["ticket"]
        assert ticket["type"] == "dev"  # Story 265 后任务类型 task→dev
        assert ticket["story_id"] == st1
        # bug（独立提案，复用 task 表 type=bug）
        pr_bug = _converged_proposal(s, p1, "B")
        result2 = service.execute_ticket_request(
            s, pr_bug, type="bug", epic_id=e1, story_id=st1, title="Bug-1",
        )
        assert result2["ticket"]["type"] == "bug"


def test_execute_epic_ticket():
    sessions, ids = _env()
    pr = _ids(ids, "pr")
    with sessions() as s:
        result = service.execute_ticket_request(s, pr, type="epic", title="Epic-1")
        assert result["ticket"]["project_id"] == _ids(ids, "p1")
        p = service.get_proposal(s, pr)
        assert p.ticket_type == "epic"


def test_execute_idempotent_when_done():
    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        r1 = service.execute_ticket_request(s, pr, type="story", epic_id=e1)
        r2 = service.execute_ticket_request(s, pr, type="story", epic_id=e1)
        assert r1["ticket"]["id"] == r2["ticket"]["id"]
        assert r2["request"]["status"] == TICKET_REQUEST_DONE


def test_execute_conflict_when_processing():
    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        req = service.create_ticket_request(s, pr, type="story", epic_id=e1)
        service.claim_ticket_request(s, req.id)  # pending → processing
        try:
            service.execute_ticket_request(s, pr, type="story", epic_id=e1)
            raise AssertionError("expected InvalidValue(processing)")
        except service.InvalidValue as e:
            assert "正在生成中" in str(e)


# ---------- 5/6/7. claim / fail / reclaim ----------

def test_claim_cas():
    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        req = service.create_ticket_request(s, pr, type="story", epic_id=e1)
        claimed = service.claim_ticket_request(s, req.id)
        assert claimed.status == TICKET_REQUEST_PROCESSING
        # 重复认领失败（已是 processing）
        again = service.claim_ticket_request(s, req.id)
        assert again is None or again.status != TICKET_REQUEST_PROCESSING


def test_fail_rolls_back_to_converged():
    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        req = service.create_ticket_request(s, pr, type="story", epic_id=e1)
        failed = service.fail_ticket_request(s, req.id, error="agent down")
        assert failed.status == TICKET_REQUEST_FAILED
        assert "agent down" in failed.error
        p = service.get_proposal(s, pr)
        assert p.status == ProposalStatus.CONVERGED.value  # 回退可重试


def test_reclaim_stale_processing():
    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        req = service.create_ticket_request(s, pr, type="story", epic_id=e1)
        service.claim_ticket_request(s, req.id)
        # 人为把 updated_at 拨回超时
        from agentboard.domains.common.models import utc_now
        from datetime import timedelta
        req.updated_at = utc_now() - timedelta(seconds=99999)
        service._commit(s)
        reclaimed = service.reclaim_stale_ticket_requests(s, lease_seconds=60)
        assert req.id in reclaimed
        s.refresh(req)
        assert req.status == TICKET_REQUEST_FAILED
        p = service.get_proposal(s, pr)
        assert p.status == ProposalStatus.CONVERGED.value


# ---------- 8. 列表 ----------

def test_list_pending_ticket_requests():
    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        service.create_ticket_request(s, pr, type="story", epic_id=e1)
        pending = service.list_pending_ticket_requests(s)
        assert any(r.proposal_id == pr and r.type == "story" for r in pending)
        rows = service.list_ticket_requests(s, pr)
        assert len(rows) == 1
        assert rows[0].type == "story"


def test_execute_ticket_request_failure_rolls_back_and_allows_retry():
    """Regression test (2026-08-27 review):
    pending -> claim -> TicketRef.create raises ValueError / validation error ->
    request.status == failed -> proposal.status == converged -> can retry ticket request.
    Verifies that execute_ticket_request does not leave TicketRequest permanently stuck in processing.
    """
    from unittest.mock import patch
    from agentboard.features.proposals.ticket_ref import TicketRef

    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        req = service.create_ticket_request(s, pr, type="story", epic_id=e1)
        assert req.status == TICKET_REQUEST_PENDING

        # Simulate ValueError inside TicketRef.create
        with patch.object(TicketRef, "create", side_effect=ValueError("Simulated validation failure in entity creation")):
            try:
                service.execute_ticket_request(s, pr, type="story", epic_id=e1, request_id=req.id)
                raise AssertionError("Expected InvalidValue")
            except service.InvalidValue as e:
                assert "Simulated validation failure" in str(e)

        # Verify request is failed and proposal is converged (NOT stuck in processing / ticket_preparing)
        s.refresh(req)
        assert req.status == TICKET_REQUEST_FAILED
        assert "Simulated validation failure" in req.error
        p = service.get_proposal(s, pr)
        assert p.status == ProposalStatus.CONVERGED.value

        # Retry: create_ticket_request resets failed request to pending and proposal to ticket_preparing
        retry_req = service.create_ticket_request(s, pr, type="story", epic_id=e1)
        assert retry_req.id == req.id
        assert retry_req.status == TICKET_REQUEST_PENDING
        s.refresh(p)
        assert p.status == ProposalStatus.TICKET_PREPARING.value

        # Now execute successfully
        result = service.execute_ticket_request(s, pr, type="story", epic_id=e1, request_id=retry_req.id)
        assert result["request"]["status"] == TICKET_REQUEST_DONE
        s.refresh(p)
        assert p.status == ProposalStatus.TICKET_CREATED.value


def test_execute_ticket_request_not_found_parent_rolls_back_to_converged():
    """Verify NotFound during TicketRef.create rolls back to converged."""
    from agentboard.core.exceptions import NotFound
    from agentboard.features.proposals.ticket_ref import TicketRef
    from unittest.mock import patch

    sessions, ids = _env()
    pr, e1 = _ids(ids, "pr"), _ids(ids, "e1")
    with sessions() as s:
        req = service.create_ticket_request(s, pr, type="story", epic_id=e1)
        with patch.object(TicketRef, "create", side_effect=NotFound("epic 999 not found")):
            try:
                service.execute_ticket_request(s, pr, type="story", epic_id=e1, request_id=req.id)
                raise AssertionError("Expected NotFound")
            except NotFound as e:
                assert "epic 999 not found" in str(e)

        s.refresh(req)
        assert req.status == TICKET_REQUEST_FAILED
        p = service.get_proposal(s, pr)
        assert p.status == ProposalStatus.CONVERGED.value

