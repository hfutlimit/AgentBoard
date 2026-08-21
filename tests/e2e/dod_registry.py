"""AgentBoard e2e DoD (Definition of Done) Registry。

每完成一个 e2e 阶段,在此追加一条 entry (含验收标准 + 状态)。
配套 docs/e2e-plan.md 第 14 节进度表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable


@dataclass
class DodEntry:
    """单个 e2e 阶段的 DoD 条目。"""
    id: str
    feature: str
    date_added: str
    test_files: list[str]
    coverage_summary: str
    acceptance: list[str]
    status: str = "pending"  # pending | in_progress | done
    closed_date: str | ""
    notes: str = ""


REGISTRY: list[DodEntry] = [
    # ── Epic 152 / 2026-08-21 / Workspace Tabs ─────────────────────
    DodEntry(
        id="epic152-workspace-tabs-2026-08-21",
        feature="项目工作台多 Tab 系统",
        date_added="2026-08-21",
        test_files=[
            "tests/e2e_workspace_tabs/test_workspace_tabs_e2e.py",
        ],
        coverage_summary=(
            "6 个 Playwright 真实断言 test_* 函数覆盖："
            "默认 1 tab / 点菜单加 tab / 重复点击激活 / 关闭 tab / "
            "关闭激活 tab 激活邻居 / URL 同步 + 浏览器 back"
        ),
        acceptance=[
            "进入项目默认 1 tab（概览）",
            "点 Kanban 菜单 → tab 条新增 kanban tab，激活态切换",
            "点 Proposals 菜单 → tab 条再增 proposals tab，共 3 个",
            "再点 Kanban tab（已开）→ 只切换激活态，tab 数仍 3",
            "点中间 tab 的 × → 关闭该 tab，激活态保持原激活",
            "关掉当前激活的 tab → 激活态切到左侧邻居（左侧优先）",
            "URL 反映当前 section（直链 / 前进后退 / 刷新全部 work）",
            "浏览器 back 5 步 → URL 回到上一个 section + 上一个 tab 重新激活",
            "同 (projectId, kind) 至多 1 个 tab；切项目 → tab 列表清空",
            "顶部 topbar 完整保留（用户硬性约束）",
            "8 个 sidebar menu 项 aria-label 全部存在（向后兼容 test_x_b1）",
        ],
        status="done",
        closed_date="2026-08-21",
        notes=(
            "实现：WorkspaceTabsService (in-memory) + TabPaneComponent 包装派发器 + "
            "ProjectWorkspaceShellComponent 重构为 sidebar + tab strip + tab pane stack。"
            "URL = 激活态 source of truth (保留 e2e_epic149/test_x_b1_route_8tab 兼容)。"
        ),
    ),
]


def get_pending() -> list[DodEntry]:
    return [e for e in REGISTRY if e.status != "done"]


def get_done() -> list[DodEntry]:
    return [e for e in REGISTRY if e.status == "done"]


def find_by_id(entry_id: str) -> DodEntry | None:
    for e in REGISTRY:
        if e.id == entry_id:
            return e
    return None
