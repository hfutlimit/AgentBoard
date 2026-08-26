"""Task 状态机一等公民(Story 265 重构后)。

把 service.py 内联的 ``set_status`` / ``TRANSITIONS`` / ``_validate_status_reason``
集中到本模块,以 ``core.state_machine.StateMachine[Task]`` 为基类,迁移边用
``TransitionSpec`` 描述 side effects 与 validators。

设计要点:
- 5 状态集:todo / in_progress / in_review / done / blocked
- blocked 全向可达(任意非终态 → blocked)
- 解除 blocked 优先恢复到 ``previous_status``
- re-open: done → in_progress 时自动清空 status_reason
- status_reason 校验:done/blocked 必填,其他状态自动清空
- 每次状态变更:写 TaskStatusHistory + 失效项目统计缓存
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from ...core.common.enums import (
    STATUS_REASONS_BY_STATUS, Status, StatusReason,
)
from ...core.common.models import utc_now
from ...core.exceptions import InvalidValue
from ...core.state_machine import StateMachine, TransitionSpec

if TYPE_CHECKING:
    from .models import Task, TaskStatusHistory

log = logging.getLogger("agentboard.features.work_items.state_machine")


# ---------------------------------------------------------------------------
# 迁移表(同 service.py TRANSITIONS 语义)
# ---------------------------------------------------------------------------

# blocked 在主表外独立处理(全向可达),所以从主表去掉;其他 4 个状态互转 + 进 blocked
#
# Review 2026-08-26 P1 #3：删除 ``{todo, in_progress} → done`` 两条边。
# AgentBoard workflow contract 要求：
#   Agent 自动流程：todo → in_progress → in_review → done（必经 Review）
#   Admin 强制完成：必须走 ``force_complete_task`` 显式命令（status_reason="manual_override"）
# 通用 set_status(DONE) 路径不再接受从 todo / in_progress 直接跳到 done。
_TASK_TRANSITIONS: dict[Status, set[Status]] = {
    Status.TODO:        {Status.IN_PROGRESS, Status.BLOCKED},
    Status.IN_PROGRESS: {Status.IN_REVIEW, Status.TODO, Status.BLOCKED},
    Status.IN_REVIEW:   {Status.DONE, Status.IN_PROGRESS, Status.BLOCKED},
    Status.DONE:        {Status.IN_PROGRESS, Status.BLOCKED},
    # BLOCKED 的合法目标在 execute() 里动态计算(全向 + previous_status 特例)
}


# ---------------------------------------------------------------------------
# 副作用:写历史 + 失效缓存 + previous_status 维护
# 注:core.state_machine.execute() 调 side effect 签名是 ``(s, entity, ctx)``,
# 所以 old_status 通过 ``entity.status`` 在副作用里读(SM 保证 side effect 在
# set_state 之前跑,此时 entity.status 还是旧值)。
# ---------------------------------------------------------------------------


def _record_status_history(s: Session, t: "Task", ctx: dict) -> None:
    """写一条 TaskStatusHistory(在 set_state 前调,entity.status 是旧值)。"""
    from .models import TaskStatusHistory  # 延迟 import,避开循环
    s.add(TaskStatusHistory(
        task_id=t.id,
        from_status=t.status,  # 此时 SM 还没 set_state,所以还是旧值
        to_status=ctx.get("_to", ""),
        changed_by=ctx.get("changed_by"),
        reason=ctx.get("reason", "") or "",
    ))


def _invalidate_project_stats(s: Session, t: "Task", ctx: dict) -> None:
    # 失败也无所谓:缓存项可能不存在
    from ...core.infrastructure.cache import get_cache  # 延迟
    get_cache().invalidate_prefix(f"project_stats:{t.project_id}")


def _save_previous_status_on_block(s: Session, t: "Task", ctx: dict) -> None:
    t.previous_status = t.status  # 此时还是旧值


def _clear_previous_status_on_unblock(s: Session, t: "Task", ctx: dict) -> None:
    t.previous_status = None


def _apply_status_reason(s: Session, t: "Task", ctx: dict) -> None:
    """根据目标状态规范化 status_reason(在 set_state 前调,此时 entity.status 还没变)。

    - new not in STATUS_REASONS_BY_STATUS → 清空(None)
    - new in STATUS_REASONS_BY_STATUS → 保持当前值(validator 已保证合法)
    """
    to = ctx.get("_to", "")
    if STATUS_REASONS_BY_STATUS.get(to) is None:
        t.status_reason = None


# ---------------------------------------------------------------------------
# 校验器:status_reason 合法性
# ---------------------------------------------------------------------------

def _validate_status_reason(s: Session, t: "Task", to: str) -> None:
    """done / blocked 必填且必须合法;其他状态不校验。

    校验失败直接抛 ``InvalidValue``(语义:请求参数非法,不是状态机迁移非法)。
    core.state_machine.execute 会让领域异常原样传播。
    """
    new = Status(to)
    allowed = STATUS_REASONS_BY_STATUS.get(str(new))
    if allowed is None:
        return  # 非 done/blocked → OK,_apply_status_reason 会清空
    cur = t.status_reason
    if not cur:
        raise InvalidValue(
            f"status_reason is required for status={new}; "
            f"allowed: {sorted(allowed)}"
        )
    if cur not in allowed:
        raise InvalidValue(
            f"invalid status_reason '{cur}' for status={new}; "
            f"allowed: {sorted(allowed)}"
        )


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------

class TaskStateMachine(StateMachine["Task"]):
    """Task 状态机。

    5 状态集(Story 265):
        todo → in_progress, blocked
        in_progress → in_review, todo, blocked
        in_review → done, in_progress, blocked
        done → in_progress, blocked
        blocked 全向可达(任意非终态 → blocked);
        解除 blocked 可达 {todo, in_progress, in_review, done} 全 4 态,
        推荐默认回 previous_status(写入 task.previous_status 字段供 UI 显示),
        但不强制约束 — 用户按实际原因选目标态。

    每次迁移自动:
        1. 校验 status_reason(done/blocked 必填)
        2. 写 TaskStatusHistory
        3. 维护 previous_status(进 blocked 记,出 blocked 清)
        4. 失效项目统计缓存

    使用::
        sm = TaskStateMachine()
        sm.execute(s, task, "in_progress", ctx={"changed_by": uid, "reason": "start"})
    """

    def __init__(self) -> None:
        # 构建 (from, to) -> TransitionSpec 表
        self._transitions: dict[tuple[str, str], TransitionSpec] = {}
        for from_, targets in _TASK_TRANSITIONS.items():
            for to in targets:
                if to == Status.BLOCKED:
                    # 进入 blocked:保存 previous_status + 写 history
                    self._transitions[(from_.value, to.value)] = TransitionSpec(
                        name=f"{from_.value}_to_blocked",
                        from_state=from_.value,
                        to_state=to.value,
                        side_effects=(
                            _save_previous_status_on_block,
                            _record_status_history,
                        ),
                    )
                else:
                    self._transitions[(from_.value, to.value)] = TransitionSpec(
                        name=f"{from_.value}_to_{to.value}",
                        from_state=from_.value,
                        to_state=to.value,
                        side_effects=(_record_status_history,),
                    )

        # 解除 blocked:根据 previous_status 决定有效目标
        # 这里穷举 4 个可能:prev = todo / in_progress / in_review / done
        for prev in (Status.TODO, Status.IN_PROGRESS, Status.IN_REVIEW, Status.DONE):
            self._transitions[(Status.BLOCKED.value, prev.value)] = TransitionSpec(
                name=f"unblock_to_{prev.value}",
                from_state=Status.BLOCKED.value,
                to_state=prev.value,
                side_effects=(
                    _clear_previous_status_on_unblock,
                    _record_status_history,
                ),
            )

        # 通用 side effect:写 status_reason + 失效缓存(对所有迁移都跑)
        for key in self._transitions:
            existing = self._transitions[key]
            # dataclass(frozen=True) → 重新构造
            self._transitions[key] = TransitionSpec(
                name=existing.name,
                from_state=existing.from_state,
                to_state=existing.to_state,
                side_effects=existing.side_effects + (_apply_status_reason, _invalidate_project_stats),
                validators=(_validate_status_reason,),
                ctx_keys=frozenset({"changed_by", "reason"}),
            )

    # ---- 抽象方法实现 -------------------------------------------------------

    def get_state(self, entity: "Task") -> str:
        return entity.status

    def set_state(self, entity: "Task", to: str) -> None:
        entity.status = to
        # status_reason 由 side effect _apply_status_reason 处理


# ---- 便捷函数(向后兼容,老代码可直接调) ------------------------------------

# 预编译 side effect context 工厂
def make_status_change_ctx(*, to: str, changed_by: int | None = None, reason: str = "") -> dict:
    return {"_to": to, "changed_by": changed_by, "reason": reason}


# 模块级单例(无状态,共享)
_task_sm = TaskStateMachine()


def execute_transition(
    s: Session,
    task: "Task",
    to: str,
    *,
    changed_by: int | None = None,
    reason: str = "",
    force: bool = False,
) -> "Task":
    """执行 Task 状态迁移(对外入口)。

    校验失败抛 ``IllegalTransition`` / ``InvalidValue``(core.exceptions)。
    业务副作用(历史/缓存/previous_status)由 TransitionSpec 自动跑。

    Review 2026-08-26 P1 #3：``force=True`` 旁路 transition 表，仅供
    ``force_complete_task``（admin 显式 exceptional path）使用：
    - 仍跑 status_reason validator（拒绝非法 reason）
    - 仍跑所有 side effects（history / cache invalidation / status_reason normalize）
    - 但跳过 transition 表查询（允许 ``todo`` / ``in_progress`` / ``blocked`` → ``done``）
    - 通用 caller 不应使用 force=True，必须从 ``set_status`` 走（普通 path 校验更严）

    blocked 解除:代码层 4 目标都允许(todo/in_progress/in_review/done),
    UI 层可推荐回 previous_status(task.previous_status 字段已写入),
    但 state machine 不强制约束。
    """
    from ...core.exceptions import IllegalTransition as _IT  # 延迟
    # blocked 全向可达:动态加 spec
    if _task_sm.get_state(task) == Status.BLOCKED.value and to != Status.BLOCKED.value:
        # 解除 blocked:已经预注册 4 个目标(见 __init__),如果不在表里就报错
        spec_key = (Status.BLOCKED.value, to)
        if spec_key not in _task_sm._transitions:
            raise _IT(
                f"Task cannot transition from {task.status} to {to} "
                f"(allowed unblock targets: todo, in_progress, in_review, done)",
                details={"from": task.status, "to": to,
                         "previous_status": task.previous_status,
                         "allowed": ["todo", "in_progress", "in_review", "done"]},
            )
    if force:
        # force path：绕过 transition 表，但仍走完整 validator + side effects。
        # 仅 admin 强制完成场景用（见 features/work_items/service.py::force_complete_task）。
        # 我们手动复制 _task_sm.execute 里的 validator + side effect 链。
        from ...core.state_machine import StateMachine as _SM
        from ...core.exceptions import DomainError as _DE
        # 1. validator：调 _validate_status_reason（如果 target 是 done/blocked）
        if to in (Status.DONE.value, Status.BLOCKED.value):
            _validate_status_reason(s, task, to)
        # 2. side effects：force 路径不依赖 spec，自己跑全链
        #    （record history + apply status_reason + invalidate cache + previous_status 维护）
        if to == Status.BLOCKED.value:
            # 进 blocked 路径：保存 previous_status
            _save_previous_status_on_block(s, task, {})
        # 写 history
        _record_status_history(
            s, task,
            {"_to": to, "changed_by": changed_by, "reason": reason or "force_complete"},
        )
        # 应用 status_reason
        _apply_status_reason(s, task, {"_to": to})
        # 失效缓存
        _invalidate_project_stats(s, task, {})
        # 3. set_state（注意：status_reason 必须在 set_state 之前已写好，
        #    因为 _apply_status_reason 是按目标状态决定的，但 done 路径会保留
        #    status_reason 由调用方预先赋值；set_status 流程保证这点）
        _task_sm.set_state(task, to)
        return task
    return _task_sm.execute(
        s, task, to,
        ctx=make_status_change_ctx(to=to, changed_by=changed_by, reason=reason),
    )
    # 注:commit 由调用方/UoW 负责(service 层或测试 fixture 自己 commit 后再 refresh)
