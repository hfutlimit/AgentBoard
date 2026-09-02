"""行动策略引擎（M6 T6.4）。

把"Agent 能做什么"从 prompt 约定升级为**服务端强制的四态策略**：
``DENY < MANUAL < APPROVAL < AUTO``，生效策略按
``effective = min(team, project, task, user_pref)`` 逐动作取最严格值。

- :mod:`.models`  四态等级、动作白名单、默认策略、:class:`ActionPolicy`
- :mod:`.engine`  层级合并（``effective_policy``）与裁决（``decide``）
- :mod:`.schemas` API 传输模型（局部覆盖语义）

硬约束只在本包内强制：Agent 通过 prompt 注入声明的等级不参与任何计算入口。
"""
from __future__ import annotations

from .engine import (
    coerce_policy,
    decide,
    effective_policy,
    is_allowed,
    requires_approval,
)
from .models import (
    ALL_ACTIONS,
    DECISION_BY_LEVEL,
    DEFAULT_LEVELS,
    DEFAULT_POLICY,
    ActionPolicy,
    ActionPolicyLevel,
    GitAction,
    PolicyDecision,
    parse_action,
)
from .schemas import ActionPolicyOverrides, PolicyLevelValue

__all__ = [
    "ActionPolicy",
    "ActionPolicyLevel",
    "ActionPolicyOverrides",
    "PolicyLevelValue",
    "PolicyDecision",
    "GitAction",
    "ALL_ACTIONS",
    "DEFAULT_LEVELS",
    "DEFAULT_POLICY",
    "DECISION_BY_LEVEL",
    "parse_action",
    "coerce_policy",
    "effective_policy",
    "decide",
    "is_allowed",
    "requires_approval",
]
