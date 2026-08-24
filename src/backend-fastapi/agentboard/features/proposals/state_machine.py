"""Proposal 状态机一等公民（Epic 123 Step 3 · Story 239）。

把散落在 service.py 5+ 个函数里的状态迁移业务副作用（发 MQ、回填
ticket_type、清租约、写 claimed_at）绑定到迁移边上，取代：
``set_proposal_status`` / ``claim_proposal`` / ``create_ticket_request`` /
``execute_ticket_request`` / ``_cancel_open_ticket_requests`` /
``reclaim_stale_ticket_requests`` 中的手写迁移逻辑。

设计（最小化抽象，拒绝命令模式/事件溯源等大词）：
- ``ProposalStateMachine``：transitions 定义 + can_transition + execute；
- ``TransitionSpec``：side_effects（(Session, Proposal, ctx) -> None）+ validators；
- 副作用以 tuple 注册顺序执行；commit 由 execute() 统一管理。

兼容性：新增模块纯增量；service.py 公开函数签名不变，仅内部委托。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from sqlalchemy.orm import Session

from . import models as _models
from .models import Proposal, ProposalStatus

# 副作用签名：(s, proposal, ctx) -> None
SideEffect = Callable[[Session, Proposal, dict], None]
# 校验器签名：(s, proposal) -> str | None（返回错误信息或 None）
Validator = Callable[[Session, Proposal], str | None]


@dataclass(frozen=True)
class TransitionSpec:
    """一条迁移边的副作用与校验器。"""

    side_effects: tuple[SideEffect, ...] = ()
    validators: tuple[Validator, ...] = ()


# ---- 复用既有业务函数的副作用注册（service.py 通过 __init__ 注入） ----
# 为避免 state_machine.py 反向 import service.py（循环依赖），副作用在此注册，
# 由 service.py 在模块加载后调用 ``bind_side_effects()`` 填充。

_SIDE_EFFECTS: dict[ProposalStatus, TransitionSpec] = {}


def bind_side_effects(specs: dict[ProposalStatus, TransitionSpec]) -> None:
    """service.py 模块加载后调用：把业务副作用绑定到状态机。"""
    _SIDE_EFFECTS.clear()
    _SIDE_EFFECTS.update(specs)


def _spec_for(new: ProposalStatus | None) -> TransitionSpec:
    if new is None:
        return TransitionSpec()
    return _SIDE_EFFECTS.get(new, TransitionSpec())


class ProposalStateMachine:
    """Proposal 状态机：定义 + 副作用绑定 + 校验一体化。

    **动态读取** ``models.PROPOSAL_TRANSITIONS``（唯一事实源）：加新状态只需
    扩展该字典 + 可选注册副作用，Service 层无需改动（Step 3 验收标准 1）。
    副作用按目标状态注册在 ``_SIDE_EFFECTS``（bind_side_effects 注入）。
    """

    def can_transition(self, from_: ProposalStatus, to: ProposalStatus) -> bool:
        target = to.value if isinstance(to, ProposalStatus) else str(to)
        return target in _models.PROPOSAL_TRANSITIONS.get(from_, set())

    def execute(self, s: Session, proposal: Proposal, to: ProposalStatus,
                *, side_effect_ctx: dict | None = None) -> Proposal:
        """执行状态迁移：校验 + 副作用 + 推进 + commit。

        - ``to`` 兼容枚举与字符串（新状态字符串无需入枚举即可流转）；
        - 同状态迁移视为幂等 no-op（与既有 set_proposal_status 语义一致）；
        - 非法迁移抛 ``IllegalTransitionError``（service 层可捕获转 HTTP 400）。
        """
        target = to.value if isinstance(to, ProposalStatus) else str(to)
        try:
            from_ = ProposalStatus(proposal.status)
        except ValueError:
            from_ = None  # 新状态字符串（未入枚举）作为源态：仅支持字符串迁移
        if from_ is not None:
            transitions = _models.PROPOSAL_TRANSITIONS.get(from_, set())
            if from_.value != target and target not in transitions:
                raise IllegalTransitionError(
                    f"{proposal.status} -> {target} 不合法",
                )
        elif proposal.status != target and target not in _models.PROPOSAL_TRANSITIONS.get(proposal.status, set()):
            raise IllegalTransitionError(
                f"{proposal.status} -> {target} 不合法",
            )
        try:
            enum_target = ProposalStatus(target)
        except ValueError:
            enum_target = None  # 新状态字符串未入枚举：走空副作用
        spec = _spec_for(enum_target)
        for validate in spec.validators:
            err = validate(s, proposal)
            if err:
                raise TransitionValidationError(err)
        if proposal.status != target:
            proposal.status = target
        # 副作用在状态推进后执行：副作用函数读 proposal.status 即目标状态，
        # 与既有 set_proposal_status 的「按新状态维护 error/租约」语义一致。
        for effect in spec.side_effects:
            effect(s, proposal, side_effect_ctx or {})
        return proposal


class IllegalTransitionError(Exception):
    """非法状态迁移（与 service.IllegalTransition 语义等价，服务层转换）。"""


class TransitionValidationError(Exception):
    """迁移前校验失败（服务层转换 HTTP 400）。"""
