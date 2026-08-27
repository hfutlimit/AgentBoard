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

    # ── Epic 152 / 2026-08-22 v4 / Workspace entity tabs ─────────────
    DodEntry(
        id="epic152-workspace-entity-tabs-2026-08-22",
        feature="Epic / Proposal / Story / Task 详情作为项目工作台实体 Tab 打开",
        date_added="2026-08-22",
        test_files=[
            "tests/e2e_workspace_tabs/test_detail_new_tab_e2e.py",
        ],
        coverage_summary=(
            "5 个 Playwright 真实断言覆盖：普通点击打开实体 Tab / 同一 Epic 复用 / "
            "Epic→Story→Task 全链路与深链接刷新 / 链接保留真实 href / 侧栏模块 Tab 切换不回归"
        ),
        acceptance=[
            "普通点击 Epic → /project/:projectId/epics/:epicId，并保留工作台 Shell",
            "Epics 列表 Tab 与 Epic 详情 Tab 同时存在；再次打开同一 Epic 不重复创建",
            "Proposal 使用同一实体 Tab 契约；新建 Epic/Proposal 成功后直接进入详情 Tab",
            "Story / Task 使用同一实体 Tab 契约；Epic→Story→Task 不跳出项目工作台",
            "Story / Task 项目内深链接刷新后恢复对应详情 Tab",
            "Ctrl/Cmd/中键通过真实 href 保留浏览器原生新标签行为",
            "workspace main 不出现 side panel；列表 pane 的筛选、分页和滚动状态保留",
        ],
        status="done",
        closed_date="2026-08-22",
        notes=(
            "v4 将模块 Tab 和实体 Tab 纳入同一 WorkspaceTabsService。普通点击直接驱动工作台状态，"
            "URL 使用项目内嵌套路由；实体详情加载后更新 Tab 标题。链接仍是普通 href，"
            "因此用户主动的 Ctrl/Cmd/中键新标签行为不需要额外拦截器。"
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

    # ── Stage 0 / 2026-08-26 / Worker 统一执行模型 Stage 0 止血收敛 ──────
    DodEntry(
        id="stage0-worker-resilience-2026-08-26",
        feature="Worker 统一执行层 Stage 0 · 止血与韧性收敛（Story/Task 租约回收 + 路由修复 + 子进程隔离 + MQ 瞬时重试 + 异步执行去重）",
        date_added="2026-08-26",
        test_files=[
            "tests/unit/test_stage0_worker_resilience.py",
            "tests/unit/test_story_async_executor.py",
            "tests/test_mq_consume_reconnect.py",
            "tests/test_wf_mq_consume_reconnect.py",
        ],
        coverage_summary=(
            "16 个 pytest 单测 + 11 个 MQ/异步回归测试覆盖："
            "Story/Task 租约列与 reclaim 端点 / 真实 action 路由键白名单与未知键告警 / "
            "子进程环境变量隔离屏蔽 AGENTBOARD_* 凭据 / MessageRetry 三态重投与指数退避 / "
            "AsyncWorkExecutor per-kind 通道与 (kind, id) in-flight 去重"
        ),
        acceptance=[
            "Story 与 Task 模型补齐 claimed_by / claimed_at 租约列与 status+claimed_at 复合索引",
            "新增 POST /api/stories/reclaim-stale 与 POST /api/tasks/reclaim-stale 端点，支持超时租约安全回收",
            "ProposalWorker 与 WorkflowConsumer 维护循环周期调用 reclaim_stale_stories / reclaim_stale_tasks",
            "RoutedSubprocessInvoker 路由白名单对齐真实 action (review_task / process_task)，未知键警告忽略",
            "SubprocessAgentInvoker 启动子进程仅放行 AgentBoard MCP API Key，剥离其余 AGENTBOARD_* Worker 凭据并注入 UTF-8 编码参数",
            "RabbitMQ / InMemoryBroker 消费端支持 MessageRetry 三态判定，瞬时失败 requeue 避免误入死信",
            "AsyncWorkExecutor 支持 per-kind 通道隔离与 (kind, id) in-flight 去重，防止慢任务阻塞主循环",
        ],
        status="done",
        closed_date="2026-08-26",
        notes=(
            "Worker 统一执行模型 Stage 0 止血修复收敛：\n"
            "1) 补齐 Story/Task 侧租约回收机制，根治 Worker 崩溃后 Story 卡 todo / Task 卡 in_progress 的问题；\n"
            "2) 修复 RoutedSubprocessInvoker 路由键失配，将历史近似键（review/story/task）归一化，未知键警告过滤；\n"
            "3) 强化子进程隔离：剥离 AGENTBOARD_* 凭据变量，强制 UTF-8 编码；\n"
            "4) MQ 消费链路支持 MessageRetry 三态判定，网络抖动与 5xx 自动退避 requeue；\n"
            "5) AsyncWorkExecutor 泛化至 clarify / ticket / story 域，支持按域隔离与去重。"
        ),
    ),

    # ── Stage 1-2 / 2026-08-26 / Worker 统一执行模型与 Server 编排收缴 ──────
    DodEntry(
        id="stage1-2-worker-unified-execution-2026-08-26",
        feature="Worker 统一执行模型 Stage 1 & 2 · 统一执行抽象与 Server DAG 编排收缴（WorkerCoordinator + WorkType + ExecutionCommand/Result + Server 自动结项）",
        date_added="2026-08-26",
        test_files=[
            "tests/unit/test_worker_coordinator.py",
            "tests/unit/test_stage0_worker_resilience.py",
            "tests/test_backend_flow.py",
            "tests/test_agent_mq_consumer.py",
        ],
        coverage_summary=(
            "7 个 Coordinator 单测 + 全量 209 个单元测试 + 35 个端到端集成测试全绿："
            "ExecutionCommand / WorkType / ExecutionResult 契约定义 / "
            "WorkerCoordinator 单一进程调度中枢与 HandlerRegistry[WorkType] 分发 / "
            "Proposal/Task/Review 5 类 Handler 策略类收敛与 execute_command 实现 / "
            "Server 评审 Approve 后自动检查 Story 下所有任务全完成并自动结项 (complete_story)"
        ),
        acceptance=[
            "引入统一执行契约 ExecutionCommand、ExecutionResult 与 WorkType 枚举",
            "实现单一常驻 WorkerCoordinator 类，提供统一 dispatch(command) 与 HandlerRegistry",
            "5 个 Handler (Clarify, Ticket, Story, Review, OwnerResponse) 继承 BaseWorkHandler 并实现 execute_command",
            "Server 端在 review_task approve 判定时自动检索 DAG 依赖并解锁后继任务；若 Story 下所有任务均已 DONE 则自动触发 Story complete 收尾",
            "修复 delete_epic / delete_story 删除 Comment 前先解绑 ReviewVote.comment_id 避免 NO ACTION FK 约束冲突",
        ],
        status="done",
        closed_date="2026-08-26",
        notes=(
            "Worker 统一执行模型 Stage 1 & 2 终态落地：\n"
            "1) 业务领域实体（Proposal, Story, Task）与执行单元（ExecutionCommand/WorkType）彻底解耦；\n"
            "2) 废除多进程分散轮询与割裂执行循环，收敛为 WorkerCoordinator 统一进程分发；\n"
            "3) Worker 只负责纯执行与结果上报，跨实体的 DAG 推进、后继任务解锁与 Story 自动结项全部由 Server 状态机集中调度；\n"
            "4) 保持原有 CLI 入口与 REST 端点 100% 向后兼容。"
        ),
    ),

    # ── Stage 3 / 2026-08-26 / Worker 统一执行模型 Stage 3 细粒度业务类型与驳回重试收敛 ──────
    DodEntry(
        id="stage3-worker-unified-granularity-2026-08-26",
        feature="Worker 统一执行模型 Stage 3 · 细粒度正交 WorkType 与 Server Re-attempt 收敛（DESIGN / DESIGN_REVIEW / IMPLEMENTATION / QA / QA_REVIEW + 驳回回归 Re-attempt）",
        date_added="2026-08-26",
        test_files=[
            "tests/unit/test_worker_coordinator.py",
            "tests/unit/test_stage0_worker_resilience.py",
            "tests/test_agent_mq_consumer.py",
        ],
        coverage_summary=(
            "10 个 Coordinator 单测 + 全量 236 个单元及集成测试全绿："
            "WorkType 细粒度枚举（DESIGN / DESIGN_REVIEW / IMPLEMENTATION / QA / QA_REVIEW）与 from_task() 正交映射 / "
            "RoutedSubprocessInvoker 优先根据上下文 work_type 精准选路对应 Agent profile / "
            "评审驳回 (task.rejected) 彻底统一为 Server 状态机触发 attempt+1 的 Re-attempt 执行指令"
        ),
        acceptance=[
            "WorkType 枚举扩充一等公民正交业务执行类型：DESIGN, DESIGN_REVIEW, IMPLEMENTATION, IMPLEMENTATION_REVIEW, QA, QA_REVIEW",
            "提供 WorkType.from_task(task_type, is_review) 工具方法，消除 Worker 二次推断 Task 内容的逻辑",
            "RoutedSubprocessInvoker 支持显式按 work_type 选路，Agent 路由与业务类型一等公民绑定",
            "Review 驳回不再作为特殊的 owner_response action 分支，由 Server 状态机驱动递增 attempt 后重新下发 implementation/design 指令",
        ],
        status="done",
        closed_date="2026-08-26",
        notes=(
            "Worker 统一执行模型 Stage 3 终态全面达成：\n"
            "1) 彻底消除了 Worker 内部针对 Task 内容解析二次推断 Agent Profile 的模糊逻辑；\n"
            "2) 彻底消解了 owner_response 特殊 action，统一回归为 Server 状态机控制的 attempt 递增执行流；\n"
            "3) 坚持 Domain Model（Proposal/Story/Task 表结构与业务逻辑）与 Execution Model（WorkType/Command/Result）完全解耦的架构原则。"
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
