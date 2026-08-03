# Change: Epic 15 文档模块整体验收与状态同步（2026-08-04）

## Why

自动开发巡检发现 **Epic 15（项目文档维护，多成员/多 Agent 协作）实现早已完整落地**，但 9 个 Story（S1-S9）与 Epic 本身在 MCP 权威数据中长期停留在 `backlog`，与真实交付状态严重不一致。文档模块的后端 REST、状态机、前端 UI、MCP 工具、文件夹/拖拽等能力自 2026-07-14 起陆续合入（散落多个 commit），却从未做过整体验收与状态收尾。

## 验收依据（功能已实现且可用）

- **S1 数据模型与迁移**：`agentboard/domains/documents/models.py` — `Document` / `DocumentComment` / `DocumentFolder` 三实体 + `DocumentType` / `DocumentStatus` 枚举；Alembic 双后端迁移。
- **S2 文档 CRUD REST API**：`agentboard/api.py` — `POST/GET /api/documents`、`GET/PATCH/DELETE /api/documents/{id}`、`/api/document-folders` 文件夹 CRUD；`DocumentIn` / `DocumentPatch` / `DocumentFolderIn` / `DocumentFolderPatch`。
- **S3 评审状态流转**：`agentboard/service.py` — `DOCUMENT_TRANSITIONS`（draft→in-review→approved/cancelled/draft；approved→draft），`PUT /api/documents/{id}/status` 非法迁移拒绝；审计映射（api.py L2334）。
- **S4 评论 API**：`POST/GET /api/documents/{id}/comments`、`PATCH /api/document-comments/{cid}`（仅作者，越权 422）。
- **S5 前端列表与编辑器**：`frontend/src/app/app.ts` — `documents/docItem/docFilter*/docModal` signals + 项目级 Tab `/project/{pid}/documents` 列表/筛选/搜索/新建弹窗；`app.html` 项目级文档工作区。
- **S6 Markdown + Mermaid 渲染**：`.doc-content` 渲染 `renderMarkdown(d.content)`，mermaid 块异步渲染 SVG。
- **S7 前端评论区 UI**：`.doc-comment-section` / `.doc-comment-form`（表单提交，无按钮、requestSubmit 触发）。
- **S8 MCP 文档工具集**：`mcp_server.py` 全套 `create_document` / `update_document` / `get_document` / `list_documents` / `delete_document` / `set_document_status` / `add_document_comment` / `list_document_comments` / `update_document_comment` / `search_documents`（全部 `_http` 实现，无 `_api` 残留）。
- **S9 多 Agent 协作闭环**：评审状态机 + 评论承载 review 意见，`memory` 类型文档作为共享记忆载体。

## 实测验证

- **MCP 全链路**（生产 MCP http://124.220.44.12/mcp，testadmin）：create_document → set_document_status(draft→in_review) → add_document_comment → list_document_comments → search_documents(q=端到端验证) → delete_document 全部通过。
- **Playwright E2E**（新增 `tests/test_epic15_doc_module_e2e.py`，本地 Docker 栈 web 28080 / api 18000）：**15/15 PASS** — 项目文档 Tab 渲染、新建文档按钮、筛选/搜索、预建文档列表、markdown h1/加粗、mermaid SVG、新建弹窗、UI 创建即时上列表、评论区发帖、Tab 可重入、0 console error / 0 pageerror / 0 failed js-css。
- **后端回归**：`test_domain_boundaries` + `test_admin_api_key_scope` + `test_epic96_p0_proposals` + `test_epic30_cache` + `test_story_151_notifications` + `test_story_152_favorites` + `test_scheduler` = **39 passed / 1 skipped / 0 failed**。

## 关键发现

- 全局 `/documents` 入口已随导航改版**不可达**（深链 goto 被重定向到 `/projects`），文档功能并入**项目级 Tab**（`/project/{pid}/documents`）——与旧 `#nav-proposals` 移除同一演进模式，属产品现状而非缺陷。

## 状态变更（经 MCP 权威源执行，逐级合法迁移）

- Story 45-53（S1-S9）：`backlog → todo → in_progress → done`
- Task 707（Story 45 下 web 资源契约测试，8/8 PASSED）：`in_review → done`
- **Task 964（新建，highest）：Epic 15 文档模块整体验收与状态同步 → in_review**（本次交付物）
- **Epic 15：`backlog → todo → in_progress → done`（整体收尾）**

## 结论

Epic 15 全部 9 个 Story 实现已验收通过并同步至 `done`，Epic 整体收尾；新增 E2E 回归资产 `tests/test_epic15_doc_module_e2e.py` 进入测试套件。MCP 权威数据与真实交付状态一致。
