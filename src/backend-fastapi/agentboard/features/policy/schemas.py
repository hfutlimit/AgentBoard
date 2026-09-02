"""行动策略的 API 传输模型（request / response body）。

只负责**形状校验与归一化**，业务规则（层级合并、裁决）一律留在
:mod:`agentboard.features.policy.engine`：否则会出现"pydantic 算一半、
engine 算一半"的分叉实现，跟 M0 评审真源统一的教训冲突。

``ActionPolicyOverrides`` 是**局部覆盖**语义：字段为 ``None`` 表示该层
对此动作不表态（合并时被忽略），而不是"要求走默认值"。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .models import ALL_ACTIONS, ActionPolicy, ActionPolicyLevel

#: API 层接受的等级字面量（与 :class:`ActionPolicyLevel` 的 label 一一对应）。
PolicyLevelValue = Literal["deny", "manual", "approval", "auto"]


class ActionPolicyOverrides(BaseModel):
    """某一层（team / project / task / user_pref）的策略覆盖。

    六个 git 动作一一列出而非动态生成：IDE 补全与 API 文档都直接可读，
    :meth:`to_levels` 负责丢掉 ``None`` 交给 engine 合并。
    """

    create_branch: PolicyLevelValue | None = Field(
        default=None, description="是否允许 Agent 自主创建分支")
    commit: PolicyLevelValue | None = Field(
        default=None, description="是否允许 Agent 自主提交")
    push: PolicyLevelValue | None = Field(
        default=None, description="是否允许 Agent 推送到远端")
    create_pr: PolicyLevelValue | None = Field(
        default=None, description="是否允许 Agent 创建 PR / MR")
    merge: PolicyLevelValue | None = Field(
        default=None, description="是否允许 Agent 合入主干")
    force_push: PolicyLevelValue | None = Field(
        default=None, description="是否允许 Agent 强推（高危，默认 deny）")

    def to_levels(self) -> dict[str, ActionPolicyLevel]:
        """转成 engine 能吃的 ``{action: level}``，剔除未表态（None）的字段。"""
        levels: dict[str, ActionPolicyLevel] = {}
        for action in ALL_ACTIONS:
            raw = getattr(self, str(action))
            if raw is None:
                continue
            levels[str(action)] = ActionPolicyLevel.parse(raw)
        return levels

    @classmethod
    def from_policy(cls, policy: ActionPolicy) -> "ActionPolicyOverrides":
        """把生效策略全量物化成响应体（补齐默认值，便于前端完整展示）。"""
        materialized = policy.to_dict()
        return cls(**{str(action): materialized[str(action)] for action in ALL_ACTIONS})
