"""Proposals service:Proposal / ProposalRound / ProposalQuestion / TicketRequest。

Phase 4 第四段:从 service.py 拆出。set_proposal_status 走 ProposalStateMachine
(已存在于 features/proposals/state_machine.py)。复杂 review 闭环留 service.py
后续批次。

老 import 路径兼容:service.py 末尾重绑到本模块。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ... import models  # 顶层 facade
from ...core.exceptions import Conflict, InvalidValue, NotFound
from ...core.service_helpers import _commit, _invalidate_project_stats_cache, _paginate, _required
from .models import (
    Proposal, ProposalQuestion, ProposalRound, ProposalTicketRequest, ProposalStatus,
)
from ..projects.models import Project
from ..identity.models import User
from .state_machine import ProposalStateMachine
from . import ticket_ref

log = logging.getLogger("agentboard.features.proposals.service")

# Proposal claim 租约默认 30 分钟(防止 Worker crash 后永久占住)
DEFAULT_CLAIM_LEASE_SECONDS = 1800


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
    for rid in stale_ids:
        fail_ticket_request(s, rid, error=f"处理超时（>{lease_seconds}s），自动回退")
    return stale_ids



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


def claim_proposal(s: Session, id: int, *, agent: str = "") -> Proposal | None:
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
    res = s.execute(
        update(Proposal)
        .where(Proposal.id == id, Proposal.status.in_(claimable))
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
    _check_ticket_type(type)
    p = _proposal_or_404(s, proposal_id)
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
        parent_epic_id=epic_id if type != "epic" else None,
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

    if request_id is not None:
        req = _ticket_request_or_404(s, request_id)
        if req.proposal_id != proposal_id:
            raise NotFound(
                f"ticket request {request_id} 不属于 proposal {proposal_id}",
            )
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

    if ProposalStatus(p.status) is not ProposalStatus.TICKET_PREPARING:
        # 兜底：若 proposal 未在 ticket_preparing（例如创建请求时已置位），
        # 此处校验状态机合法迁移，避免竞态下跳过中间态。
        raise InvalidValue(
            f"proposal {proposal_id} 当前状态 {p.status}，"
            f"仅 ticket_preparing 可执行转换",
        )

    # ---- 创建实体（本事务内完成，杜绝部分成功；Step 3 委托 TicketRef）----
    from .domains.proposals.ticket_ref import TicketRef
    try:
        ref = TicketRef.create(
            s, p, type=type,
            parent_epic_id=req.parent_epic_id,
            parent_story_id=req.parent_story_id,
            title=title,
        )
    except ValueError as e:
        raise InvalidValue(str(e)) from None
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



def create_proposal(
    s: Session, *, project_id: int, title: str, content: str = "",
    author_id: int | None = None,
) -> Proposal:
    """新建需求提案，初始状态 pending（待开始，点击「开始 grill」才入队）。"""
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    if author_id is not None and not s.get(User, author_id):
        raise InvalidValue(f"author {author_id} not found")
    p = Proposal(
        project_id=project_id,
        title=_required(title, "title", 300),
        content=content or "",
        status=ProposalStatus.PENDING.value,
        current_round=0,
        author_id=author_id,
    )
    s.add(p); _commit(s); s.refresh(p); return p


