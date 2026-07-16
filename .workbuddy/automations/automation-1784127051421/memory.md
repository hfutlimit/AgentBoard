# Automation 1784127051421 Execution Memory

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
