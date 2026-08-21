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

    # ── Epic 152 / 2026-08-21 v3 - 4 修 / Detail open in new tab ───────
    DodEntry(
        id="epic152-detail-new-tab-2026-08-21",
        feature="*-tab 内部点详情 link → 在新浏览器 tab 打开全页路由",
        date_added="2026-08-21",
        test_files=[
            "tests/e2e_workspace_tabs/test_detail_new_tab_e2e.py",
        ],
        coverage_summary=(
            "4 个 Playwright 真实断言 test_* 函数覆盖："
            "点 epic → window.open 被调用 1 次 / 点 link 后无 side panel / "
            "原 tab URL + tab 列表不变 / 侧栏菜单不被拦截"
        ),
        acceptance=[
            "从 epics tab 点 epic → window.open 调 1 次,target=_blank,url=/epic/:id,带 noopener",
            "原 workspace tab URL 保持 /project/1/epics,tab 列表不变",
            "workspace main **不**出现 side panel (用户要求不要抽屉)",
            "顶部 topbar + 左侧菜单 + 8 个 section tab 仍 work (拦截器不误伤)",
            "原 tab 没有 page reload (无 URL navigate)",
        ],
        status="done",
        closed_date="2026-08-21",
        notes=(
            "v3 - 4 修:用户实测后拒绝 side panel 方案,要求点详情 link 直接**在新浏览器 tab 打开**全页路由 "
            "(打开 /epic/:id 这种完整页面),workspace 上下文保持不变(切回原 tab 继续工作)。\n"
            "实现:onDocumentClickCapture 在 capture phase 拦截 *-tab 内部 <a routerLink>, "
            "preventDefault 当前 tab navigate + window.open(href, '_blank', 'noopener,noreferrer') "
            "开新 tab,opener=null 防 tab-nabbing。\n"
            "技术栈选型说明:\n"
            "- 不修改任何 *-tab 组件的 template/TS (用 capture phase document-level 拦截)\n"
            "- 修饰键 (Ctrl/Meta/Shift) + 中键 → 让浏览器原生处理 'open in new tab',我们不拦截\n"
            "- 仅在 /project/:id/* 路径下生效,避免误伤顶栏/侧栏同 URL link\n"
            "- 移除 v3 Step 1 的 DetailPaneComponent (用户实测不要抽屉)\n"
            "- 顶层 /story/:id / /task/:id / /epic/:id 全页路由仍 work (从命令面板/通知/URL bar 进入)"
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
