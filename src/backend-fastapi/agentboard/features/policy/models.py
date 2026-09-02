"""行动策略（Action Policy）领域模型：四态等级、动作集合与默认策略。

背景
----
M6 之前，Agent 能做什么基本靠 prompt 约束（"不要在 main 上强推"之类）。
prompt 是不可信输入 —— Agent 输出/工具注入都可以让它自我提权，因此
**硬约束必须在 Policy 层强制**：运行时只认服务端算出的 ``effective``
策略，任何来自 Agent 文本的等级声明都不参与计算。

设计取舍
--------
1. **等级用 ``IntEnum`` 而非 ``StrEnum``**：``effective = min(team, project, task, user_pref)``
   依赖"越严格值越小"的序语义，IntEnum 天然可比较，避免各处手写 rank
   映射表（漏更新就会出现"更严格的等级反而更大"的静默错误）。
   序列化统一走 :attr:`ActionPolicyLevel.label`（小写字符串）。
2. **序为 ``DENY < MANUAL < APPROVAL < AUTO``**：任何一层出现 DENY，
   ``min()`` 必定收敛到 DENY，下层无法通过声明 AUTO 提权（核心不变式）。
3. **默认策略"安全但可用"**，见 :data:`DEFAULT_LEVELS`：放行 create_branch /
   commit（否则自动化流程一步都跑不动，M6 评审结论），push / create_pr 需要
   审批（保留审计点），merge / force_push 直接拒绝（破坏性且难回滚）。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

from ...core.exceptions import InvalidValue


class ActionPolicyLevel(IntEnum):
    """单个动作的自治等级（四态，序数值即严格程度）。

    ``DENY``     禁止执行（Agent 永远不可以做）；
    ``MANUAL``   只能人来做（Agent 连"审批后自动执行"都不允许）；
    ``APPROVAL`` 审批通过后可由 Agent 执行；
    ``AUTO``     Agent 可自主执行。
    """

    DENY = 0
    MANUAL = 1
    APPROVAL = 2
    AUTO = 3

    @property
    def label(self) -> str:
        """对外/持久化用的小写字符串形式（``"deny"`` / ``"auto"`` ...）。"""
        return self.name.lower()

    @classmethod
    def parse(cls, value: Any) -> "ActionPolicyLevel":
        """把 int / 大小写不敏感字符串 / 自身解析成等级。

        非法值抛 :class:`InvalidValue`（HTTP 400），而不是回退到某个默认等级：
        配置写错时回退会让"以为收紧了其实没收紧"，属于安全洞。
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):  # bool 是 int 子类，先挡掉
            raise InvalidValue(f"策略等级不接受布尔值：{value!r}")
        if isinstance(value, int):
            try:
                return cls(value)
            except ValueError as exc:
                raise InvalidValue(
                    f"未知策略等级 {value!r}，仅允许 {[m.value for m in cls]}"
                ) from exc
        if isinstance(value, str):
            raw = value.strip().upper()
            if raw in cls.__members__:
                return cls[raw]
        raise InvalidValue(
            f"未知策略等级 {value!r}，仅允许 {[m.label for m in cls]}"
        )


class GitAction(StrEnum):
    """受 Policy 约束的 git 相关动作（计划 §4.9）。

    枚举即白名单：未登记的动作一律拒绝，避免"新加一个动作但忘了配策略"
    导致默认放行。
    """

    CREATE_BRANCH = "create_branch"
    COMMIT = "commit"
    PUSH = "push"
    CREATE_PR = "create_pr"
    MERGE = "merge"
    FORCE_PUSH = "force_push"


ALL_ACTIONS: tuple[GitAction, ...] = tuple(GitAction)


def parse_action(value: Any) -> GitAction:
    """把字符串归一化成 :class:`GitAction`，未知动作抛 :class:`InvalidValue`。"""
    if isinstance(value, GitAction):
        return value
    if isinstance(value, str):
        raw = value.strip().lower()
        for action in ALL_ACTIONS:
            if action == raw:
                return action
    raise InvalidValue(
        f"未知动作 {value!r}，仅允许 {[str(a) for a in ALL_ACTIONS]}"
    )


