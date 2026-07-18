# Automation 1784127051421 Execution Memory

## 2026-07-18 02:00 Run
- **Outcome**: Epic 34 (DB 24) 前端体验升级 v1.4 → done ✅（任务列表汇总栏）
- **Key deliverable**: Story 详情任务列表工具条下方 `.task-list-summary`——`taskListSummary()` computed（total/done/inProgress/rate/segments）+ 状态分布堆叠条（复用 `--status-*-bg`）+ "共 N 项 · 已完成 X · 进行中 Y · 完成率 Z%" 文案；仅列表模式显示。净增 57 行（app.ts +12 / app.html +12 / app.css +33），符合 R2 <80，不改后端契约。
- **Push**: ✅ origin/main（commit `4e7b4f2`，SSH-over-443，`af9ddb7..4e7b4f2`）
- **Verification**: Playwright E2E `tests/test_epic34_summary_e2e.py`（8080→58125）—— `.task-list-summary` 渲染、3 段堆叠条、文案含 共/完成率、summary total==task list rows 一致性、看板↔列表 摘要消失/重现、零 page/console/.js+.css 错误；回归 Epic 33 E2E 仍 PASS。
- **Issues hit**: ① 后端 pytest 回归挂起（DB fixture 慢，4+ min 无输出）→ 停止；本次纯前端改动，E2E 回归已覆盖。② 发现既有 SPA 路由竞态：直接 `goto(/story/N)` 时 `tasks()` 被全项目任务（153）覆盖而非 story 级（6），因 `loadDashboard()` 预加载与 story 路由 `tasks.set` 竞态——非本次引入，汇总栏与任务列表始终一致。③ Playwright failed-request 须按既有约定只计 .js/.css（/api/* ERR_ABORTED 是良性导航中断）。
- **DB mapping**: Epic 24 / Story 60 / Task 831 全部 done（REST API 流转 backlog→todo→in_progress→in_review→done）。
- **Next run suggestions**: 修复 `loadRoute()` dashboard 预加载竞态（加 `if (kind)` 守卫或取消 in-flight）；清理 project 3 残留副本 task 829/830；继续 Epic 11 Backlog（B-02 负责人指派 / 命令面板 Ctrl+K / 侧栏任务计数徽章）。

## 2026-07-17 01:55-02:55 Run
- **Outcome**: B-04 看板拖拽排序 → done ✅
- **Key deliverable**: Kanban HTML5 drag-and-drop (5 handlers + 2 signals + CSS) + CORS preflight fix (OPTIONS skip) + Playwright E2E
- **Push**: ✅ origin/main (commit 4a486cf)
- **Issues hit**: CORS preflight 429 from rate limiter (middleware order); rate limit frequently hit during dev (300/60s); MCP auth still broken
- **Mitigation**: Fixed OPTIONS skip in rate_limit_middleware; used REST API for all MCP bypass; docker restart api between tests
- **Next run suggestions**: B-02 负责人/指派 (assignee) — backend already has assignee_id, only need frontend dropdown; or B-06「按负责人分组」 (depends on B-02)

## 2026-07-16 01:55-02:25 Run
- **Outcome**: Story 15.3 done ✅
- **Key deliverable**: Dark mode system sync + toggle button (style.css + index.html + Playwright test)
- **Push**: ✅ origin/main (commit b03cbc7)
- **Issues hit**: Docker network loss on restart, MCP DNS stale, API container vs local code version drift
- **Mitigation**: Used API directly for status updates after MCP intermittently rejected integer task_id; force-recreated API container + injected /etc/hosts entry in MCP (port 18001 untouchable)
- **Next run suggestions**: Stories 15.1 (通知) / 15.2 (最近访问); or Task 260/261 (high priority infra)
