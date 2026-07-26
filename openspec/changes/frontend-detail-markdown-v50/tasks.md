# Tasks: Story/Epic 详情页描述 Markdown 渲染（v5.0）

## 实现
- [x] Epic 详情页描述由 `text-pre` 改为 `[innerHTML]=renderMarkdown` + `.task-md`，含空态 `.task-md-empty`
- [x] Story 详情页描述由 `text-pre` 改为 `[innerHTML]=renderMarkdown` + `.story-description.task-md`，含空态 `.task-md-empty`
- [x] 复用 `.task-md`/`.task-md-empty`（v4.9 样式，含 dark），零新增 CSS
- [x] 前端构建 `npm run build`（managed node 22.22.2，清 `.angular/cache`）→ cp `dist/frontend/browser/.` → `agentboard/web/static/`（新包 `main-6IRL5C5X.js`）

## 验证
- [x] E2E `tests/test_epic63_v50_detail_md_e2e.py`：Story/Epic 详情页 Markdown 渲染（h1/h2/strong/em/ol/ul/li/blockquote/code/a）+ 无原始 `**` 标记 + 0 pageerror/console/.js+.css 404
- [x] 回归：后端 `pytest test_epic30_cache.py` 8 passed；E2E v4.9 任务详情 Markdown / v4.5 抽屉导航 全绿

## 状态流转
- [x] Task 1118 → in_review（backlog→todo→in_progress→in_review 合法链）
- [x] Story 102 → in_review；Epic 53 → in_review
