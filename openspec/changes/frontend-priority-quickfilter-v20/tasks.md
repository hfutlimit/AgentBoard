# 任务清单：任务列表优先级快速筛选 chips

## 实现任务
- [x] T1 `app.ts`：`filterPriorities` 初始化读取 `localStorage.agentboard_quick_priority`
- [x] T2 `app.ts`：新增 `priorityCounts` computed（各优先级计数）
- [x] T3 `app.ts`：新增 `setQuickPriority(p)` 单选切换 + 私有 `persistQuickPriority()` 持久化
- [x] T4 `app.ts`：`toggleFilterPriority` / `clearFilters` 末尾调用 `persistQuickPriority()`
- [x] T5 `app.html`：工具条新增 `.task-quickfilter-bar` chip 行（全部 + 5 级优先级，计数）
- [x] T6 `app.css`：补充 chip / active / count 样式（主题适配）

## 验证任务
- [x] V1 `npm run build` 通过（无 budget / 编译错误）
- [x] V2 启动 web 服务，Playwright 访问 Story 任务列表，确认 chips 渲染、计数、点击筛选
- [x] V3 Playwright 验证 localStorage 持久化（点击高优先级 → reload → 仍高亮且列表已筛）
- [x] V4 控制台 / 网络零错误（仅计 .js/.css 失败；/api ERR_ABORTED 良性忽略）
- [x] V5 新增 `tests/test_epic31_priority_quickfilter_e2e.py` 并通过
- [x] V6 运行既有回归（Epic 34 汇总栏 / Epic 35 搜索 / Epic 36 内联编辑 / v1.9 折叠）确保无回归

## 关联 MCP
- Epic 31 (id=66) → in_progress
- Story 67 (id=67) → in_progress
- Task 716 (id=716) → in_review（验收通过后）