class PolicyDecision(StrEnum):
    """单次动作的裁决结果。

    ``ALLOW``            可直接执行（AUTO）；
    ``REQUIRE_APPROVAL`` 需审批，通过后可由 Agent 执行（APPROVAL）；
    ``REQUIRE_MANUAL``   必须由人执行，Agent 不得代劳（MANUAL）；
    ``DENY``             禁止（DENY）。
    """

    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    REQUIRE_MANUAL = "require_manual"
    DENY = "deny"


#: 等级 → 裁决。``decide()`` 的唯一真源，杜绝散落各处的 if/elif 分支。
DECISION_BY_LEVEL: Mapping[ActionPolicyLevel, PolicyDecision] = {
    ActionPolicyLevel.DENY: PolicyDecision.DENY,
    ActionPolicyLevel.MANUAL: PolicyDecision.REQUIRE_MANUAL,
    ActionPolicyLevel.APPROVAL: PolicyDecision.REQUIRE_APPROVAL,
    ActionPolicyLevel.AUTO: PolicyDecision.ALLOW,
}

#: 未配置任何层级时的默认策略：安全但可用（刻意不是全 MANUAL）。
DEFAULT_LEVELS: Mapping[str, ActionPolicyLevel] = {
    GitAction.CREATE_BRANCH: ActionPolicyLevel.AUTO,
    GitAction.COMMIT: ActionPolicyLevel.AUTO,
    GitAction.PUSH: ActionPolicyLevel.APPROVAL,
    GitAction.CREATE_PR: ActionPolicyLevel.APPROVAL,
    GitAction.MERGE: ActionPolicyLevel.DENY,
    GitAction.FORCE_PUSH: ActionPolicyLevel.DENY,
}


@dataclass(frozen=True)
class ActionPolicy:
    """一份（可能是不完整的）策略规则集。

    契约：``levels`` **只保存显式配置的规则**，缺失的动作不写进来，由
    :meth:`level_for` 回落到 :data:`DEFAULT_LEVELS`。这样层级合并时才能区分
    "该层没表态" 与 "该层明确要按默认走"（合并语义见
    :func:`agentboard.features.policy.engine.effective_policy`）。

    :meth:`to_dict` 例外地返回**全量物化**结果（补齐默认值），用于持久化与
    API 响应 —— 读回来再解析得到同一份 effective，保证 round-trip 稳定。
    """

    levels: dict[str, ActionPolicyLevel] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ActionPolicy":
        """从 dict 解析，**严格校验**：未知动作 / 非法等级都抛 :class:`InvalidValue`。

        为什么对未知 key 也报错：配置里把 ``force_push`` 拼成 ``force-push``
        若静默忽略，该动作会掉回默认值；默认值一旦偏松，等于配置者"以为收紧
        了其实没收紧"。宁可让配置立刻失败。
        """
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise InvalidValue(
                f"策略必须是 JSON 对象，实际为 {type(data).__name__}"
            )
        levels: dict[str, ActionPolicyLevel] = {}
        for raw_action, raw_level in data.items():
            action = parse_action(raw_action)
            levels[str(action)] = ActionPolicyLevel.parse(raw_level)
        return cls(levels=levels)

    def level_for(self, action: str | GitAction) -> ActionPolicyLevel:
        """取某动作的生效等级：显式配置优先，否则回落默认值。"""
        key = str(parse_action(action))
        return self.levels.get(key, DEFAULT_LEVELS[key])

    def to_dict(self) -> dict[str, str]:
        """全量物化成 ``{action: level_label}``，用于存 DB / 出 API。"""
        return {str(a): self.level_for(a).label for a in ALL_ACTIONS}

    def materialize(self) -> "ActionPolicy":
        """返回补齐所有动作的完整副本（本对象不可变，只能换一个新的）。"""
        return ActionPolicy(levels={
            str(a): self.level_for(a) for a in ALL_ACTIONS
        })


#: 未提供任何层级配置时的默认策略对象。
DEFAULT_POLICY: ActionPolicy = ActionPolicy(levels=dict(DEFAULT_LEVELS))
