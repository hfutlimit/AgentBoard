# Tasks: 命令面板接入 Sprint 搜索（v6.14）

- [x] T1 后端 `service.search_sprints(s, q, limit=20)`：`Sprint.title/goal ilike` OR 匹配 + `id desc` + limit（镜像 search_epics）。
- [x] T2 后端 `GET /api/search/sprints` 端点（q/limit 参数，镜像 search_epics_api）。
- [x] T3 前端 `api.service.ts searchSprints()`（镜像 searchEpics，apiCache TTL 30s）。
- [x] T4 前端 `app.ts`：`category` 联合类型 + `paletteSprintResults` 信号 + `paletteRunSearch` sprint 分支（跳转 `/sprint/{id}`）+ `paletteItems` 合并 + 清空分支。
- [x] T5 前端 `app.html` 分类标签补 `sprint→Sprint`；`app.css` `.cat-sprint` 紫色系。
- [x] T6 单测：`tests/test_sprint_search.py`（service 直调 title/goal 匹配、limit、空结果、API 直调 200）；vitest app.spec.ts（Sprint 结果合并 + 分类标签）。
- [x] T7 Playwright E2E：`tests/test_palette_sprint_e2e.py`（Ctrl+K → 输入关键词 → `.cat-sprint` 结果 → 跳转 `/sprint/{id}` 渲染 → 0 console/pageerror/js·css 失败）。
- [x] T8 回归：pytest 聚焦 + vitest 全量；部署 api(restart) + web(cp dist + restart)；MCP 状态 Task/Story/Epic → in_review；git commit + push。
