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
    # ── Epic 152 / 2026-08-21 / Workspace Tabs (v2 修) ─────────────
    DodEntry(
        id="epic152-workspace-tabs-2026-08-21",
        feature="项目工作台多 Tab 系统",
        date_added="2026-08-21",
        test_files=[
            "tests/e2e_workspace_tabs/test_workspace_tabs_e2e.py",
        ],
        coverage_summary=(
            "7 个 Playwright 真实断言 test_* 函数覆盖："
            "默认 1 tab / 点菜单加 tab / 重复点击激活 / 关闭 tab / "
            "无 page reload (v2 修) / 跨 tab 状态保留 (v2 修) / "
            "URL replaceState 静默同步 (v2 修)"
        ),
        acceptance=[
            "进入项目默认 1 tab（概览）",
            "点 Kanban 菜单 → tab 条新增 kanban tab，激活态切换",
            "点 Proposals 菜单 → tab 条再增 proposals tab，共 3 个",
            "再点 Kanban tab（已开）→ 只切换激活态，tab 数仍 3",
            "点中间 tab 的 × → 关闭该 tab，激活态保持原激活",
            "关掉当前激活的 tab → 激活态切到左侧邻居（左侧优先）",
            "切 tab **不**触发整页刷新（v2 修核心，DOM sentinel 验证）",
            "切走再切回，tab 内 select 数量不变（v2 修核心，证明组件实例保活）",
            "切 tab → URL 静默更新（replaceState，不新增 history entry）",
            "URL 与 service 状态保持一致，刷新能恢复用户当前激活 tab",
            "同 (projectId, kind) 至多 1 个 tab；切项目 → tab 列表清空",
            "顶部 topbar 完整保留（用户硬性约束）",
            "8 个 sidebar menu 项 aria-label 全部存在（向后兼容 test_x_b1）",
        ],
        status="done",
        closed_date="2026-08-21",
        notes=(
            "实现：WorkspaceTabsService (in-memory) + TabPaneComponent 包装派发器 + "
            "ProjectWorkspaceShellComponent 重构为 sidebar + tab strip + tab pane stack。\n"
            "v1 → v2 修：菜单/tab 条点击不再用 <a routerLink>（会触发 Angular router "
            "跳路由 → app.ts loadRoute 重拉数据 → 用户感知为'刷新 + 状态丢失'）。"
            "v2 改用 (click) + tabsService 直接调 + history.replaceState 静默同步 URL，"
            "tab 切换是纯 client state 操作（ajax 风格），其他 tab 状态完整保留。"
        ),
    ),

    # ── Epic 152 / 2026-08-21 v3 (Step 1) / Detail Pane ───────────────
    DodEntry(
        id="epic152-detail-pane-2026-08-21",
        feature="项目工作台 master-detail side panel",
        date_added="2026-08-21",
        test_files=[
            "tests/e2e_workspace_tabs/test_detail_pane_e2e.py",
        ],
        coverage_summary=(
            "5 个 Playwright 真实断言 test_* 函数覆盖："
            "从 *-tab 内部点 link → side panel 出现 / 点 × 关闭 / "
            "panel 打开时切 tab 不影响 / 侧栏菜单 link 不被误伤 / "
            "'open in full page' 跳顶层路由"
        ),
        acceptance=[
            "从 epics tab 点 Epic 链接 → side panel 出现，URL 不跳 /epic/:id",
            "side panel 显示 kind (Epic) + id (#N) + 关闭按钮",
            "点 × 关闭 side panel，workspace 上下文不变",
            "side panel 打开时切 tab 仍 work（不关 panel，无 page reload）",
            "左侧菜单的同 URL link 不被误伤（workspace click 拦截器只针对 6 类 detail 路由）",
            "side panel 的 'open in full page' 走原顶层路由，panel 关闭 + URL = /epic/:id",
            "顶部 topbar + 8 个 section tab 仍 work（向后兼容 v2 修）",
            "workspace 上下文保留：active tab 不变、tab 列表不变、其他 tab 状态不丢",
        ],
        status="in_progress",
        closed_date="",
        notes=(
            "v2 → v3 修 (Step 1)：*-tab 内部点 Story/Task/Epic/Proposal/Sprint/Document 链接 "
            "不再跳顶层 /story/:id / /task/:id / /epic/:id 全页（会退出 workspace 上下文），"
            "改为 workspace 内的 master-detail side panel（workspace main 右侧滑出）。\n"
            "实现：\n"
            "- DetailPaneComponent — workspace main 右侧 480px 滑出 panel\n"
            "- project-workspace-shell.ts 加 detailSelection signal + onOpenDetail/Close\n"
            "- workspace main 加全局 click 拦截：捕获指向 6 类 detail 路由的 <a> click，"
            "preventDefault + 显示 side panel\n"
            "- 仅在 /project/:id/* 路径下拦截（避免误伤侧边栏 / 顶栏同 URL link）\n"
            "- Step 1 是占位 panel（kind + id + 关闭 + open full page 链接）\n"
            "- Step 2 下一个 commit：提取 app.html @case ('story' / 'task' / 'epic' / 'proposal' / 'sprint') "
            "到独立 component，side panel 用真实详情渲染。\n"
            "顶层 /story/:id 全页路由仍 work（从命令面板 / 通知 / URL bar 进入的场景）。"
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
