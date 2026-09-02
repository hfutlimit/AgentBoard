"""work item 归属门（Implementation Plan T1.5）— 单一真源。

为什么要有这个模块
------------------
owner 校验此前散落在 6 处，且各自硬编码 ``created_by_user_id``：

    features/work_items/service.py  try_assign_task / apply_for_task
    features/scheduling/service.py  list_runnable_candidates /
                                     assign_task_reviewer /
                                     _reassign_task_reviewer
                                     （_reassign_story_reviewer 干脆没有门）

``created_by_user_id`` 是**不可变审计列**（谁建的），T2.3 移交之后它会与真实
归属分叉 —— 到那时这 6 处会**同时判错**，而且错得各不相同。所以判据必须收敛
成一条，且只看**可变**的 ``owner_user_id``。

判据
----
    item.owner_user_id == agent.user_id   且   agent.id ∉ exclude_agent_ids

**明确不查 ProjectMember**。成员关系是**读门**（能不能看见，T2.1），归属是
**执行门**（能不能干）。两者职责不同：项目成员应当能读到别人的任务（共享读是
本次重构的目标之一），但不应当能替别人执行。混在一起会把读门退化成写门。

**owner 为 NULL → 不通过**（fail closed）。这不是保守：放行等于让任意在线
agent 抢走无人认领的活，事后无法追溯。存量 NULL 由 T1.4 回填处理，回填不了
的（created_by 也为空）需人工补 owner —— 卡住是对的，静默放行是错的。

为什么放这里
------------
`scheduling.service` 在模块顶层 import `work_items.service`，反向 import 会成
环；本模块只依赖 model 层，两个方向都能安全引用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ...core.exceptions import Forbidden
from ..projects.models import Agent, Story
from .models import Task

__all__ = [
    "GateDecision",
    "work_item_owner_user_id",
    "work_item_label",
    "agent_can_handle_work_item",
    "assert_agent_can_handle_work_item",
]

# 门不通过的原因码。写进 Task.assignment_deferred_reason 供看板/排障消费，
# 不要随便改字面量 —— 前端与运维脚本按 code 分支。
CODE_OK = "ok"
CODE_NO_OWNER = "no_owner"          # owner 为 NULL，无从判断归属
CODE_NOT_OWNER = "not_owner"        # agent 不属于 owner 名下
CODE_EXCLUDED = "excluded"          # 自审排除（实现方不能评自己）


@dataclass(frozen=True)
class GateDecision:
    """执行门判定结果。

    带 ``code`` 而不是只回 bool：派发链路要把它序列化进
    ``assignment_deferred_reason``，看板得区分「没人能干」和「不该你干」。
    """

    allowed: bool
    code: str = CODE_OK
    detail: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def work_item_owner_user_id(item: Any) -> int | None:
    """取 work item 的当前归属 user（Task 与 Story 同名列，鸭子类型取之）。"""
    value = getattr(item, "owner_user_id", None)
    return None if value is None else int(value)


def work_item_label(item: Any) -> str:
    """日志/异常里用的实体名，避免每条都手写 f"{type} {id}"。"""
    return f"{type(item).__name__.lower()} {getattr(item, 'id', '?')}"


def agent_can_handle_work_item(
    agent: Agent | None,
    item: Task | Story,
    *,
    exclude_agent_ids: Iterable[int] | None = None,
    fallback_user_id: int | None = None,
) -> GateDecision:
    """判断 ``agent`` 能否执行 ``item``。

    :param exclude_agent_ids: 自审排除集。实现方 agent 不能评审自己做的活，
        由调用方从 ``get_assignment_exclusion`` 拿到后传入 —— 本模块不反向
        依赖 scheduling，否则成环。
    :param fallback_user_id: **用户级**调用的归属依据。API key 可能只绑 user
        不绑 agent（人工认领、管理端代操作），此时没有 agent 可判，退而比对
        user 本身 —— 老代码就是这么判的，不能因为加了 agent 维度就把这条路堵死。
    """
    owner_user_id = work_item_owner_user_id(item)
    if owner_user_id is None:
        return GateDecision(
            False, CODE_NO_OWNER,
            f"{work_item_label(item)} has no owner (owner_user_id is NULL); "
            "assign an owner before it can be handled",
        )

    if agent is None:
        if fallback_user_id is None:
            return GateDecision(
                False, CODE_NOT_OWNER,
                f"no agent identity and no fallback user for "
                f"{work_item_label(item)}; cannot verify ownership",
            )
        if int(fallback_user_id) != owner_user_id:
            return GateDecision(
                False, CODE_NOT_OWNER,
                f"only the owner may handle {work_item_label(item)} "
                f"(owner={owner_user_id}, requester user={fallback_user_id})",
            )
        return GateDecision(True, CODE_OK)

    if int(agent.user_id) != owner_user_id:
        return GateDecision(
            False, CODE_NOT_OWNER,
            f"only the owner's agent may handle {work_item_label(item)} "
            f"(owner={owner_user_id}, agent '{agent.agent_id}' belongs to "
            f"user {agent.user_id})",
        )

    # 容忍 None：调用方常把 `| {task.reviewer_agent_id}` 拼进来，那个字段可空。
    excluded = {int(a) for a in (exclude_agent_ids or ()) if a is not None}
    if int(agent.id) in excluded:
        return GateDecision(
            False, CODE_EXCLUDED,
            f"agent '{agent.agent_id}' is excluded for {work_item_label(item)}"
            " (self-review guard)",
        )

    return GateDecision(True, CODE_OK)


def assert_agent_can_handle_work_item(
    agent: Agent | None,
    item: Task | Story,
    *,
    exclude_agent_ids: Iterable[int] | None = None,
    fallback_user_id: int | None = None,
) -> None:
    """主动认领路径的门：不通过直接 403。

    只有**主动**路径（agent 自己来 claim / apply）才用这个 —— 那是越权，该报错。
    scheduler 自动派发必须用 ``agent_can_handle_work_item`` 的返回值做过滤，
    不能让空候选变成异常（Plan 验收②：fail-closed 保持待处理 + 写
    ``assignment_deferred_reason``，不抛 403）。
    """
    decision = agent_can_handle_work_item(
        agent, item, exclude_agent_ids=exclude_agent_ids,
        fallback_user_id=fallback_user_id,
    )
    if not decision.allowed:
        raise Forbidden(decision.detail, details={"code": decision.code})
