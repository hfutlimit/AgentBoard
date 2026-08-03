# Design: Epic 15 文档模块整体验收与状态同步

## 上下文

Epic 15 的实现代码（后端 `domains/documents` + REST + 前端 + MCP 工具）自 2026-07-14 起已陆续合入，但 MCP 侧 Story/Epic 状态全部停留在 backlog。本次为**验收 + 状态同步**，不新增业务代码，不改 REST 契约，不触碰端口 18001 / docker。

## 验收矩阵

| Story | 交付物 | 验证方式 | 结果 |
|---|---|---|---|
| S1 数据模型与迁移 | `domains/documents/models.py` + Alembic | 代码检查 + E2E 创建文档 | ✅ |
| S2 文档 CRUD REST API | api.py documents/folders 端点 | MCP create/list + E2E | ✅ |
| S3 评审状态流转 | service.py DOCUMENT_TRANSITIONS | MCP set_document_status | ✅ |
| S4 评论 API | comments 端点（越权 422） | MCP add/list_document_comments | ✅ |
| S5 前端列表与编辑器 | app.ts signals + 项目 Tab | Playwright 列表/筛选/新建 | ✅ |
| S6 Markdown + Mermaid | renderMarkdown + mermaid | Playwright h1/strong/svg | ✅ |
| S7 前端评论区 | .doc-comment-form | Playwright 发帖 | ✅ |
| S8 MCP 文档工具集 | mcp_server.py 10 工具 | 全链路实测 | ✅ |
| S9 协作闭环 | 评审状态机 + 评论 | MCP + E2E | ✅ |

## 关键决策

1. **不新增代码**：文档模块已完整实现（07-14 至 08-03 多个 commit 累积），本次只做验收证据收集与状态同步，避免重复劳动与回归风险。
2. **验证走真实栈**：MCP 走生产（124.220.44.12），UI 走本地 Docker 栈（28080/18000，bind-mount 当前源码），双端互补。
3. **新增回归资产**：`tests/test_epic15_doc_module_e2e.py` 自包含（可配 `AGENTBOARD_WEB_URL` / `AGENTBOARD_API_URL`），沉淀文档模块 UI 验收为可持续回归。
4. **状态逐级迁移**：生产状态机不支持跨级跳转，Story/Epic 均 `backlog→todo→in_progress→done` 逐步流转。

## 风险与缓解

- 全局 `/documents` 深链不可达 → 测试不依赖全局入口，改验项目级 Tab（当前产品形态）。
- 测试文档污染生产数据 → E2E 用 `[E2E-<ts>]` 前缀，结尾统一 DELETE 清理（本机栈），生产 MCP 验收文档创建后即删。
