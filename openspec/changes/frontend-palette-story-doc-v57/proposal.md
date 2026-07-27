# Change: 命令面板接入 Story/文档后端搜索（v5.7）

## Why
命令面板（v5.4 引入、v5.6 接入任务/项目后端搜索）当前仅能搜索「任务」与「项目」实体。Story 与文档是 AgentBoard 的核心实体，用户在命令面板中无法快速跳转到指定 Story 或文档，需退回列表页逐级查找，体验割裂。补齐 Story/文档搜索可让命令面板成为真正的全局快速导航入口。

## What Changes
- 后端（agentboard）：
  - `service.py` 新增 `search_stories(s, q, limit)`：跨全库按标题/描述 ilike 模糊搜索 Story。
  - `api.py` 新增 `GET /api/search/stories?q=&limit=`（路径避开与 `/api/stories/{sid}` 冲突；`/api/documents?q=` 已原生支持文档搜索，前端直接复用）。
- 前端（frontend/src/app）：
  - `api.service.ts` 新增 `searchStories()`（带 30s TTL 缓存，与 `searchTasks` 一致）。
  - `app.ts`：
    - `PaletteCommand.category` 联合类型扩展 `'story' | 'document'`。
    - 新增 `paletteStoryResults` / `paletteDocumentResults` 信号；`openPalette()` / `closePalette()` 同步重置。
    - `paletteRunSearch()` 在任务/项目搜索基础上并行拉取 Story（`/api/search/stories`）与文档（`/api/documents?q=`），结果写入信号。
    - `paletteItems` computed 合并四类后端结果（任务 + 项目 + Story + 文档）。
  - `app.html`：分类标签 ternary 扩展 Story/文档中文；输入框 placeholder 提示「任务 / 项目 / Story / 文档」。
  - `styles.css`：新增 `.cat-story`（青）/ `.cat-document`（橙）分类色标签，复用既有 `.palette-item-cat` 主题变量。
- 部署：API 通过本地 uvicorn 58125 重启 + docker `agentboard-api-1` restart 生效（bind-mount 只读挂载，无需 docker cp）；前端构建产物 `cp` 至 `agentboard/web/static/`，web 8090 / 28080 即时生效。

## Impact
- 新增 1 个只读 GET 端点（无契约破坏，不改动既有端点行为）。
- 命令面板成为任务/项目/Story/文档四类实体的统一快速跳转入口。
- 新增 E2E `tests/test_epic70_v57_palette_story_doc_e2e.py` 覆盖 Story/文档搜索结果渲染、点击跳转、空态与 0 控制台/页面/404 错误。

## Status
Implemented（in_review）
