"""Generic StateMachine base + TransitionSpec。

把 ``domains/proposals/state_machine.py`` 的设计抽象成通用基类,所有 feature
(Task/Story/Proposal/Document) 的状态机继承这个基类,统一行为、统一可观测性。

设计要点:
- ``TransitionSpec`` = (from, to, side_effects, validators)
- ``StateMachine.execute()`` 统一:commit 前跑 validators → 跑 side_effects → 状态变更 → commit
- ``illegal_transition`` 自动抛 ``IllegalTransition``(不写 if/else 散在 service)
- 每个 transition 可独立注册 metrics / log,默认全开
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

from sqlalchemy.orm import Session

from .exceptions import DomainError, IllegalTransition

log = logging.getLogger("agentboard.state_machine")

E = TypeVar("E")  # 实体类型(Task / Proposal / Story / ...)


# ---- 副作用与校验器签名 ------------------------------------------------------

SideEffect = Callable[[Session, E, dict[str, Any]], None]
"""``(session, entity, ctx) -> None``。状态变更后执行(如发 MQ、写审计)。"""

Validator = Callable[[Session, E, str], str | None]
"""``(session, entity, to_state) -> error_msg | None``。返回 None 表示通过。"""


# ---- 转移规格 ----------------------------------------------------------------

@dataclass(frozen=True)
class TransitionSpec:
    """一个状态转移的完整规格。

    Attributes:
        name: 转移名(用于 metric / log)
        from_state: 起始状态
        to_state: 目标状态
        side_effects: 提交前依次执行的副作用
        validators: 提交前依次执行的校验器(返回错误信息则阻断)
        ctx_keys: 该转移需要从 ctx 读取哪些字段(契约声明)
    """

    name: str
    from_state: str
    to_state: str
    side_effects: tuple[SideEffect, ...] = field(default_factory=tuple)
    validators: tuple[Validator, ...] = field(default_factory=tuple)
    ctx_keys: frozenset[str] = field(default_factory=frozenset)

    def can_execute(self, s: Session, entity: E) -> str | None:
        for v in self.validators:
            err = v(s, entity, self.to_state)
            if err:
                return err
        return None


# ---- 状态机基类 --------------------------------------------------------------

class StateMachine(Generic[E]):
    """通用状态机基类。

    子类声明 ``_transitions: dict[(from, to), TransitionSpec]``,通过 ``execute()``
    统一执行。例::

        class TaskStateMachine(StateMachine):
            def __init__(self):
                self._transitions = {
                    ("todo", "in_progress"): TransitionSpec(
                        name="start", from_state="todo", to_state="in_progress",
                        side_effects=(clear_status_reason,),
                    ),
                    ...
                }

            def get_state(self, task) -> str:
                return task.status

            def set_state(self, task, to: str) -> None:
                task.status = to
    """

    # 必须由子类填充:``(from, to) -> TransitionSpec``
    _transitions: dict[tuple[str, str], TransitionSpec] = {}

    # 必须由子类实现
    def get_state(self, entity: E) -> str:  # pragma: no cover
        raise NotImplementedError

    def set_state(self, entity: E, to: str) -> None:  # pragma: no cover
        raise NotImplementedError

    # ---- 公共 API -----------------------------------------------------------

    def can_transition(self, entity: E, to: str) -> bool:
        return (self.get_state(entity), to) in self._transitions

    def allowed_targets(self, entity: E) -> set[str]:
        return {spec.to_state for spec in self._transitions.values()
                if spec.from_state == self.get_state(entity)}

    def execute(
        self,
        s: Session,
        entity: E,
        to: str,
        *,
        ctx: dict[str, Any] | None = None,
    ) -> E:
        """执行状态转移。统一流程:查 spec → 校验 → side effect → set_state。

        Args:
            s: SQLAlchemy session(本函数不 commit,由调用方/UoW 决定)
            entity: 状态机操作的目标实体
            to: 目标状态
            ctx: 透传给 side_effect / validator 的上下文(如 reason / actor)

        Returns:
            更新后的 entity(同一引用)

        Raises:
            IllegalTransition: 起始/目标状态不合法
            DomainError: validator 返回错误(由 validator 自己决定抛哪种)
        """
        ctx = ctx or {}
        from_state = self.get_state(entity)
        spec = self._transitions.get((from_state, to))
        if spec is None:
            raise IllegalTransition(
                f"{type(entity).__name__}: {from_state} → {to} is not allowed",
                details={"from": from_state, "to": to, "entity_id": getattr(entity, "id", None)},
            )

        # 1) validators
        # 校验器可以返回错误字符串(包装为 IllegalTransition)或直接抛领域异常
        # (如 InvalidValue)。后者保留原始 exception type,便于上层精确捕获。
        for validator in spec.validators:
            try:
                err = validator(s, entity, to)
            except DomainError:
                raise  # 让领域异常原样传播
            if err:
                raise IllegalTransition(err, details={"transition": spec.name, "from": from_state, "to": to})

        # 2) side effects(状态变更前)
        for fx in spec.side_effects:
            fx(s, entity, ctx)

        # 3) 状态变更
        self.set_state(entity, to)

        # 4) 观测
        log.info("state_transition", extra={
            "entity": type(entity).__name__,
            "entity_id": getattr(entity, "id", None),
            "from": from_state,
            "to": to,
            "transition": spec.name,
        })

        return entity


def bind_side_effects(*fxs: SideEffect) -> tuple[SideEffect, ...]:
    """辅助构造 side_effects 元组,使 dataclass(frozen=True) 接受 list 字面量。"""
    return fxs
