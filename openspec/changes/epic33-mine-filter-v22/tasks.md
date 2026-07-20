# 任务拆分：Epic 33 v2.2 任务列表「只看我」筛选

## Task 718（high）：任务列表快速筛选「只看指派给我」
- [x] `app.ts` 新增 `filterMineOnly` signal（读取 `localStorage.agentboard_filter_mine`）
- [x] `app.ts` 新增 `myUserId()` 计算（currentUser + members 映射）
- [x] `app.ts` 新增 `toggleFilterMine()` 切换 + 持久化
- [x] `app.ts` `visibleTasks` 末尾追加 assignee 过滤（成员已加载且命中时生效）
- [x] `app.ts` `activeFilterCount` 计入该筛选
- [x] `app.ts` `clearFilters()` 一并清除
- [x] `app.html` 工具条新增「只看我」切换按钮
- [x] `app.css` 新增 `.mine-toggle` 样式（复用 qf-chip 视觉）
- [x] Playwright e2e：过滤生效 / 与计数联动 / 持久化 / 还原
- [x] `npm run build` 通过，产物 cp 至 `agentboard/web/static/`
- [x] Playwright 端到端验证：0 控制台 / 资源 / 页面错误，登录 / 项目 / 任务列表 / 筛选核心功能正常
- [x] 既有 e2e 回归（Epic 31/32/34/35/36/v1.9）全绿
- [x] 标记 Task 718 → in_review，Story 69 / Epic 68 → in_review/done 同步
