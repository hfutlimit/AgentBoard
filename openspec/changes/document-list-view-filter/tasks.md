# Tasks：项目文档列表视图 + 过滤增强

## 后端

- [x] `service.py` `list_documents` 新增 kw：`folder_id` / `author_id` / `epic_id` / `story_id` / `sort`
- [x] `service.py` `_DOCUMENT_SORT_WHITELIST` + sort 校验
- [x] `service.py` 新增 `count_document_comments(document_id)`（func.count + NotFound）
- [x] `api.py` `GET /api/documents` 透传 5 个新 query
- [x] `api.py` 新增 `GET /api/documents/{id}/comments/count`
- [x] `mcp_server.py` `list_documents` 工具扩参
- [x] `mcp_server.py` 新增 `count_document_comments` 工具

## 测试（后端）

- [x] `tests/test_doc_filters.py` 13 case：
  - folder / author / epic / story 单独过滤
  - type + status + folder + author 组合
  - sort=title 升序、sort=invalid 抛 InvalidValue、sort 默认 updated desc
  - 无 project + 有 user → 跨用户隔离
  - 无 project + 无 user → 返回全部（admin 场景）
  - count_document_comments：空 / 加 2 条 / 文档不存在 NotFound

## 前端

- [x] `app.ts` 新增 7 signal：`docListViewMode` / `docFilterAuthor` / `docFilterEpic` / `docSortBy` / `docFilterProject` / `docCommentCounts` + 视图模式从 localStorage 初始化
- [x] `app.ts` 新增 9 方法：`setDocListViewMode` / `loadDocCommentCounts` / `docCommentCount` / `docSummary` / `docScopePath` / `docSortLabel` / `onDocSortChange` / `applyDocSort` / `allEpicsAcrossProjects` / `docAuthorOptions`
- [x] `app.ts` `loadDocuments` 转发新 query（folder 不转发 → 用 docFolderId；project 按 project() 或 docFilterProject 决定）
- [x] `app.ts` `loadProjectTab('documents')` 加载后若 list view 触发 `loadDocCommentCounts`
- [x] `app.ts` `docVisible` + `projectDocVisible` 客户端再过滤 author / epic / project
- [x] `app.ts` `openDocModal('edit', target?)` 扩展支持传入 doc 参数
- [x] `api.service.ts` `listDocuments` 扩签名（+ folder_id / author_id / sort）
- [x] `api.service.ts` 新增 `countDocumentComments(documentId)`
- [x] `app.html` 跨项目 `case 'documents'` 工具栏扩展（项目 / 作者 / Epic / 排序下拉 + 视图切换器 + 列表行渲染）
- [x] `app.html` project Tab `documents` 工具栏扩展（作者 / Epic / 排序 + 视图切换器 + 列表行渲染）
- [x] `app.css` 新增 `.doc-list.list-view` / `.doc-list-head` / `.doc-list-row` / `.doc-list-title` / `.doc-list-summary` / `.doc-list-scope` / `.doc-list-author` / `.doc-list-comments` / `.doc-list-updated` / `.doc-list-actions` / `.doc-toolbar--extended` / `.doc-view-switch` / 响应式 <900px 塌缩
- [x] `app.css` `anyComponentStyle` budget 120/140kB → 150/170kB（容纳新 CSS）

## 测试（前端）

- [x] `tests/test_epic138_doc_list_filter_e2e.py` Playwright：
  - 视图切换器存在 + 切到 list + 7 列渲染 + 持久化 + 切回 tile
  - Sort by title 行序非递减
  - Type filter 收敛
  - 跨项目 `/documents` 视图 + 项目下拉
  - 0 console error / 0 pageerror / 0 本地 js+css 失败

## OpenSpec 文档

- [x] `openspec/changes/document-list-view-filter/proposal.md`
- [x] `openspec/changes/document-list-view-filter/design.md`
- [x] `openspec/changes/document-list-view-filter/tasks.md`（本文件）

## 验证

- [x] `pytest tests/test_doc_filters.py tests/test_document_links.py -q` → 19 passed
- [x] `cd frontend && npm run build` → bundle 生成成功（含 drive-by CSS budget 提升 + Story 265 类型绕过）
- [x] `frontend/dist/frontend/browser/*` 复制到 `agentboard/web/static/`
- [ ] `python tests/test_epic138_doc_list_filter_e2e.py`（需 Docker 栈 28080/18000 在跑）→ 计划后续在 staging 跑

## 已知遗留（独立 PR）

- [ ] `current.status === 'backlog'` 用 `$any()` 绕过；正经修复需把 Story workflow state 显式建模
- [ ] `angular.json` CSS budget 提升 30kB；CSS 增长 > 170kB 需拆分组件样式
- [ ] 评论数首次进入 list view 有 ~200ms 延迟；可改为预加载
- [ ] 跨项目 author filter 派生自 documents()；可加"全部成员"开关
