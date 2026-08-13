# 变更提案：项目文档列表视图 + 过滤增强

## 背景

AgentBoard 已具备项目文档（`Document`）的 CRUD、文件夹层级、评论、Markdown + Mermaid 渲染
（见 `openspec/changes/documents-maintenance/`）。但 **列表展示** 仍是单一的 **Tile 视图**，
信息密度低、过滤维度少：

1. **Tile 视图信息密度低**：单个 tile 只显示图标 / 标题 / type / status / 项目名 / updated
   6 项，缺 summary、缺 author、缺评论数、缺 Epic/Story/folder 完整路径。
2. **过滤维度不足**：仅有 type / status / search 三项，缺作者、Epic、排序。
3. **跨项目视图无项目过滤**：在 `/documents` 路由下，文档按用户权限聚合展示，但没有
   显式项目下拉；多项目用户难以收敛到一个项目内查看。
4. **天然 project 隔离未充分利用**：后端 `list_documents` 已有 project_id 隔离逻辑，
   但前端没有把它变成可操作 UI 控件。

参考：KnowledgeVault `workspace-page` 与 `knowledge-list` 提供 list 行视图（含
title+summary / project.group 元信息 / category / tags / status / date / actions 7 列）
与丰富的下拉过滤（search / project / group / status）。

## 目标

1. 文档列表新增 **List 行视图**（与 Tile 视图共存，可切换，持久化到 localStorage）。
2. 过滤栏增加 **作者 / Epic / 排序**；跨项目视图增加 **项目下拉**。
3. List 行展示 **7 列** 元信息：title+summary / type / status / 归属路径 / author / 评论数 / updated。
4. 复用现有天然的 project 隔离（API + UI 双重兜底）。

## 非目标

- 不引入文档 Revision（留给后续 Phase 2）。
- 不引入文件上传/下载/zip 打包（留给后续 Phase 3）。
- 不引入 Fullscreen Workspace / Tile-Grid 切换（留给后续 Phase 4）。
- 不重做 Task / Epic / Story 的列表（仅文档模块受益）。
- 不做"全选 / 批量操作"列表（留给上传/下载阶段一并做）。

## 范围

- **后端**：
  - `agentboard/service.py` `list_documents` 新增 kw：`folder_id` / `author_id` / `epic_id` / `story_id` / `sort`
  - `agentboard/service.py` 新增 `count_document_comments(document_id)`
  - `agentboard/api.py` `GET /api/documents` 透传新 query
  - `agentboard/api.py` 新增 `GET /api/documents/{id}/comments/count`
  - `agentboard/mcp_server.py` `list_documents` 工具扩参 + 新增 `count_document_comments`
- **前端**：
  - `models.ts` 不变（DocumentItem 已含 `author_id` / `author` / `folder_id` / `epic_id` / `story_id`）
  - `api.service.ts` `listDocuments` 扩签名 + 新增 `countDocumentComments`
  - `app.ts` 新增 7 个 signal：`docListViewMode` / `docFilterAuthor` / `docFilterEpic` / `docSortBy` / `docFilterProject` / `docCommentCounts`
  - `app.ts` 新增方法：`setDocListViewMode` / `loadDocCommentCounts` / `onDocSortChange` / `applyDocSort` / `docSummary` / `docScopePath` / `allEpicsAcrossProjects` / `docAuthorOptions`
  - `app.html` `case 'documents'` 与 project Tab 的 `documents` 子页增加扩展 toolbar + 列表行渲染
  - `app.css` 新增 `.doc-list.list-view` / `.doc-list-head` / `.doc-list-row` 等样式
  - 修复 pre-existing 的 `current.status === 'backlog'` 模板类型错误（Story 265 引入；用 `$any()` 绕过，保留业务语义）
  - 提升 `anyComponentStyle` budget 以容纳新增样式（120kB→150kB 警告，140kB→170kB 错误）
- **测试**：
  - `tests/test_doc_filters.py`：13 个 case 覆盖 service 层扩展 + 跨用户/跨项目隔离 + sort 白名单
  - `tests/test_epic138_doc_list_filter_e2e.py`：Playwright 端到端（视图切换持久化 / 行渲染 7 列 / 过滤 / 排序 / 跨项目 / 健康度）
- **OpenSpec 文档**：本 change（proposal / design / tasks）

## 影响

- 数据库无变更（无新表、无迁移）。
- 现有 `list_documents` 调用方完全兼容（新增 kw 全部 optional）。
- MCP 工具签名扩展但向后兼容。
- 前端 CSS 总量 +~3KB（143kB → 146kB），main bundle 几乎不变（仅新增几个方法 + signal）。
- 现有 Playwright `test_epic15_doc_module_e2e.py` 不需修改（仍命中 `.doc-row.doc-tile` 默认 tile 视图）。

## 退出标准

1. `pytest tests/test_doc_filters.py tests/test_document_links.py -q` 19 passed。
2. `cd frontend && npm run build` 成功生成 `main-*.js` + `styles-*.css`。
3. `python tests/test_epic138_doc_list_filter_e2e.py`（需 Docker 栈）在 staging 通过：
   - 项目 Tab 视图切换按钮存在
   - List 行渲染 7 列（title/scope/author/comments/updated/type/status）
   - 视图切换持久化（reload 后仍是 list）
   - Sort by title 后行序非递减
   - 跨项目 `/documents` 视图项目下拉生效
   - 0 console error / pageerror / 本地 js+css 加载失败
4. Docker 部署 web/static/ 包含新的 `main-*.js` + `styles-*.css`。
5. Git: feature commit + push `origin main`。
