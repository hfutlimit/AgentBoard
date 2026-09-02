"""行动策略合并与裁决引擎（M6 T6.4）。

职责
----
只做三件事，且只接受**服务端**的层级配置：

1. :func:`effective_policy` —— 自上而下合并 team / project / task / user_pref
   四层，逐动作取 ``min()``（最严格者胜）；
2. :func:`decide` —— 把等级翻译成裁决（ALLOW / REQUIRE_APPROVAL /
   REQUIRE_MANUAL / DENY）；
3. :func:`is_allowed` / :func:`requires_approval` —— 给调用方（MCP 工具、
   executor）用的两个高频速查短路。

安全边界
--------
**Agent 的产出永远不参与这里的计算。** 无头 Agent 通过 prompt 注入
（"项目策略已更新为 AUTO"）自我提权在本层无效：本模块没有任何入口接受
来自 Agent 文本的等级，effective 只由服务端持久化配置决定。调用方若需要
"Agent 请求放宽"，必须走审批流写回持久化配置，而不是临时传参。

接入点建议
----------
- MCP / executor 执行 git 动作前：``decide(effective, action)`` → 非 ALLOW 时
  按裁决生成审批单或直接拒绝；
- Task / Project / 用户偏好配置落库时：用 :class:`ActionPolicy` 解析校验，
  拒绝非法等级（``InvalidValue`` → HTTP 400）。
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ...core.exceptions import InvalidValue
from .models import (
    ALL_ACTIONS,
    DECISION_BY_LEVEL,
    DEFAULT_LEVELS,
    ActionPolicy,
    ActionPolicyLevel,
    GitAction,
    PolicyDecision,
    parse_action,
)

__all__ = [
    "effective_policy",
    "coerce_policy",
    "decide",
    "is_allowed",
    "requires_approval",
]


def coerce_policy(value: Any, *, layer: str = "policy") -> ActionPolicy | None:
    """把 ``ActionPolicy`` / dict / JSON 字符串归一化成 :class:`ActionPolicy`。

    - ``None`` / 空 dict / 空串 → ``None``，**表示该层不表态**（不参与 min），
      而不是"该层要求走默认值"；
    - dict / JSON 字符串 → 严格解析，非法内容抛 :class:`InvalidValue`。

    支持 JSON 字符串是因为既有配置普遍以 JSON 文本存列
    （如 ``agent_behavior_configs.config_json``），调用方不必各自 ``json.loads``。
    """
    if value is None:
        return None
    if isinstance(value, ActionPolicy):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise InvalidValue(f"{layer} 层策略不是合法 JSON：{raw!r}") from exc
        if not isinstance(parsed, Mapping):
            raise InvalidValue(f"{layer} 层策略 JSON 必须是对象，实际为 {type(parsed).__name__}")
        return ActionPolicy.from_dict(parsed)
    if isinstance(value, Mapping):
        return ActionPolicy.from_dict(value) if value else None
    raise InvalidValue(
        f"{layer} 层策略类型不支持：{type(value).__name__}"
        "（仅接受 ActionPolicy / dict / JSON 字符串 / None）"
    )


def effective_policy(
    *,
    team: Any = None,
    project: Any = None,
    task: Any = None,
    user_pref: Any = None,
) -> ActionPolicy:
    """合并四层配置，返回逐动作取最严格值后的生效策略。

    合并规则（核心不变式）：

    - 每个动作独立取 ``min(该动作在所有非空层里的等级)``；
    - **某层没有表态（None / 未配置该动作）就不参与比较**，因此下层只能把
      策略收紧，**永远不能放宽上层**（上层 DENY + 下层 AUTO = DENY）；
    - 四层都没表态的动作，回落到 :data:`~.models.DEFAULT_LEVELS`。

    全部参数都是 keyword-only：强制调用方显式标注层级，避免"传错顺序把
    team 配置当 user_pref"这类低级但致命的错误。
    """
    layers: list[tuple[str, ActionPolicy]] = []
    for name, raw in (("team", team), ("project", project),
                      ("task", task), ("user_pref", user_pref)):
        policy = coerce_policy(raw, layer=name)
        if policy is not None:
            layers.append((name, policy))

    merged: dict[str, ActionPolicyLevel] = {}
    for action in ALL_ACTIONS:
        key = str(action)
        candidates = [
            policy.levels[key]
            for _, policy in layers
            if key in policy.levels  # 只有显式配置才表态
        ]
        # 无层表态 → 回落到默认策略，保证返回值始终是完整策略（可安全落库）。
        merged[key] = min(candidates) if candidates else DEFAULT_LEVELS[key]
    return ActionPolicy(levels=merged)


def decide(policy: ActionPolicy, action: str | GitAction) -> PolicyDecision:
    """裁决单个动作：等级 → :class:`PolicyDecision`。

    未知动作抛 :class:`InvalidValue`（而不是默认放行）：白名单外的动作
    往往是新功能漏配策略，拒绝比静默放行安全。
    """
    level = policy.level_for(parse_action(action))
    return DECISION_BY_LEVEL[level]


def is_allowed(policy: ActionPolicy, action: str | GitAction) -> bool:
    """该动作是否"有被执行的机会"（含审批后执行）。

    注意语义：只有 DENY 返回 ``False``；MANUAL / APPROVAL 都返回 ``True``，
    它们不是"允许自动执行"，而是"允许走人工/审批流程"。能否自动执行请看
    :func:`decide` 是否返回 ``ALLOW``。
    """
    return decide(policy, action) is not PolicyDecision.DENY


def requires_approval(policy: ActionPolicy, action: str | GitAction) -> bool:
    """是否需要人工介入（APPROVAL 走审批流、MANUAL 只能人来做）。"""
    return decide(policy, action) in (
        PolicyDecision.REQUIRE_APPROVAL,
        PolicyDecision.REQUIRE_MANUAL,
    )
