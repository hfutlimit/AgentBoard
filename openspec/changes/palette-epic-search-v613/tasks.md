# Tasks: 命令面板接入 Epic 后端搜索（v6.13）

## Task 1: 后端全局 Epic 搜索端点

- [x] `agentboard/service.py` 新增 `search_epics(s, q, limit=20)`（title/description ilike + id desc）。
- [x] `agentboard/api.py` 新增 `GET /api/search/epics`（q 必填、limit 1..50；避开 `/api/epics/{eid}`）。
- [x] pytest `tests/test_epic119_search_epics.py` 8 用例：service 标题/描述匹配、limit 与 id desc 顺序、无匹配、端点 200 结构与序列化、路由不被 `{eid}` 捕获、q 必填与 limit 上限、与 `/api/search/stories` 并存。

## Task 2: 前端命令面板接入 Epic 结果

- [x] `api.service.ts` 新增 `searchEpics({q, limit})`（30s TTL 缓存）。
- [x] `app.ts`：`category` 联合加 `'epic'`；`paletteEpicResults` 信号（open/close/短查询清空）；`paletteRunSearch` 第 5 分支；`paletteItems` 合并。
- [x] `app.html` 分类标签补 `epic→Epic`；`styles.css` `.cat-epic` 绿色系。
- [x] Playwright `tests/test_epic119_v613_palette_epic_e2e.py`：Ctrl+K → 输入 token → `.cat-epic` 结果 → 点击跳转 `/epic/{id}` → 无匹配空态 → 0 pageerror/console/js·css 404。

## Task 3: 回归与部署

- [x] `pytest tests/test_epic119_search_epics.py` 8 passed；`pytest tests/test_epic30_cache.py` 单独 7 passed/1 skipped（并跑时 sys.modules 污染为既有测试基础设施问题）。
- [x] 前端 `ng test` 29 passed。
- [x] E2E 主应用回归（overview 首页 + 项目页）通过。
- [x] 部署：`npm run build` → cp `dist/frontend/browser/.` → `agentboard/web/static/`；`docker restart agentboard-api-1 agentboard-web-1`（未触碰 18001）。
