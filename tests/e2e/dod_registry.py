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

    # ── v7.3 / 2026-08-21 / Story 详情页任务列表简化 ─────────────────
    DodEntry(
        id="v73-story-slim-tasks-2026-08-21",
        feature="Story 详情页任务列表简化（taskbar 精简 + 选项 popover + chips 隐藏零计数 + 行内降噪）",
        date_added="2026-08-21",
        test_files=[
            "tests/e2e_story_slim_tasks/test_story_slim_tasks_e2e.py",
        ],
        coverage_summary=(
            "6 个 Playwright 真实断言 test_* 函数覆盖："
            "taskbar 单行结构 / 选项 popover 控件全开合 / 零计数 chip 隐藏 / "
            "行内降噪 / 密度切换持久 / 默认态降噪 (checkbox+kbd)"
        ),
        acceptance=[
            "Story 详情页 'Task 列表' tab → taskbar 单行 + 内联进度条 (n/m + 进度条 fill)，无 .task-list-summary 旧结构",
            "点 `.icon-btn[aria-label='Task 选项']` → .task-opts-popover 开，6 个控件全在 (只看我 / 密度 / 排序 / 分组 / 筛选预设 inline / 导出 CSV/JSON)",
            "无 .export-menu (新设计移除),只有 .icon-btn[aria-label='导出'] 单按钮",
            "statusCounts 0 的 chip 不渲染：5 个状态最多 4 个 chip,评审中/已阻塞零计数不出现",
            "task 行无 due 时不渲染 '无截止' 占位 pill；无 assignee 时不渲染 '未分配' 占位 pill",
            "task 行无 due 时无 '设截止' inline 编辑文案",
            "popover 内切密度 → 关闭后 .task-opts-dot 仍显示密度对应活动点 (持久化到 service)",
            "task-checkbox 默认 opacity:0 (focus/hover 显);kbd-hint 默认 display:none (focus 显)",
            "API 事实 = 页面事实: 计数断言用 GET /api/stories/{id}/tasks 读回,适配 create_story 自动编排生成 '设计：/开发：' 2 个子任务",
        ],
        status="done",
        closed_date="2026-08-21",
        notes=(
            "v7.3 收尾:用户原话 'task 列表里 task 不会那么多 重新设计下 简洁一点' → 把旧 4 行 taskbar "
            "+ 11 个 chip + 8 个 export 菜单项的繁复 UI 收敛到 1 行 taskbar + popover 收纳 + "
            "零计数隐藏 + 行内降噪。\n"
            "实现要点:\n"
            "- taskbar--slim 单行容器:左侧 n/m + 进度条 fill,右侧 icon-btn 收纳到 popover\n"
            "- taskOptionsOpen / taskOptionsActive / taskOptionsHandlers (open/toggle/close) "
            "app.ts 加,清理 presetOpen 死代码\n"
            "- icon-btn 标签默认 display:none,kbd 提示 focus 显 (kbmode)\n"
            "- 行内降噪:无 due 不渲染占位 pill,无 assignee 不渲染占位 pill\n"
            "- filterbar--inline:筛选预设条横置\n"
            "- statusCounts 计算走中央 helper,0 计数的 chip 不渲染 (.slim-count-0 selector 直接 filter 掉)\n"
            "app.spec.ts 更新 story task controls 断言到新结构 (icon-btn + popover),70 passed / 1 skipped 全绿。"
        ),
    ),

    # ── v7.3 bugfix / 2026-08-21 / delete_epic|delete_story FK 500 ──────
    DodEntry(
        id="v73-bugfix-delete-cascade-fk-2026-08-21",
        feature="DELETE /api/epics|/api/stories 500 产品级 bug 修复（FK 防御级联）",
        date_added="2026-08-21",
        test_files=[
            "tests/test_delete_cascade_fk.py",
        ],
        coverage_summary=(
            "7 个 pytest 服务端单测 test_* 函数覆盖："
            "epic 删 done 任务 (落 task_outcome) 不 500 / story 删 done 任务不 500 / "
            "task 删含 ReviewVote.comment_id 引用不 500 / "
            "epic 删解绑 agent_schedules.epic_id / epic 删解绑 review_votes entity_id 锚点 / "
            "删不存在 epic/story 返回 False"
        ),
        acceptance=[
            "task 走 done（落 task_outcome + episode_embedding + project_playbook_episode）后 DELETE /api/epics/{id} → 200，learning 引用全部联动清",
            "task 走 done 后 DELETE /api/stories/{id} → 200",
            "task 含评审 comment 且被 ReviewVote.comment_id 引用 → DELETE /api/tasks/{id} → 200（vote.comment_id 已被 NULL 化）",
            "绑了 agent_schedule 的 epic 被删 → schedule.epic_id 被 NULL 化，schedule 配置保留",
            "删 epic 时其下 story 的 review_votes 锚点 (entity_type=story) 被切断 (entity_id 置 -1)",
            "删不存在的 epic/story → 返回 False（不抛 500）",
            "dev API 重启后 25 个 v73-e2e / fk-probe 残留垃圾 epic 全部 DELETE 200 清空",
            "v7.3 e2e 6/6 跑完 teardown 零残留（DELETE /api/epics 100% 生效）",
        ],
        status="done",
        closed_date="2026-08-21",
        notes=(
            "v7.3 e2e 收尾发现的产品级 bug：Epic 140 切片 1/3 引入 task_outcome / "
            "episode_embedding / project_playbook* 后（旧 facade 没跟进清理）→ "
            "task 走 done 落 learning 引用 → 用户删 epic/story → SQLite 抛 "
            "FOREIGN KEY constraint failed → HTTP 500。\n"
            "根因：agentboard/features/projects/service.py 的 delete_epic(:499) / "
            "delete_story(:829) 走裸批量 delete（只清 Comment+Task+Story+Epic），"
            "绕过了 agentboard/service.py:1032 中央 delete_task 的防御性级联。\n"
            "修复策略：\n"
            "1) delete_epic / delete_story 改为逐 task 调中央 delete_task（自带动所有 "
            "learning/dependency/comment/attachment 清理），单实现多入口；\n"
            "2) delete_epic 同步解绑 agent_schedules.epic_id (NO ACTION FK 防御，置 NULL 保留 schedule)；\n"
            "3) delete_epic / delete_story 同步切断 review_votes entity_id 锚点 (置 -1，"
            "保留 vote 历史但截断对已删 story 的引用)；\n"
            "4) 中央 delete_task 同步补 ReviewVote.comment_id 防御：删 task comment 前先 "
            "NULL 化 vote.comment_id（防 NO ACTION FK 撞）。\n"
            "性能：每次 commit 独立，delete 是非热路径，可接受。\n"
            "回归保护：tests/test_delete_cascade_fk.py 7 个 case 覆盖全部 NO ACTION FK 洞 + 边界。"
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
