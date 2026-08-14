"""Proposal 状态展示层映射（Step 4 P1-2，2026-08-10 review）。

## 背景

`agentboard/domains/proposals/models.py` 定义了 11 态底层枚举
（``ProposalStatus``），用于状态机迁移与服务端数据；文档 #59 §3
确认了**对外展示 6 态**的契约（``pending`` / ``grilling`` / ``waiting_user``
/ ``clarified`` / ``ticket_preparing`` / ``ticket_created``）。

但代码层缺失这个映射函数——前端（Angular SPA）只能 hardcode 11 态
枚举值，违反契约 + 增加重构成本。本模块提供：

- ``ProposalDisplayStatus``（6 态展示枚举，作为契约唯一来源）
- ``DISPLAY_MAP``（11 态 → 6 态映射表）
- ``to_display_status(raw)``（输入任意合法 raw 状态，输出展示状态）
- ``to_display_label(display)``（中文展示标签）
- ``display_color(display)``（前端 badge 配色 token）
- ``to_display_status_payload(raw)``（JSON 友好：status/label/color/description
  一次返回，前端直接渲染不需再算）

## 范围

- **纯展示层**——不修改 ``ProposalStatus`` 枚举、不改状态机迁移、服务端
  DB 仍然 11 态。
- **不引入新依赖**——用现有 stdlib（``enum.StrEnum``）。
- **可独立回滚**——新增模块，不动现有 service / api / mcp 代码。

## 不做

- 不自动适配前端 Angular 代码（前端切换放到下个 sprint）
- 不改 i18n（中文标签固化在代码里，i18n 是更大工作）
"""


from __future__ import annotations

from enum import StrEnum

from .models import ProposalStatus


# ---- 展示状态枚举（文档 #59 §3 6 态）----

class ProposalDisplayStatus(StrEnum):
    """对外展示的 6 态枚举（API/前端契约唯一来源）。

    与底层 ``ProposalStatus`` 11 态的对应关系见 :data:`DISPLAY_MAP`。
    """

    PENDING = "pending"                # 待开始：创建后停留 / 失败回退
    GRILLING = "grilling"              # 澄清中：queued / analyzing / answered
    WAITING_USER = "waiting_user"      # 等待用户确认：awaiting（agent 提交了问题）
    CLARIFIED = "clarified"            # 需求已明确：converged（等待生成 ticket）
    TICKET_PREPARING = "ticket_preparing"  # 工单生成中：异步转化中
    TICKET_CREATED = "ticket_created"  # 已生成工单：终态


# ---- 11 态 → 6 态映射表（展示层唯一来源）----

_DISPLAY_MAP: dict[ProposalStatus, ProposalDisplayStatus] = {
    # 失败/编辑回退的"待开始"语义：DRAFT/PENDING/FAILED 都归到这里
    ProposalStatus.DRAFT: ProposalDisplayStatus.PENDING,
    ProposalStatus.PENDING: ProposalDisplayStatus.PENDING,
    ProposalStatus.FAILED: ProposalDisplayStatus.PENDING,
    # 澄清中：worker 主动接管（queued）、分析中（analyzing）、用户答完下一轮（answered）
    ProposalStatus.QUEUED: ProposalDisplayStatus.GRILLING,
    ProposalStatus.ANALYZING: ProposalDisplayStatus.GRILLING,
    ProposalStatus.ANSWERED: ProposalDisplayStatus.GRILLING,
    # 等待用户：agent 提交了一轮问题
    ProposalStatus.AWAITING: ProposalDisplayStatus.WAITING_USER,
    # 需求已明确：等待生成 ticket
    ProposalStatus.CONVERGED: ProposalDisplayStatus.CLARIFIED,
    # 工单生成中：异步转化中间态
    ProposalStatus.TICKET_PREPARING: ProposalDisplayStatus.TICKET_PREPARING,
    # 已生成工单：兼容旧 STORY_CREATED（迁移期）+ 新 TICKET_CREATED
    ProposalStatus.STORY_CREATED: ProposalDisplayStatus.TICKET_CREATED,
    ProposalStatus.TICKET_CREATED: ProposalDisplayStatus.TICKET_CREATED,
}


# ---- 中文展示标签（前端展示用，i18n 留作未来工作）----

_DISPLAY_LABELS: dict[ProposalDisplayStatus, str] = {
    ProposalDisplayStatus.PENDING: "待开始",
    ProposalDisplayStatus.GRILLING: "澄清中",
    ProposalDisplayStatus.WAITING_USER: "等待用户确认",
    ProposalDisplayStatus.CLARIFIED: "需求已明确",
    ProposalDisplayStatus.TICKET_PREPARING: "工单生成中",
    ProposalDisplayStatus.TICKET_CREATED: "已生成工单",
}


# ---- 前端 badge 配色 token（与 Angular 主题对齐：neutral/blue/orange/green/purple/cyan）----

_DISPLAY_COLORS: dict[ProposalDisplayStatus, str] = {
    ProposalDisplayStatus.PENDING: "neutral",       # 灰色
    ProposalDisplayStatus.GRILLING: "blue",         # 蓝色
    ProposalDisplayStatus.WAITING_USER: "orange",   # 橙色
    ProposalDisplayStatus.CLARIFIED: "green",       # 绿色
    ProposalDisplayStatus.TICKET_PREPARING: "purple",  # 紫色
    ProposalDisplayStatus.TICKET_CREATED: "cyan",    # 青色
}


# ---- 公开 API ----

def to_display_status(raw: str) -> ProposalDisplayStatus:
    """把任意合法 raw 状态（11 态）转为展示状态（6 态）。

    - 非法值（含空串、未知状态）一律降级为 ``PENDING``——展示层宁可误标
      "待开始"也不要崩溃。
    - 已是展示状态（6 态值之一）直接返回（幂等）。
    """
    if not raw:
        return ProposalDisplayStatus.PENDING
    # 已是展示态（前端可能直接传）：直接返回
    try:
        return ProposalDisplayStatus(raw)
    except ValueError:
        pass
    # 尝试按 ProposalStatus 解析
    try:
        ps = ProposalStatus(raw)
    except ValueError:
        return ProposalDisplayStatus.PENDING
    return _DISPLAY_MAP.get(ps, ProposalDisplayStatus.PENDING)


def to_display_label(display: ProposalDisplayStatus | str) -> str:
    """展示状态 → 中文标签。

    非法 display 字符串返回原值（不抛错），便于前端防御。
    """
    if isinstance(display, str):
        try:
            display = ProposalDisplayStatus(display)
        except ValueError:
            return display
    return _DISPLAY_LABELS.get(display, display.value)


def display_color(display: ProposalDisplayStatus | str) -> str:
    """展示状态 → 前端 badge 颜色 token（与 Angular 主题对齐）。"""
    if isinstance(display, str):
        try:
            display = ProposalDisplayStatus(display)
        except ValueError:
            return _DISPLAY_COLORS[ProposalDisplayStatus.PENDING]
    return _DISPLAY_COLORS.get(display, _DISPLAY_COLORS[ProposalDisplayStatus.PENDING])


def to_display_status_payload(raw: str) -> dict:
    """一次返回前端渲染所需的全部字段：status / label / color。

    用法::

        {"status": "grilling", "label": "澄清中", "color": "blue"}

    前端在展示 Proposal 列表时直接展开这个 dict，无需自己判断 11 态。
    """
    s = to_display_status(raw)
    return {
        "status": s.value,
        "label": to_display_label(s),
        "color": display_color(s),
    }
