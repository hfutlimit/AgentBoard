# Tasks — Dashboard 首页加载性能优化（跨项目聚合统计端点）

epic: 117
story: 223

## 任务清单

- [x] 后端 `service.get_overview(s, user_id)`：counts / projects / status_distribution / activity_7d
- [x] 后端 `GET /api/overview` 端点（可见性复用 list_accessible_projects）
- [x] 后端单测 `tests/test_overview.py`（结构 / 权限 / 一致性 / API 直调）
- [x] 前端 `models.ts` OverviewStats + `api.service.ts getOverview()`（15s TTL 缓存）
- [x] 前端 `app.ts`：overviewStats 信号 + loadDashboard 两阶段化 + computed overview 优先
- [x] 前端 `app.html`：hero 与 stats-row 统计卡改读 stat* computed
- [x] 前端单测 `app.spec.ts` 新增 overview 优先用例
- [x] Playwright E2E：首页秒出 + 统计卡/图表渲染 + 0 报错
- [x] 回归：前端单测全量 + 后端聚焦 pytest
- [x] 部署验证 + git commit/push + MCP 状态流转 in_review
