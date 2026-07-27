# Tasks: 命令面板接入 Story/文档后端搜索（v5.7）

## 1. 后端 Story 搜索能力
- [x] `service.py` 新增 `search_stories(s, q, limit=20)`（标题/描述 ilike + id 倒序 + limit）。
- [x] `api.py` 新增 `GET /api/search/stories?q=&limit=`（避开 `/api/stories/{sid}` 路由冲突）。
- [x] 本地 uvicorn 58125 重启 + docker `agentboard-api-1` restart 生效。

## 2. 前端命令面板扩展
- [x] `api.service.ts` 新增 `searchStories()`（30s TTL 缓存）。
- [x] `app.ts`：`PaletteCommand.category` 扩展 `'story'|'document'`；新增 `paletteStoryResults`/`paletteDocumentResults` 信号并在 `openPalette`/`closePalette` 重置。
- [x] `app.ts`：`paletteRunSearch()` 并行拉取 Story + 文档；`paletteItems` 合并四类结果。
- [x] `app.html`：分类标签 ternary 扩展 Story/文档；placeholder 提示更新。
- [x] `styles.css`：新增 `.cat-story` / `.cat-document` 分类色。
- [x] `npm run build` → `cp` 静态产物至 `agentboard/web/static/`。

## 3. 验证与测试
- [x] E2E `tests/test_epic70_v57_palette_story_doc_e2e.py`：Story/文档搜索结果渲染、点击跳转（/story/{id}、/documents/{id}）、空态、0 错误 → ALL PASS。
- [x] 回归：`pytest test_epic30_cache.py` 8 passed；`test_epic69_v56_palette_search_e2e.py` / `test_epic67_v54_command_palette_e2e.py` ALL PASS（无回归）。
- [x] 追踪实体（project 59 / epic 60 / story 109 / task 1125）经 REST 置 in_review。

## 4. 文档
- [x] `openspec/changes/frontend-palette-story-doc-v57/{proposal,design,tasks}.md`。
