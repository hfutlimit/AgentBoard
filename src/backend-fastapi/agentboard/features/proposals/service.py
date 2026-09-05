"""Proposals service:Proposal / ProposalRound / ProposalQuestion / TicketRequest。

Phase 4 第四段:从 service.py 拆出。set_proposal_status 走 ProposalStateMachine
(已存在于 features/proposals/state_machine.py)。复杂 review 闭环留 service.py
后续批次。

老 import 路径兼容:service.py 末尾重绑到本模块。
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, update
from sqlalchemy.orm import Session

from ... import models  # 顶层 facade
from ...core.common.models import utc_now  # noqa: F401  (跨域常量)
from ...core.common.enums import ItemType, Priority
from ...core.exceptions import Conflict, InvalidValue, NotFound
from ...core.service_helpers import _commit, _invalidate_project_stats_cache, _paginate, _required, _ser
from ..projects.models import Epic, Project, Story
from ..identity.models import User
from ..work_items.models import Task, TaskDependency
from .state_machine import (
    IllegalTransitionError as _SM_IllegalTransitionError,
    ProposalStateMachine,
)
from . import ticket_ref

# 状态机全局单例（从原 service.py 2439 行搬迁）
_PROPOSAL_SM = ProposalStateMachine()

log = logging.getLogger("agentboard.features.proposals.service")

from .models import (
    ALL_PROPOSAL_STATUSES, ASKABLE_STATUSES, CLAIMABLE_STATUSES,
    AUTO_RESOLVABLE_TICKET_TYPES, AUTO_TICKET_MODIFIABLE_STATUSES,
    AUTO_STORY_TICKET_TYPE, AUTO_TICKET_TYPE,
    DEFAULT_CLAIM_LEASE_SECONDS, Proposal, ProposalQuestion, ProposalRound,
    ProposalStatus, ProposalTicketRequest,
    TICKET_REQUEST_DONE, TICKET_REQUEST_FAILED,
    TICKET_REQUEST_PENDING, TICKET_REQUEST_PROCESSING,
    TICKET_REQUEST_STATUSES, TICKET_REQUEST_TYPES, TICKET_TYPES,
)

from ...core.common.models import utc_now  # noqa: F401  (跨域常量)
from ...core.exceptions import (
    IllegalTransition,
)

from ..projects.models import (
    ProjectMember,
)


# (DEFAULT_CLAIM_LEASE_SECONDS 已移至 .models, 此处保留仅作 fallback re-bind 兼容)
DEFAULT_CLAIM_LEASE_SECONDS = 1800  # 30 min  # noqa: F811  (re-bound in models)


def reclaim_stale_ticket_requests(
    s: Session, *, lease_seconds: int = DEFAULT_CLAIM_LEASE_SECONDS,
) -> list[int]:
    """回收处理中超时的转换请求（processing 停滞 → failed），proposal 回退 converged。

    返回被回收的 request id 列表（无租约字段，用 updated_at 判定——processing
    只在认领/完成/失败时刷新，worker 崩溃后不再变化，可安全作超时依据）。
    """
    if lease_seconds < 0:
        raise InvalidValue("lease_seconds must be >= 0")
    now = utc_now()
    cutoff = now - timedelta(seconds=lease_seconds)
    stale_ids = [
        row[0]
        for row in s.query(ProposalTicketRequest.id)
        .filter(ProposalTicketRequest.status == TICKET_REQUEST_PROCESSING,
                ProposalTicketRequest.updated_at < cutoff)
        .all()
    ]
    reclaimed = []
    for rid in stale_ids:
        from ..scheduling.durable_routing import durable_project_enabled
        request = s.get(ProposalTicketRequest, rid)
        proposal = s.get(Proposal, request.proposal_id) if request else None
        if request and request.type == "auto_story" and proposal and durable_project_enabled(proposal.project_id):
            continue  # Project-scoped Durable materializer owns recovery/replay.
        fail_ticket_request(s, rid, error=f"处理超时（>{lease_seconds}s），自动回退")
        reclaimed.append(rid)
    return reclaimed


def _proposal_or_404(s: Session, proposal_id: int) -> Proposal:
    """取 Proposal 不存在时 raise NotFound(404)。"""
    p = s.get(Proposal, proposal_id)
    if not p:
        raise NotFound(f"proposal {proposal_id} not found")
    return p


def _check_proposal_status(value: str) -> None:
    """校验 Proposal 状态值在 ALL_PROPOSAL_STATUSES 集合内。"""
    if value not in ALL_PROPOSAL_STATUSES:
        raise InvalidValue(f"invalid proposal status '{value}'")


def _check_ticket_type(value: str) -> None:
    if value not in TICKET_TYPES:
        raise InvalidValue(
            f"invalid ticket type '{value}'，仅允许 {sorted(TICKET_TYPES)}",
        )


def _check_ticket_request_type(value: str) -> None:
    if value not in TICKET_REQUEST_TYPES:
        raise InvalidValue(
            f"invalid ticket request type '{value}'，仅允许 {sorted(TICKET_REQUEST_TYPES)}",
        )


def _check_ticket_request_status(value: str) -> None:
    if value not in TICKET_REQUEST_STATUSES:
        raise InvalidValue(
            f"invalid ticket request status '{value}'，仅允许 {sorted(TICKET_REQUEST_STATUSES)}",
        )


def _validate_ticket_parents(
    s: Session, proposal: Proposal, *, type: str, epic_id: int | None,
    story_id: int | None,
) -> None:
    """层级校验：epic∈项目；story∈epic（且∈项目）；task/bug 必挂 story。

    Phase 9 拆分时本函数被截断（只留 epic_id 必填），story 归属与跨项目
    校验丢失 → 2026-08-15 回归修复补全（与顶层 service.py 旧实现一致）。
    """
    if type == "epic":
        return  # epic 独立，无父级
    if not epic_id:
        raise InvalidValue(f"ticket type '{type}' 需要 epic_id")
    epic = s.get(Epic, epic_id)
    if epic is None:
        raise NotFound(f"epic {epic_id} not found")
    if epic.project_id != proposal.project_id:
        raise InvalidValue(
            f"epic {epic_id} 不属于提案所在项目 {proposal.project_id}",
        )
    if type == "story":
        return
    # task / bug：必挂 story，且 story 属于指定 epic
    if not story_id:
        raise InvalidValue(f"ticket type '{type}' 需要 story_id")
    story = s.get(Story, story_id)
    if story is None:
        raise NotFound(f"story {story_id} not found")
    if story.epic_id != epic_id:
        raise InvalidValue(
            f"story {story_id} 不属于 epic {epic_id}",
        )


def _ticket_request_by_type(
    s: Session, proposal_id: int, type: str,
) -> ProposalTicketRequest | None:
    return (
        s.query(ProposalTicketRequest)
        .filter(ProposalTicketRequest.proposal_id == proposal_id,
                ProposalTicketRequest.type == type)
        .first()
    )


def set_proposal_status(
    s: Session, id: int, new_status: str, *, error: str | None = None,
) -> Proposal:
    """澄清状态机流转，非法迁移抛 IllegalTransition。

    Step 3（Story 239）：校验与副作用统一委托 ``ProposalStateMachine``
    （定义在 state_machine.py，副作用在下方 bind_side_effects 注册），
    加新状态只需扩展 transitions 字典 + 注册副作用，不改本函数。
    """
    p = _proposal_or_404(s, id)
    _check_proposal_status(new_status)
    new = ProposalStatus(new_status)
    try:
        _PROPOSAL_SM.execute(
            s, p, new,
            side_effect_ctx={"error": error},
        )
    except _SM_IllegalTransitionError as e:
        raise IllegalTransition(str(e)) from None
    _commit(s); s.refresh(p); return p


# ---- Step 3：状态迁移副作用注册（委托 StateMachine 前的业务行为） ----


def claim_proposal(s: Session, id: int, *, agent: str = "",
                   user_id: int | None = None) -> Proposal | None:
    """**原子**认领提案：queued/answered → analyzing。

    返回 Proposal 表示认领成功；返回 ``None`` 表示竞争失败（已被他人持有或状态
    不可认领）；提案不存在抛 NotFound。

    为什么必须是单条条件 UPDATE，而不能「先 GET 复核状态再 PUT」：
    ``set_proposal_status`` 对同状态迁移（analyzing→analyzing）是幂等 no-op，
    直接返回 200 而非 400 —— 也就是说 PUT 本身**不具备仲裁能力**，N 个 Worker
    会全部拿到 200 并同时开工。加一次前置 GET 只能收窄窗口，读与写之间的
    TOCTOU 依然存在。这里把「判定」和「写入」压进同一条 SQL：

        UPDATE proposals SET status='analyzing', ...
         WHERE id=:id AND status IN ('queued','answered')

    单行条件更新的原子性由存储引擎保证（SQLite 写锁全局串行化；MariaDB/InnoDB
    行级排他锁 + 加锁读），后到者必然读到已提交的 'analyzing' 而匹配不到任何行，
    ``rowcount`` 恰为 0。竞争由数据库仲裁，不留任何窗口。

    注意语句顺序：UPDATE 必须是本会话的**第一条** SQL。若先 SELECT 会先取到读锁
    再升级为写锁，在并发下平白增加锁冲突（WAL 模式下还可能触发 BUSY_SNAPSHOT）。
    因此「区分 404 与竞争失败」的查询放在 rowcount=0 之后才执行。
    """
    now = utc_now()
    claimable = sorted(st.value for st in CLAIMABLE_STATUSES)
    conditions = [Proposal.id == id, Proposal.status.in_(claimable)]
    if user_id is not None:
        # 归属收敛：只有提案 author（owner）能认领；author 为空的历史提案
        # 匹配不到 → 保持不可认领（fail-closed），需人工补 owner。
        conditions.append(Proposal.author_id == user_id)
    res = s.execute(
        update(Proposal)
        .where(*conditions)
        .values(
            status=ProposalStatus.ANALYZING.value,
            claimed_by=(agent or "").strip()[:100],
            claimed_at=now,
            updated_at=now,
            error="",
        )
        .execution_options(synchronize_session=False),
    )
    if res.rowcount == 1:
        _commit(s)
        p = s.get(Proposal, id)
        s.refresh(p)
        return p
    # 一行都没命中：可能是提案不存在，也可能是竞争失败 —— 此时才去读一次区分。
    s.rollback()
    if not s.get(Proposal, id):
        raise NotFound(f"proposal {id} not found")
    return None


def add_proposal_questions(
    s: Session, *, proposal_id: int, questions: list[str],
    round_no: int | None = None, summary: str = "", agent: str = "",
) -> dict:
    """Agent 回写一轮 open questions，并把提案推进到 awaiting。

    返回 ``{"round": <round dict>, "questions": [...]}``。
    """
    if not questions:
        raise InvalidValue("questions must not be empty")
    cleaned = [str(q).strip() for q in questions if str(q).strip()]
    if not cleaned:
        raise InvalidValue("questions must not be empty")
    r = create_proposal_round(
        s, proposal_id=proposal_id, round_no=round_no, summary=summary, agent=agent,
    )
    # 幂等：同一轮次已有问题则不再重复写入
    already = (
        s.query(ProposalQuestion).filter(ProposalQuestion.round_id == r.id).count()
    )
    if already == 0:
        for i, text in enumerate(cleaned, start=1):
            s.add(ProposalQuestion(
                proposal_id=proposal_id, round_id=r.id, seq=i, question=text,
            ))
        _commit(s)
    p = _proposal_or_404(s, proposal_id)
    if ProposalStatus(p.status) is ProposalStatus.ANALYZING:
        p.status = ProposalStatus.AWAITING.value
        _commit(s); s.refresh(p)
    rows = (
        s.query(ProposalQuestion)
        .filter(ProposalQuestion.round_id == r.id)
        .order_by(ProposalQuestion.seq.asc(), ProposalQuestion.id.asc())
        .all()
    )
    return {"round": _ser(r), "questions": [_ser(x) for x in rows]}


def create_proposal_round(
    s: Session, *, proposal_id: int, round_no: int | None = None,
    summary: str = "", agent: str = "",
) -> ProposalRound:
    """开启一轮澄清。

    ``(proposal_id, round_no)`` 唯一：消息 at-least-once 重投时，同一轮次重复创建
    会命中唯一约束并直接复用既有轮次，天然幂等。

    注意状态校验与幂等复用的**先后顺序**：一次成功提问会把提案推进到 awaiting，
    若先校验状态再查重，任何重投都会在 awaiting 上被 IllegalTransition 挡下，
    「幂等复用」分支永远走不到——即 at-least-once 保证形同虚设。
    因此这里的规则是：**新开轮次**需要 analyzing；**重投已存在的轮次**在任何
    状态下都安全返回既有轮次（不改动任何数据）。
    """
    p = _proposal_or_404(s, proposal_id)
    askable = ProposalStatus(p.status) in ASKABLE_STATUSES
    if round_no is None:
        # 未显式指定轮次时无法判定是否重投，只能按「新开一轮」处理
        if not askable:
            raise IllegalTransition(
                f"proposal {proposal_id} 当前状态 {p.status}，仅 analyzing 可提问",
            )
        round_no = (p.current_round or 0) + 1
    if round_no < 1:
        raise InvalidValue("round_no must be >= 1")
    existing = (
        s.query(ProposalRound)
        .filter(ProposalRound.proposal_id == proposal_id,
                ProposalRound.round_no == round_no)
        .first()
    )
    if existing:  # 幂等：重投同一轮次直接复用（任何状态下都不产生副作用）
        return existing
    if not askable:
        raise IllegalTransition(
            f"proposal {proposal_id} 当前状态 {p.status}，仅 analyzing 可提问",
        )
    r = ProposalRound(
        proposal_id=proposal_id, round_no=round_no,
        summary=summary or "", agent=(agent or "")[:100],
    )
    s.add(r)
    p.current_round = max(p.current_round or 0, round_no)
    _commit(s); s.refresh(r); return r


def list_proposals(
    s: Session, *, project_id: int | None = None, status: str | None = None,
    q: str | None = None, limit: int | None = None, offset: int = 0,
    user_id: int | None = None,
):
    """列出提案。未指定 project_id 时按调用者可见项目收敛（与文档模块一致）。"""
    qry = s.query(Proposal)
    if project_id is not None:
        qry = qry.filter(Proposal.project_id == project_id)
    elif user_id is not None:
        user = s.get(User, user_id)
        if user and not user.is_admin:
            member_pids = [
                r[0]
                for r in s.query(ProjectMember.project_id)
                .filter(ProjectMember.user_id == user_id)
                .all()
            ]
            if member_pids:
                qry = qry.filter(Proposal.project_id.in_(member_pids))
            else:
                qry = qry.filter(False)
    if status is not None:
        _check_proposal_status(status)
        qry = qry.filter(Proposal.status == status)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Proposal.title.ilike(like), Proposal.content.ilike(like)))
    qry = qry.order_by(Proposal.updated_at.desc(), Proposal.id.desc())
    return _paginate(qry, limit, offset).all()


def create_ticket_request(
    s: Session, proposal_id: int, *, type: str,
    epic_id: int | None = None, story_id: int | None = None,
    title: str | None = None,
) -> ProposalTicketRequest:
    """创建转换请求（幂等复用）并推进 proposal → ticket_preparing。

    - 校验：提案存在且状态 converged（若已是 ticket_preparing 且同类型请求存在
      则幂等复用，不重复置状态）；层级合法；
    - 幂等：(proposal_id, type) 唯一 —— done/pending/processing 复用既有请求；
      failed 请求重置为 pending（重新排队）；
    - 请求落库后 proposal 状态 converged → ticket_preparing（异步生成中）。
    """
    _check_ticket_request_type(type)
    p = _proposal_or_404(s, proposal_id)
    if type == AUTO_STORY_TICKET_TYPE:
        epic_id = epic_id or p.target_epic_id
        _validate_ticket_parents(
            s, p, type="story", epic_id=epic_id, story_id=None,
        )
    elif type != AUTO_TICKET_TYPE:
        _validate_ticket_parents(s, p, type=type, epic_id=epic_id, story_id=story_id)

    existing = (
        s.query(ProposalTicketRequest)
        .filter(ProposalTicketRequest.proposal_id == proposal_id,
                ProposalTicketRequest.type == type)
        .first()
    )
    if existing is not None:
        if existing.status == TICKET_REQUEST_FAILED:
            # 失败重试：重置为 pending 重新排队，proposal 回 ticket_preparing
            existing.status = TICKET_REQUEST_PENDING
            existing.error = ""
            existing.updated_at = utc_now()
            if ProposalStatus(p.status) is ProposalStatus.CONVERGED:
                p.status = ProposalStatus.TICKET_PREPARING.value
            _commit(s); s.refresh(existing)
            return existing
        if existing.status == TICKET_REQUEST_DONE:
            return existing  # 已完成，幂等复用（不重复创建）
        # pending / processing：已在生成中，复用
        if ProposalStatus(p.status) is ProposalStatus.CONVERGED:
            p.status = ProposalStatus.TICKET_PREPARING.value
            _commit(s)
        return existing

    if ProposalStatus(p.status) is not ProposalStatus.CONVERGED:
        raise InvalidValue(
            f"proposal {proposal_id} 当前状态为 {p.status}，"
            f"仅 converged 可生成 ticket",
        )
    if not (p.converged_spec or "").strip():
        raise InvalidValue(
            f"proposal {proposal_id} 的 converged_spec 为空，无法生成 ticket",
        )
    req = ProposalTicketRequest(
        proposal_id=proposal_id, type=type,
        parent_epic_id=epic_id if type not in ("epic", AUTO_TICKET_TYPE) else None,
        parent_story_id=story_id if type in ("task", "bug") else None,
        title=(title or "").strip()[:300],
        status=TICKET_REQUEST_PENDING,
    )
    s.add(req)
    p.status = ProposalStatus.TICKET_PREPARING.value
    p.error = ""
    _commit(s); s.refresh(req)
    return req


def get_proposal_project_id(s: Session, proposal_id: int) -> int | None:
    p = s.get(Proposal, proposal_id)
    return p.project_id if p else None


# P3：converged_spec 中生成子 Task 的清单项前缀（与 generate_tasks_from_spec 一致）
_SPEC_TASK_RE = re.compile(r"\s*[-*]\s*\[\s*[ xX]\s*\]\s*(.*)")


def get_proposal(s: Session, id: int) -> Proposal | None:
    return s.get(Proposal, id)


def execute_ticket_request(
    s: Session, proposal_id: int, *, type: str = "",
    epic_id: int | None = None, story_id: int | None = None,
    title: str | None = None, request_id: int | None = None,
) -> dict:
    """agent 经 MCP 创建 ticket 的服务端事务（文档 #59 步骤 [10]）。

    - 定位请求：显式 request_id 或按 (proposal_id, type)；不存在则先创建
      （create_ticket_request，converged 校验内聚其中）；
    - **归属校验**：request_id 必须属于 URL 中的 proposal（跨 Proposal 拒绝，
      2026-08-09 review 修复）；request_id 路径下类型取 req.type，
      type 参数忽略（无需调用方重复传）；
    - CAS 认领 pending → processing，抢到执行权才创建实体；
    - 按类型创建：epic（独立）/ story（挂 epic，回填 story_id）/
      task·bug（挂 story，复用 tasks 表 type 区分）；
    - 回填 request.ticket_id + done，proposal ticket_type/ticket_id +
      ticket_preparing → ticket_created；
    - 幂等：done 复用既有结果；processing 抛 409 由调用方轮询。

    返回 ``{"ticket": {...}, "request": {...}}``。
    """
    p = _proposal_or_404(s, proposal_id)

    resolved_type = ""
    if request_id is not None:
        req = _ticket_request_or_404(s, request_id)
        if req.proposal_id != proposal_id:
            raise NotFound(
                f"ticket request {request_id} 不属于 proposal {proposal_id}",
            )
        if req.type == AUTO_TICKET_TYPE:
            if type not in AUTO_RESOLVABLE_TICKET_TYPES:
                raise InvalidValue(
                    "auto ticket request 必须由 Agent 选择 epic / story / task",
                )
            _validate_ticket_parents(
                s, p, type=type, epic_id=epic_id, story_id=story_id,
            )
            resolved_type = type
        elif req.type == AUTO_STORY_TICKET_TYPE:
            # 新 AUTO 路径的实体类型由服务端固定为 Story；caller 无权覆盖。
            type = AUTO_STORY_TICKET_TYPE
            resolved_type = "story"
        else:
            type = req.type
    else:
        _check_ticket_type(type)
        req = _ticket_request_by_type(s, proposal_id, type)
        if req is None:
            req = create_ticket_request(
                s, proposal_id, type=type, epic_id=epic_id,
                story_id=story_id, title=title,
            )
    if req.status == TICKET_REQUEST_DONE:
        # 幂等：已生成，直接返回既有结果
        return _ticket_execute_result(s, req, proposal_id)

    claimed = claim_ticket_request(s, req.id)
    if claimed is None or claimed.status != TICKET_REQUEST_PROCESSING:
        if req.status == TICKET_REQUEST_PROCESSING:
            raise InvalidValue(
                f"ticket request {req.id} 正在生成中（processing），请稍后查询",
            )
        raise InvalidValue(
            f"ticket request {req.id} 当前状态 {req.status}，无法执行",
        )
    req = claimed

    if req.type == AUTO_TICKET_TYPE:
        req.resolved_type = resolved_type
        req.parent_epic_id = epic_id if resolved_type != "epic" else None
        req.parent_story_id = story_id if resolved_type == "task" else None

    can_resume_auto_story = (
        req.type == AUTO_STORY_TICKET_TYPE
        and p.story_id is not None
        and ProposalStatus(p.status) is ProposalStatus.STORY_CREATED
    )
    if ProposalStatus(p.status) is not ProposalStatus.TICKET_PREPARING and not can_resume_auto_story:
        # 兜底：若 proposal 未在 ticket_preparing（例如创建请求时已置位），
        # 此处校验状态机合法迁移，避免竞态下跳过中间态。
        err = (
            f"proposal {proposal_id} 当前状态 {p.status}，"
            f"仅 ticket_preparing 可执行转换"
        )
        s.rollback()
        fail_ticket_request(s, req.id, error=err)
        raise InvalidValue(err)

    if req.type == AUTO_STORY_TICKET_TYPE:
        request_id = req.id
        try:
            return _execute_auto_story_request(s, p, req, title=title)
        except (InvalidValue, NotFound) as e:
            s.rollback()
            fail_ticket_request(s, request_id, error=str(e))
            raise
        except Exception as e:
            s.rollback()
            fail_ticket_request(s, request_id, error=f"AUTO materialization failed: {e}")
            raise

    # ---- 创建实体（本事务内完成,杜绝部分成功;Step 3 委托 TicketRef）----
    from .ticket_ref import TicketRef
    try:
        ref = TicketRef.create(
            s, p, type=type,
            parent_epic_id=req.parent_epic_id,
            parent_story_id=req.parent_story_id,
            title=title or req.title or None,
        )
    except (ValueError, InvalidValue) as e:
        s.rollback()
        fail_ticket_request(s, req.id, error=str(e))
        raise InvalidValue(str(e)) from None
    except NotFound as e:
        s.rollback()
        fail_ticket_request(s, req.id, error=str(e))
        raise
    ticket_id = ref.id

    req.ticket_id = ticket_id
    req.status = TICKET_REQUEST_DONE
    req.error = ""
    req.updated_at = utc_now()
    p.ticket_type = ref.type
    p.ticket_id = ticket_id
    p.status = ProposalStatus.TICKET_CREATED.value
    p.error = ""
    _commit(s)
    s.refresh(req)
    s.refresh(p)
    return _ticket_execute_result(s, req, proposal_id)


def _execute_auto_story_request(
    s: Session,
    proposal: Proposal,
    req: ProposalTicketRequest,
    *,
    title: str | None = None,
) -> dict:
    """确定性 materialize AUTO Proposal，并激活/派发首批 ready Task。

    request 已由 ``execute_ticket_request`` 用 CAS 认领为 processing。Story/DAG
    与 Proposal 回填先在单事务提交；后续激活/派发幂等执行。若进程在两阶段之间
    退出，reclaim 后重放会复用 ``proposal.story_id``，不会创建第二个 Story。
    """
    from .conversion_service import ProposalConversionService
    from ..projects import service as project_service
    from ..work_items import service as work_item_service
    from ..scheduling.service import dispatch_implementation_task
    from ...core.infrastructure import messaging as mq

    epic_id = req.parent_epic_id or proposal.target_epic_id
    _validate_ticket_parents(
        s, proposal, type="story", epic_id=epic_id, story_id=None,
    )

    story = s.get(Story, proposal.story_id) if proposal.story_id else None
    if story is None:
        plan = ProposalConversionService.plan(proposal, epic_id=epic_id)
        if title is not None:
            plan.story = {**(plan.story or {}), "title": title}
        ProposalConversionService.validate(plan, project_id=proposal.project_id)
        result = ProposalConversionService.apply(
            s, plan, proposal, commit=False,
        )
        story = s.get(Story, result.story_id)
        if story is None:  # pragma: no cover - flush 后的防御性检查
            s.rollback()
            raise InvalidValue("AUTO materialization 未生成 Story")
        req.ticket_id = story.id
        req.resolved_type = "story"
        req.error = ""
        req.updated_at = utc_now()
        proposal.ticket_type = "story"
        proposal.ticket_id = story.id
        # apply 已写 story_created；与 request 字段同一事务提交。
        _commit(s)
        s.refresh(story)
        s.refresh(proposal)
        s.refresh(req)
    else:
        req.ticket_id = story.id
        req.resolved_type = "story"
        proposal.ticket_type = "story"
        proposal.ticket_id = story.id
        _commit(s)

    # 自动 Proposal 已完成 Grill/人工答疑，不再经过 Story 人工 confirm gate。
    if story.status == "backlog":
        project_service.confirm_story(s, story.id, changed_by=None)
        # Persist the Story/DAG before their identifiers become externally
        # visible through RabbitMQ.
        s.commit()
        mq.publish_workflow_event(
            mq.EVENT_STORY_CONFIRMED, "story", story.id, ref_id=story.epic_id,
        )
        s.refresh(story)

    dispatched: list[int] = []
    deferred: list[int] = []
    for task in work_item_service.list_tasks(s, story_id=story.id, limit=200):
        if task.status not in ("backlog", "todo"):
            continue
        if not work_item_service.get_task_readiness(s, task)["ready"]:
            continue
        if dispatch_implementation_task(s, task.id) is None:
            deferred.append(task.id)
        else:
            dispatched.append(task.id)

    req.status = TICKET_REQUEST_DONE
    req.error = ""
    req.updated_at = utc_now()
    _commit(s)
    s.refresh(req)
    result = _ticket_execute_result(s, req, proposal.id)
    result["dispatched_task_ids"] = dispatched
    result["deferred_task_ids"] = deferred
    return result


def create_proposal(
    s: Session, *, project_id: int, title: str, content: str = "",
    author_id: int | None = None, auto_create_ticket: bool = False,
    target_epic_id: int | None = None,
) -> Proposal:
    """新建需求提案，初始状态 pending（待开始，点击「开始 grill」才入队）。"""
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    if author_id is not None and not s.get(User, author_id):
        raise InvalidValue(f"author {author_id} not found")
    if auto_create_ticket and target_epic_id is None:
        raise InvalidValue("auto_create_ticket=true 时必须指定 target_epic_id")
    if target_epic_id is not None:
        epic = s.get(Epic, target_epic_id)
        if epic is None:
            raise NotFound(f"epic {target_epic_id} not found")
        if epic.project_id != project_id:
            raise InvalidValue(
                f"epic {target_epic_id} 不属于提案所在项目 {project_id}",
            )
    p = Proposal(
        project_id=project_id,
        title=_required(title, "title", 300),
        content=content or "",
        status=ProposalStatus.PENDING.value,
        current_round=0,
        author_id=author_id,
        auto_create_ticket=bool(auto_create_ticket),
        target_epic_id=target_epic_id,
    )
    s.add(p); _commit(s); s.refresh(p); return p




# ---- 同步自 service.py ----
def _cancel_open_ticket_requests(
    s: Session, proposal_id: int, *, reason: str,
) -> None:
    """提案被编辑回退时，取消其未完成的转换请求（pending/processing → failed）。

    2026-08-09 review 修复（中）：防止 agent 用并发修改后的内容生成 ticket。
    """
    for req in (
        s.query(ProposalTicketRequest)
        .filter(
            ProposalTicketRequest.proposal_id == proposal_id,
            ProposalTicketRequest.status.in_(
                (TICKET_REQUEST_PENDING, TICKET_REQUEST_PROCESSING),
            ),
        )
        .all()
    ):
        req.status = TICKET_REQUEST_FAILED
        req.error = (reason or "cancelled")[:2000]
        req.updated_at = utc_now()

# ---- 同步自 service.py ----
def claim_ticket_request(
    s: Session, request_id: int, *, agent: str = "",
) -> ProposalTicketRequest | None:
    """**原子**认领转换请求：pending → processing（worker 竞争消费）。

    条件 UPDATE 由数据库仲裁，恰一个赢家；返回 None 表示竞争失败（已被他人
    认领 / 已完成 / 不存在），调用方据此跳过或 409。
    """
    now = utc_now()
    res = s.execute(
        update(ProposalTicketRequest)
        .where(
            ProposalTicketRequest.id == request_id,
            ProposalTicketRequest.status == TICKET_REQUEST_PENDING,
        )
        .values(
            status=TICKET_REQUEST_PROCESSING,
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    if res.rowcount == 1:
        _commit(s)
        req = s.get(ProposalTicketRequest, request_id)
        s.refresh(req)
        return req
    s.rollback()
    return None

# ---- 同步自 service.py ----
def _ticket_execute_result(
    s: Session, req: ProposalTicketRequest, proposal_id: int,
) -> dict:
    """组装 execute 返回（ticket 实体序列化 + 请求）。"""
    ticket: dict | None = None
    if req.ticket_id is not None:
        effective_type = req.resolved_type or req.type
        if effective_type == "epic":
            ticket = _ser(s.get(Epic, req.ticket_id)) if s.get(Epic, req.ticket_id) else None
        elif effective_type == "story":
            ticket = _ser(s.get(Story, req.ticket_id)) if s.get(Story, req.ticket_id) else None
        else:
            ticket = _ser(s.get(Task, req.ticket_id)) if s.get(Task, req.ticket_id) else None
    return {
        "proposal": _ser(s.get(Proposal, proposal_id)),
        "request": _ser(req),
        "ticket": ticket,
    }

# ---- 同步自 service.py ----
def fail_ticket_request(
    s: Session, request_id: int, *, error: str,
) -> ProposalTicketRequest | None:
    """标记转换请求失败：status → failed，proposal ticket_preparing → converged
    （回退，可重新点击生成）。"""
    req = _ticket_request_or_404(s, request_id)
    if req.status == TICKET_REQUEST_DONE:
        return req  # 已完成不允许改判失败
    req.status = TICKET_REQUEST_FAILED
    req.error = (error or "unspecified failure")[:2000]
    req.updated_at = utc_now()
    p = s.get(Proposal, req.proposal_id)
    if p and ProposalStatus(p.status) is ProposalStatus.TICKET_PREPARING:
        p.status = ProposalStatus.CONVERGED.value
        p.error = ""
        _commit(s)
    else:
        _commit(s)
    s.refresh(req)
    return req

# ---- 同步自 service.py ----
def list_ticket_requests(s: Session, proposal_id: int) -> list[ProposalTicketRequest]:
    """列出提案的全部转换请求（前端轮询生成状态）。"""
    _proposal_or_404(s, proposal_id)
    return (
        s.query(ProposalTicketRequest)
        .filter(ProposalTicketRequest.proposal_id == proposal_id)
        .order_by(ProposalTicketRequest.id.asc())
        .all()
    )

# ---- 同步自 service.py ----
def list_pending_ticket_requests(s: Session, limit: int = 20):
    """Worker 拉取待认领转换请求（status=pending）。"""
    limit = max(1, min(int(limit or 20), 200))
    return (
        s.query(ProposalTicketRequest)
        .filter(ProposalTicketRequest.status == TICKET_REQUEST_PENDING)
        .order_by(ProposalTicketRequest.id.asc())
        .limit(limit)
        .all()
    )

# ---- 同步自 service.py ----
def get_ticket_request(s: Session, request_id: int) -> ProposalTicketRequest | None:
    """按 id 取转换请求（供端点做归属校验）。"""
    return s.get(ProposalTicketRequest, request_id)

# ---- 同步自 service.py ----
def get_ticket_request_project_id(s: Session, request_id: int) -> int | None:
    """按请求反查项目（供项目访问中间件用）。"""
    req = s.get(ProposalTicketRequest, request_id)
    if not req:
        return None
    p = s.get(Proposal, req.proposal_id)
    return p.project_id if p else None

# ---- 同步自 service.py ----
def _ticket_request_or_404(s: Session, request_id: int) -> ProposalTicketRequest:
    r = s.get(ProposalTicketRequest, request_id)
    if not r:
        raise NotFound(f"ticket request {request_id} not found")
    return r

# ---- 同步自 service.py ----
def _sm_failed_effect(s: Session, p: Proposal, ctx: dict) -> None:
    """FAILED：写 error（保留原语义：error 参数优先，其次既有值，兜底固定文案）。"""
    error = ctx.get("error")
    p.error = error or p.error or "unspecified failure"

# ---- 同步自 service.py ----
def _sm_clear_error_effect(s: Session, p: Proposal, ctx: dict) -> None:
    """非 FAILED 且未显式传 error：清空历史错误。"""
    if ctx.get("error") is None:
        p.error = ""

# ---- 同步自 service.py ----
def _sm_success_clear_retry(s: Session, p: Proposal, ctx: dict) -> None:
    """成功终态（收敛/生成工单）清零自动重投计数：agent 已恢复或人工接管。"""
    p.auto_retry_count = 0

# ---- 同步自 service.py ----
def _sm_claim_lease_effect(s: Session, p: Proposal, ctx: dict) -> None:
    """进入 analyzing：盖上租约时间戳（含旧版 PUT /status 认领路径）。"""
    p.claimed_at = utc_now()

# ---- 同步自 service.py ----
def _sm_clear_lease_effect(s: Session, p: Proposal, ctx: dict) -> None:
    """离开 analyzing：清空租约，防止已收敛/失败的提案仍挂着持有者。"""
    p.claimed_by = ""
    p.claimed_at = None

# ---- 同步自 service.py ----
def _sm_apply_side_effects(s: Session, p: Proposal, ctx: dict) -> None:
    """统一副作用分派：按目标状态执行对应注册副作用。"""
    # _SUCCESS_TERMINALS 定义于顶层 service.py（Phase 9 未迁移），延迟导入避免循环
    from ...service import _SUCCESS_TERMINALS
    new = ProposalStatus(p.status)  # StateMachine.execute 已推进 status
    if new is ProposalStatus.FAILED:
        _sm_failed_effect(s, p, ctx)
    elif ctx.get("error") is None:
        _sm_clear_error_effect(s, p, ctx)
    if new in _SUCCESS_TERMINALS and (p.auto_retry_count or 0) > 0:
        _sm_success_clear_retry(s, p, ctx)
    if new is ProposalStatus.ANALYZING:
        _sm_claim_lease_effect(s, p, ctx)
    else:
        _sm_clear_lease_effect(s, p, ctx)

# ---- 同步自 service.py ----
def reclaim_stale_proposals(
    s: Session, *, lease_seconds: int = DEFAULT_CLAIM_LEASE_SECONDS,
) -> list[int]:
    """把租约过期的 analyzing 提案批量回退 queued，返回被回收的 id 列表。

    这是整个自动化闭环唯一的丢单兜底：持有者进程被 kill 后，提案必须能重新入队。

    判定依据是 ``claimed_at`` 而非 ``updated_at``：后者带 onupdate，用户作答、
    PATCH converged_spec 等**与持有者无关**的写入都会刷新它，导致一个早已崩溃的
    Worker 的租约被旁人不断续期，提案永久卡死在 analyzing。
    """
    if lease_seconds < 0:
        raise InvalidValue("lease_seconds must be >= 0")
    now = utc_now()
    cutoff = now - timedelta(seconds=lease_seconds)
    analyzing = ProposalStatus.ANALYZING.value
    # claimed_at 为 NULL 的 analyzing 行只可能来自本迁移之前（历史遗留），
    # 对它们退回用 updated_at 兜底，避免升级后这批行永远无法回收。
    stale = or_(
        Proposal.claimed_at < cutoff,
        and_(Proposal.claimed_at.is_(None), Proposal.updated_at < cutoff),
    )
    ids = [
        row[0]
        for row in s.query(Proposal.id)
        .filter(Proposal.status == analyzing, stale)
        .all()
    ]
    if not ids:
        return []
    s.execute(
        update(Proposal)
        .where(Proposal.id.in_(ids), Proposal.status == analyzing)
        .values(
            status=ProposalStatus.QUEUED.value,
            claimed_by="",
            claimed_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    _commit(s)
    s.expire_all()
    return ids

# ---- 同步自 service.py ----
def recover_failed_proposals(
    s: Session, *, window_seconds: int = 120, max_retries: int = 5,
) -> list[int]:
    """把「Agent 不可用」导致的 failed 提案自动回退 queued 重投（后端 job）。

    与 ``reclaim_stale_proposals``（analyzing 租约超时）互补：
    - reclaim：worker 崩溃后卡在 analyzing → 回退 queued；
    - recover：agent CLI 不可用导致的 failed → 自动重试，前端不做手动 retry。

    规则：
    - 仅处理 error 匹配 AGENT_ERROR_KEYWORDS 的 failed 提案（人工判定失败如
      轮次上限超限 / 用户中止**不**自动重投）；
    - 重投计数用 ``auto_retry_count`` 字段（worker 每次失败会覆盖 error 文本，
      不能编码进 error）；达到 max_retries 停投转人工，避免 agent 永久不可用时
      无限循环；
    - 距上次失败（updated_at）不足 window_seconds 跳过，控制重投频率。
    """
    # AGENT_ERROR_KEYWORDS 定义于顶层 service.py（Phase 9 未迁移），延迟导入避免循环
    from ...service import AGENT_ERROR_KEYWORDS
    if window_seconds < 0:
        raise InvalidValue("window_seconds must be >= 0")
    now = utc_now()
    cutoff = now - timedelta(seconds=window_seconds)
    failed_rows = (
        s.query(Proposal)
        .filter(Proposal.status == ProposalStatus.FAILED.value)
        .all()
    )
    recovered: list[int] = []
    for p in failed_rows:
        err = p.error or ""
        if not any(k in err for k in AGENT_ERROR_KEYWORDS):
            continue
        if (p.auto_retry_count or 0) >= max_retries:
            continue
        if p.updated_at is not None and p.updated_at > cutoff:
            continue
        p.status = ProposalStatus.QUEUED.value
        p.claimed_by = ""
        p.claimed_at = None
        p.auto_retry_count = (p.auto_retry_count or 0) + 1
        p.updated_at = now
        recovered.append(p.id)
    if recovered:
        _commit(s)
    return recovered

# ---- 同步自 service.py ----
def answer_proposal_question(
    s: Session, question_id: int, *, answer: str = "", unsure: bool = False,
    user_id: int | None = None,
) -> ProposalQuestion:
    """用户作答单条问题；``unsure=True`` 表示标记不确定（视为已处理）。"""
    qs = s.get(ProposalQuestion, question_id)
    if not qs:
        raise NotFound(f"proposal question {question_id} not found")
    proposal = _proposal_or_404(s, qs.proposal_id)
    if ProposalStatus(proposal.status) is ProposalStatus.CANCELLED:
        raise InvalidValue(f"proposal {proposal.id} 已取消，不能继续作答")
    answer = (answer or "").strip()
    if not answer and not unsure:
        raise InvalidValue("answer is required unless marked unsure")
    if user_id is not None and not s.get(User, user_id):
        raise InvalidValue(f"user {user_id} not found")
    qs.answer = answer
    qs.unsure = bool(unsure)
    qs.answered_at = utc_now()
    qs.answered_by = user_id
    _commit(s); s.refresh(qs)
    _maybe_mark_answered(s, qs.proposal_id)
    return qs

# ---- 同步自 service.py ----
def _maybe_mark_answered(s: Session, proposal_id: int) -> None:
    """当前轮次问题全部处理完毕时，自动把 awaiting 推进到 answered。"""
    p = s.get(Proposal, proposal_id)
    if not p or ProposalStatus(p.status) is not ProposalStatus.AWAITING:
        return
    r = (
        s.query(ProposalRound)
        .filter(ProposalRound.proposal_id == proposal_id,
                ProposalRound.round_no == p.current_round)
        .first()
    )
    if not r:
        return
    pending = (
        s.query(ProposalQuestion)
        .filter(ProposalQuestion.round_id == r.id,
                ProposalQuestion.answered_at.is_(None))
        .count()
    )
    if pending == 0:
        p.status = ProposalStatus.ANSWERED.value
        _commit(s)

# ---- 同步自 service.py ----
def list_proposal_rounds(s: Session, proposal_id: int) -> list[dict]:
    """按轮次正序返回澄清历史（含每轮问题），供前端问答工作台渲染。"""
    _proposal_or_404(s, proposal_id)
    rounds = (
        s.query(ProposalRound)
        .filter(ProposalRound.proposal_id == proposal_id)
        .order_by(ProposalRound.round_no.asc())
        .all()
    )
    out = []
    for r in rounds:
        qs = (
            s.query(ProposalQuestion)
            .filter(ProposalQuestion.round_id == r.id)
            .order_by(ProposalQuestion.seq.asc(), ProposalQuestion.id.asc())
            .all()
        )
        item = _ser(r)
        # 新记录存 agent_id；历史记录可能是 worker 服务账号或空值。
        # 能匹配 Agent 注册表时同时返回可读名称，前端仍保留 id 便于审计。
        from ..projects.models import Agent
        agent_row = (
            s.query(Agent).filter(Agent.agent_id == r.agent).first()
            if (r.agent or "").strip() else None
        )
        item["agent_name"] = agent_row.name if agent_row else ""
        item["questions"] = [_ser(x) for x in qs]
        out.append(item)
    return out

# ---- 同步自 service.py ----
def convert_proposal_to_story(
    s: Session, proposal_id: int, *, epic_id: int, title: str | None = None,
) -> tuple[Story, list[Task], Proposal]:
    """人工终审确认后，把已收敛提案转化为 Story + 子 Task（Epic 96 P3）。

    Review 2026-08-26 P1/P2 #5 修复：此函数变 thin facade，业务全部走
    ``ProposalConversionService.plan + validate + apply``：

    - 要求提案状态为 converged，且 converged_spec 非空（否则 400/422 拒绝）；
    - 要求目标 Epic 存在且属于提案所在项目；
    - 幂等防重放：story_id 已回填且 Story 仍存在时直接返回既有结果。

    返回 ``(story, tasks, proposal)``。
    """
    from .conversion_service import ProposalConversionService
    p = _proposal_or_404(s, proposal_id)

    # 幂等：已转化过且 Story 还在 → 直接复用，避免重放产生重复 Story。
    if p.story_id is not None:
        existing = s.get(Story, p.story_id)
        if existing is not None:
            tasks = (
                s.query(Task).filter(Task.story_id == existing.id).all()
            )
            return existing, tasks, p

    if ProposalStatus(p.status) is not ProposalStatus.CONVERGED:
        raise InvalidValue(
            f"proposal {proposal_id} 当前状态为 {p.status}，仅 converged 可转化为 Story",
        )
    if not (p.converged_spec or "").strip():
        raise InvalidValue(
            f"proposal {proposal_id} 的 converged_spec 为空，无法生成 Story",
        )

    epic = s.get(Epic, epic_id)
    if epic is None:
        raise NotFound(f"epic {epic_id} not found")
    if epic.project_id != p.project_id:
        raise InvalidValue(
            f"epic {epic_id} 不属于提案所在项目 {p.project_id}",
        )

    # 委托 ProposalConversionService 三阶段：
    # plan() 从 spec 推演 tasks + dependencies（含 design / dev / qa）
    # validate() 校验完整性
    # apply() 事务性落库（Review 2026-08-26 P1 #2：单 transaction commit，
    # 杜绝"Story 已建 / Tasks 未建"的孤儿数据）
    plan = ProposalConversionService.plan(p, epic_id=epic_id)
    # 如果 caller 显式传了 title，覆盖 plan.story.title
    if title is not None:
        plan.story = {**(plan.story or {}), "title": title}
    ProposalConversionService.validate(plan, project_id=p.project_id)
    ProposalConversionService.apply(s, plan, p)

    story = s.get(Story, p.story_id)
    # Review 2026-08-26 P1/P2 #5 备注：facade return signature 与原实现保持兼容。
    # 实际 conversion 路径只返回 plan 推演的 spec-driven dev tasks（不含
    # create_story 自动创的 design / default dev），跟原 convert_proposal_to_story
    # 的 ``return story, created, p`` 一致；幂等路径 return Story 下所有 task
    # 保持不变（fixme: 这两条路径 return 内容不一致，是 P1 现场，Phase 2 收敛）
    if plan.tasks:
        # 从 plan 的 spec-driven dev task titles 反查 DB id
        spec_dev_titles = {
            t["title"] for t in plan.tasks
            if t.get("type") == ItemType.DEV.value
            and t["title"] != f"实现：{p.title}"  # 不含 default dev
        }
        all_tasks = s.query(Task).filter(Task.story_id == story.id).all()
        tasks = [t for t in all_tasks if t.title in spec_dev_titles]
    else:
        tasks = []
    return story, tasks, p


def build_proposal_task_graph(s: Session, proposal_id: int) -> dict:
    """Build a planned (推演) DAG representation for a Proposal.

    Review 2026-08-26 P1 #3：明确标"planned"语义。
    此函数**不查 DB Task / TaskDependency**，仅基于 converged_spec 解析
    ``- [ ]`` 清单项推演"如果按 spec 转换会得到什么 DAG"。返回的节点
    id 是虚拟前缀（design-1 / dev-N / qa-1），不是真实 DB id。

    对应 endpoint：``GET /api/proposals/{pid}/task-graph/planned``。
    真实 DB DAG（persisted）见 ``get_persisted_task_graph``。
    """
    p = _proposal_or_404(s, proposal_id)
    spec = p.converged_spec or p.content or ""
    tasks: list[dict] = []
    seen: set[str] = set()
    for line in spec.splitlines():
        m = _SPEC_TASK_RE.match(line)
        if m:
            title = m.group(1).strip()
            if title and title not in seen:
                seen.add(title)
                tasks.append({"id": f"dev-{len(tasks) + 1}", "type": "dev", "title": title})

    if not tasks:
        tasks = [{"id": "dev-1", "type": "dev", "title": f"实现：{p.title}"}]

    nodes = [
        {"id": "design-1", "type": "design", "title": f"设计：{p.title}"},
        *tasks,
        {"id": "qa-1", "type": "qa", "title": f"QA验收：{p.title}"},
    ]

    edges = []
    for t in tasks:
        edges.append({"source": "design-1", "target": t["id"], "type": "blocks"})
        edges.append({"source": t["id"], "target": "qa-1", "type": "blocks"})

    return {
        "proposal_id": proposal_id,
        "title": p.title,
        "planned": True,
        "nodes": nodes,
        "edges": edges,
    }


def get_persisted_task_graph(s: Session, proposal_id: int) -> dict:
    """Build the **真实** DB DAG for a Proposal。

    Review 2026-08-26 P1 #3：原 ``build_proposal_task_graph`` 总是返回推演图，
    不查 DB Task / TaskDependency。导致：
    - 转换前 / 转换中：推演图无真实 id，UI 看到 design-1 / dev-N / qa-1 节点
    - 转换后：实际 DB 可能没 qa-1（conversion 不创建 QA task），但推演图永远画 qa-1
    → UI 显示与真实执行图不一致（"source-of-truth 分裂"）。

    修法：本函数直接查 proposal.story_id → Story → Task + TaskDependency，
    返回真实 DB DAG（节点 id 是真实 Task.id，edge 是 TaskDependency）。
    proposal 未转换时返回空图 + planned 提示字段。

    对应 endpoint：``GET /api/proposals/{pid}/task-graph``。
    """
    p = _proposal_or_404(s, proposal_id)
    if p.story_id is None:
        return {
            "proposal_id": proposal_id,
            "title": p.title,
            "planned": False,
            "persisted": False,
            "nodes": [],
            "edges": [],
            "message": "proposal 尚未转化（story_id 为空），请先 POST /convert",
        }
    story = s.get(Story, p.story_id)
    if story is None:
        raise NotFound(f"proposal {proposal_id} 关联的 story {p.story_id} 不存在")
    # 查真实 Task
    tasks = s.query(Task).filter(Task.story_id == story.id).order_by(Task.id.asc()).all()
    # 查真实 TaskDependency
    task_ids = {t.id for t in tasks}
    deps = []
    if task_ids:
        deps = (
            s.query(TaskDependency)
            .filter(TaskDependency.task_id.in_(task_ids))
            .filter(TaskDependency.dependency_type == "blocks")
            .all()
        )
    nodes = [
        {
            "id": t.id,
            "type": t.type,
            "title": t.title,
            "status": t.status,
            "assignee_id": t.assignee_id,
        }
        for t in tasks
    ]
    edges = [
        {
            "source": d.depends_on_id,
            "target": d.task_id,
            "type": d.dependency_type,
        }
        for d in deps
        if d.depends_on_id in task_ids
    ]
    return {
        "proposal_id": proposal_id,
        "title": p.title,
        "story_id": story.id,
        "planned": False,
        "persisted": True,
        "nodes": nodes,
        "edges": edges,
    }

# ---- 同步自 service.py ----
def update_proposal(s: Session, id: int, **fields) -> Proposal | None:
    """编辑提案正文（状态流转请用 set_proposal_status）。

    用户编辑 title/content 时，若提案处于澄清流（queued/analyzing/awaiting/
    answered/converged），**回退 pending**（待开始）——编辑后需重新点击
    「开始 grill」才重新入队；已答历史保留（全量重放不丢上下文）。
    worker 写入 converged_spec / 回填 story_id 等**非用户编辑**字段不回退。
    """
    p = s.get(Proposal, id)
    if not p:
        return None
    if ProposalStatus(p.status) is ProposalStatus.CANCELLED:
        raise InvalidValue(f"proposal {id} 已取消，不能继续修改")
    # auto_create_ticket 状态边界（Story 389）：收敛及建单阶段后锁定，
    # 服务端拒绝（422）；draft/pending/queued/analyzing/awaiting/answered/failed
    # 均可反复修改。
    if (
        (fields.get("auto_create_ticket") is not None
         or fields.get("target_epic_id") is not None)
        and ProposalStatus(p.status) not in AUTO_TICKET_MODIFIABLE_STATUSES
    ):
        raise InvalidValue(
            f"proposal {id} 当前状态为 {p.status}，"
            f"auto_create_ticket / target_epic_id 仅在收敛前可修改",
        )
    allowed = {
        "title", "content", "converged_spec", "story_id",
        "auto_create_ticket", "target_epic_id",
    }
    edited_user_fields = False
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "title":
            v = _required(v, "title", 300)
        elif k == "story_id" and not s.get(Story, v):
            raise NotFound(f"story {v} not found")
        elif k == "auto_create_ticket":
            v = bool(v)
        elif k == "target_epic_id":
            epic = s.get(Epic, v)
            if epic is None:
                raise NotFound(f"epic {v} not found")
            if epic.project_id != p.project_id:
                raise InvalidValue(
                    f"epic {v} 不属于提案所在项目 {p.project_id}",
                )
        if k in ("title", "content"):
            edited_user_fields = True
        setattr(p, k, v)
    if p.auto_create_ticket and p.target_epic_id is None:
        raise InvalidValue("auto_create_ticket=true 时必须指定 target_epic_id")
    # 编辑回退：澄清流状态 → pending（清租约，等「开始 grill」重新入队）
    # 2026-08-09 review 修复：ticket_preparing（生成中）编辑同样回退，
    # 并把该提案未完成的转换请求置 failed——防止 agent 用并发修改后的
    # 内容生成 ticket。
    if edited_user_fields and p.status in (
        ProposalStatus.QUEUED.value, ProposalStatus.ANALYZING.value,
        ProposalStatus.AWAITING.value, ProposalStatus.ANSWERED.value,
        ProposalStatus.CONVERGED.value, ProposalStatus.TICKET_PREPARING.value,
    ):
        was_ticket_preparing = p.status == ProposalStatus.TICKET_PREPARING.value
        p.status = ProposalStatus.PENDING.value
        if was_ticket_preparing:
            _cancel_open_ticket_requests(s, id, reason="提案被编辑，生成已取消")
        p.claimed_by = ""
        p.claimed_at = None
        p.claimed_by = ""
        p.claimed_at = None
    _commit(s); s.refresh(p); return p

# ---- 同步自 service.py ----
def delete_proposal(s: Session, id: int) -> bool:
    p = s.get(Proposal, id)
    if not p:
        return False
    # 显式清理子表（外键 ondelete=CASCADE 也会兜底；SQLite 默认不强制外键）
    s.query(ProposalQuestion).filter(ProposalQuestion.proposal_id == id).delete(
        synchronize_session=False,
    )
    s.query(ProposalRound).filter(ProposalRound.proposal_id == id).delete(
        synchronize_session=False,
    )
    s.delete(p); _commit(s); return True
