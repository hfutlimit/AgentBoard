"""Step 3 验收测试：StateMachine 一等公民 + TicketRef（Story 239）。

验收标准：
1. **加新状态演示**：临时在 transitions 加 1 项即可支持新状态流转，
   不需要改 service.py 任何函数（副作用绑定在 state_machine 层）。
2. 既有 set_proposal_status / claim_proposal / execute_ticket_request 签名不变。
3. 副作用（FAILED 写 error、成功终态清计数、analyzing 盖租约、离开清租约）
   在迁移后正确执行。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard import service
from agentboard.models import Base
from agentboard.domains.proposals.models import ProposalStatus
# 注意:state_machine 模块的 import 必须在每个 test 函数内部做,而不是模块级
# 别名。test_story_status_machine / test_ticket_search 会在自己的顶部
# ``del sys.modules['agentboard.*']`` 后重新 import agentboard,导致
# state_machine 里 ``_models`` 的引用指向「旧」models 模块;
# 而本 test 的模块级 import 已经把 ProposalStateMachine 缓存为旧类。
# 函数级 import 每次取最新,避免这种顺序污染。
_ProposalStateMachine = None  # 延迟到 test 函数里取


def _env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as s:
        u = service.register_user(s, username="sm-user", password="password123")
        pj = service.create_project(s, name="SM", key="SM")
        service.add_project_member(s, project_id=pj.id, user_id=u.id, role="owner")
    return sessions


def _mk_proposal(s, sessions):
    u = s.query(service.User).filter_by(username="sm-user").one()
    pj = s.query(service.Project).filter_by(key="SM").one()
    return service.create_proposal(
        s, project_id=pj.id, author_id=u.id, title="SM proposal", content="body",
    )


# ---------- 验收标准 1：加新状态只需动 transitions 字典 ----------

def test_add_new_status_only_needs_transitions_entry():
    """演示：新增 PENDING_REVIEW 状态，仅扩展 transitions 字典即可流转。

    StateMachine.execute 只依赖 transitions 字典（唯一事实源），不依赖
    service.py 任何函数的 if 分支。新状态需同时加入枚举（定义层），
    但迁移逻辑零改动——这正是 P0-1「状态机定义层和执行层分家」的验收。
    """
    # Phase 拆分后实际实现与 TRANSITIONS 字典在 features/proposals/models；
    # domains/ 是 re-export shim，重绑定其模块属性不会传播到 state_machine 读取处。
    from agentboard.features.proposals import models as pm
    from agentboard.features.proposals import state_machine as sm_mod
    print(f"DEBUG pm is sm_mod._models? {pm is sm_mod._models}", flush=True)
    print(f"DEBUG pm.PROPOSAL_TRANSITIONS id={id(pm.PROPOSAL_TRANSITIONS)}", flush=True)
    print(f"DEBUG sm_mod._models.PROPOSAL_TRANSITIONS id={id(sm_mod._models.PROPOSAL_TRANSITIONS)}", flush=True)

    orig = pm.PROPOSAL_TRANSITIONS
    fake = {k: set(v) for k, v in orig.items()}
    fake.setdefault(ProposalStatus.CONVERGED, set()).add("pending_review")
    fake.setdefault("pending_review", set()).add(ProposalStatus.TICKET_PREPARING.value)

    pm.PROPOSAL_TRANSITIONS = fake
    try:
        # 函数级 import:取最新的 state_machine 类(避免被前序 test
        # ``del sys.modules`` 后 reload 出来的旧 _models 引用污染)。
        from agentboard.features.proposals.state_machine import (
            IllegalTransitionError as _ITE,
            ProposalStateMachine as _PSM,
        )
        sm = _PSM()
        # 迁移定义层：can_transition 直接反映字典改动
        assert sm.can_transition(ProposalStatus.CONVERGED, "pending_review")
        assert sm.can_transition("pending_review", ProposalStatus.TICKET_PREPARING)
        # 执行层：execute 直接驱动新状态（不经过 service 枚举守卫，属定义层）
        sessions = _env()
        with sessions() as s:
            p = _mk_proposal(s, sessions)
            p.status = ProposalStatus.CONVERGED.value
            sm.execute(s, p, "pending_review")
            assert p.status == "pending_review"
            # 非法迁移（pending_review → ticket_created 未定义边）被拒
            try:
                sm.execute(s, p, ProposalStatus.TICKET_CREATED)
                raised = False
            except _ITE:
                raised = True
            assert raised
            # 合法迁移（pending_review → ticket_preparing 已定义）通过
            sm.execute(s, p, ProposalStatus.TICKET_PREPARING)
            assert p.status == ProposalStatus.TICKET_PREPARING.value
    finally:
        pm.PROPOSAL_TRANSITIONS = orig


# ---------- 验收标准 2：非法迁移仍被拒绝 ----------

def test_illegal_transition_rejected():
    sessions = _env()
    with sessions() as s:
        p = _mk_proposal(s, sessions)
        try:
            # 终态 TICKET_CREATED 不可回退（transitions 为空）
            service.set_proposal_status(s, p.id, ProposalStatus.QUEUED.value)
            service.set_proposal_status(s, p.id, ProposalStatus.ANALYZING.value)
            service.set_proposal_status(s, p.id, ProposalStatus.AWAITING.value)
            service.set_proposal_status(s, p.id, ProposalStatus.ANSWERED.value)
            service.set_proposal_status(s, p.id, ProposalStatus.CONVERGED.value)
            service.set_proposal_status(s, p.id, ProposalStatus.TICKET_PREPARING.value)
            service.set_proposal_status(s, p.id, ProposalStatus.TICKET_CREATED.value)
            raised = False
            try:
                service.set_proposal_status(s, p.id, ProposalStatus.PENDING.value)
            except service.IllegalTransition:
                raised = True
            assert raised, "终态回退应抛 IllegalTransition"
        finally:
            s.rollback()


# ---------- 验收标准 3：副作用随迁移正确执行 ----------

def test_side_effects_failed_writes_error():
    sessions = _env()
    with sessions() as s:
        p = _mk_proposal(s, sessions)
        service.set_proposal_status(s, p.id, ProposalStatus.QUEUED.value)
        service.set_proposal_status(s, p.id, ProposalStatus.ANALYZING.value)
        p2 = service.set_proposal_status(s, p.id, ProposalStatus.FAILED.value,
                                         error="boom")
        assert p2.error == "boom"
        assert p2.claimed_by == "" and p2.claimed_at is None  # 离开 analyzing 清租约


def test_side_effects_analyzing_sets_lease():
    sessions = _env()
    with sessions() as s:
        p = _mk_proposal(s, sessions)
        service.set_proposal_status(s, p.id, ProposalStatus.QUEUED.value)
        p2 = service.set_proposal_status(s, p.id, ProposalStatus.ANALYZING.value)
        assert p2.claimed_at is not None  # 进入 analyzing 盖租约


def test_side_effects_success_clears_retry():
    sessions = _env()
    with sessions() as s:
        p = _mk_proposal(s, sessions)
        service.set_proposal_status(s, p.id, ProposalStatus.QUEUED.value)
        service.set_proposal_status(s, p.id, ProposalStatus.ANALYZING.value)
        p2 = service.set_proposal_status(s, p.id, ProposalStatus.FAILED.value,
                                         error="Agent 调用失败")
        p2.auto_retry_count = 3
        service.recover_failed_proposals(s, window_seconds=0, max_retries=5)
        # converged 是成功终态 → 清零计数
        p3 = service.set_proposal_status(s, p.id, ProposalStatus.PENDING.value)
        service.set_proposal_status(s, p3.id, ProposalStatus.QUEUED.value)
        service.set_proposal_status(s, p3.id, ProposalStatus.ANALYZING.value)
        p4 = service.set_proposal_status(s, p3.id, ProposalStatus.CONVERGED.value)
        assert (p4.auto_retry_count or 0) == 0


# ---------- TicketRef 值对象 ----------

def test_ticket_ref_creates_epic_and_attaches():
    from agentboard.domains.proposals.ticket_ref import TicketRef
    sessions = _env()
    with sessions() as s:
        p = _mk_proposal(s, sessions)
        p.converged_spec = "# 需求\n- [ ] 任务一"
        ref = TicketRef.create(s, p, type="epic", title="Epic from proposal")
        assert ref.type == "epic" and ref.id is not None
        ref.attach_to_proposal(s, p)
        assert p.ticket_type == "epic"
        assert p.ticket_id == ref.id
        ep = s.get(service.Epic, ref.id)
        assert ep is not None and ep.title == "Epic from proposal"
