# 任务清单：项目文档维护（多成员 / 多 Agent 协作）

> 本任务清单已在 AgentBoard MCP（project 3）中创建 Epic 106 及下属 Story 164–172 / Task 863–871，此处为索引与设计摘要。MCP 为唯一真相源。全部状态：`done`。

## MCP 索引

- **Epic 106**：项目文档维护（多成员 / 多 Agent 协作）
- **Story 164**：文档数据模型与迁移
- **Story 165**：文档 REST CRUD
- **Story 166**：文档评审状态机与状态流转
- **Story 167**：文档评论 API
- **Story 168**：MCP 文档工具集（11 个）
- **Story 169**：前端文档列表页
- **Story 170**：前端文档详情页（Markdown/Mermaid 渲染 + 评审状态条）
- **Story 171**：前端文档新建与内联编辑
- **Story 172**：前端文档评论区 + 端到端验证

## Story 164 任务（数据模型与迁移）

1. 新建 `agentboard/domains/documents/__init__.py` 与 `models.py`（`Document` / `DocumentComment` / 枚举 / `DOCUMENT_TRANSITIONS`）。
2. `agentboard/models.py` 门面 re-export `Document, DocumentComment`。
3. 编写 Alembic 迁移 `migrations/versions/f2a3b4c5d6e7_add_documents.py`（`down_revision="e1f2a3b4c5d6"`，建两表 + CHECK 约束 + 级联 FK）。
4. 验证 `init_db()` → `alembic upgrade head` 自动落库（Docker 实测 upgrade 成功）。

## Story 165 任务（REST CRUD）

1. `service.py` 实现 `create_document / get_document / list_documents / update_document / delete_document`。
2. `api.py` 实现 `GET/POST /api/documents`、`GET/PUT/PATCH/DELETE /api/documents/{id}`。
3. 受 `project_access_middleware` 约束（私有项目非成员写 403）。

## Story 166 任务（评审状态机）

1. `DOCUMENT_TRANSITIONS`：`draft→in_review→approved/cancelled`、`approved→in_review`、`cancelled→draft`。
2. `service.set_document_status` 校验合法迁移，非法返回 400。
3. `api.py` 实现 `PUT /api/documents/{id}/status`。

## Story 167 任务（评论 API）

1. `service.py` 实现 `list_document_comments / add_document_comment / update_document_comment / delete_document_comment`。
2. `api.py` 实现 `GET/POST /api/documents/{id}/comments`、`PATCH/DELETE /api/documents/comments/{cid}`。

## Story 168 任务（MCP 工具集）

1. `mcp_server.py` 新增 11 个文档工具：CRUD、状态流转、评论读写、按 project/type/status 查询。
2. 统一走 `_http(method, path, ...)` 规避既有 `_api` 未定义缺陷。

## Story 169 任务（前端列表页）

1. `models.ts` 增加 `DocumentItem` / `DocumentCommentItem` / 类型枚举。
2. `app.routes.ts` 增 `documents` / `documents/:id` 路由。
3. `app.ts` 增 `documents / docFilterType / docFilterStatus / docSearchQuery` signals 与 `loadDocuments()` / 过滤搜索方法。
4. `app.html` 侧栏「📄 项目文档」导航 + `@case('documents')` 列表（筛选 + 搜索 + 类型/状态 badge + 归属）。
5. `app.css` 文档模块样式。

## Story 170 任务（前端详情页）

1. `app.ts` 增 `docItem / documentComments / docMermaidReady` signals + `getDocument` 详情加载 + `renderMarkdown()` 自渲染器 + `enhanceMermaid()` Mermaid 懒加载/降级。
2. `app.html` `@case('document')`：面包屑、编辑/删除、状态流转按钮条、Markdown 内容 `[innerHTML]`、评论区。
3. 评审状态条 `draft→in_review→approved/cancelled` 按钮联动。

## Story 171 任务（前端新建与内联编辑）

1. `api.service.ts` 增 `patchJson()`（fetch 绕过 HttpClient PATCH 缺陷）。
2. `app.ts` 增 `docCreateOpen / docCreate*` signals + `openDocCreate / createDocument / openDocEdit / saveDocEdit / setDocStatus / deleteDoc`。
3. `app.html` 新建表单（标题/类型/内容/Epic/Story）+ 详情页内联编辑（标题/内容/类型）。

## Story 172 任务（评论区 + 端到端验证）

1. `app.ts` 增 `docCommentContent / docCommentPreview` + `addDocComment / saveDocComment / deleteDocComment / toggleDocCommentPreview`。
2. `test_doc_api.py`：17 断言覆盖 CRUD / 状态机 / 评论（ALL PASS）。
3. `test_doc_frontend.py`：Playwright 真实浏览器 15/15（列表 / Markdown / Mermaid / 评论 / 新建 / 状态机 / 零错误）。

## 依赖关系

```
Story 164 (模型/迁移)
  └─> Story 165 (REST CRUD) ─> Story 166 (状态机) ─> Story 167 (评论 API)
                                        └─> Story 168 (MCP 工具)
Story 169 (列表页) ─> Story 170 (详情页) ─> Story 171 (编辑) ─> Story 172 (评论+测试)
```
