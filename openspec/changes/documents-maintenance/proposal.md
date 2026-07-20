# 变更提案：项目文档维护（多成员 / 多 Agent 协作）

## 背景

AgentBoard 已通过任务 `spec` 字段承载「如何做」的规范文档（OpenSpec / Superpowers 风格），但任务 `spec` 的定位是**单任务级、面向执行的规范**，存在以下不足：

1. **粒度错配**：跨 Epic / 跨 Story 的「团队记忆、项目计划、领域知识、架构设计」等长文素材，硬塞进某个任务的 `spec` 会割裂上下文，也难以被检索与评审。
2. **无评审闭环**：`spec` 没有状态，无法表达「草稿 → 评审中 → 已批准」的协作演进；多成员 / 多 Agent 同时改写时没有版本与评审语义。
3. **无独立评论流**：关于某篇文档的讨论只能写进任务评论，无法与文档本身绑定。
4. **Agent 不可直接维护**：MCP 工具只覆盖任务 `spec` 读写，缺少面向「文档」这一协作实体的结构化工具。

## 目标

1. 引入独立实体 `Document`（`documents` 表）与 `DocumentComment`（`document_comments` 表），与任务 `spec` 解耦。
2. 文档支持四类 `type`：`memory`（团队记忆）/ `plan`（计划方案）/ `knowledge`（知识沉淀）/ `design`（设计文档）。
3. 文档自带评审状态机 `DOCUMENT_TRANSITIONS`：`draft → in_review → approved / cancelled`，强制评审闭环。
4. 提供完整 REST 接口、Web SPA 模块、MCP 工具集三条读写路径，使人类与 AI Agent 能同等协作维护文档。
5. Web 端提供 Markdown + Mermaid 自渲染、评论区、新建 / 内联编辑，且**不修改现有 `models.py` / `api.py` 任务契约**。

## 非目标

- 不替换或削弱任务 `spec` 能力；两者长期并存，定位互补。
- 不引入文档版本历史 / 差异对比（后续增强，不阻塞首轮）。
- 不做文档细粒度 RBAC（复用既有 `project_access_middleware` 项目级权限，私有项目仅成员可写）。
- 不引入新的构建链 / 前端框架（沿用现有单组件 Angular SPA）。
- 不做富文本编辑器（首轮采用 Markdown 文本域 + 实时预览）。

## 范围

- **后端**：新增 `agentboard/domains/documents/`（模型 + 枚举 + 状态机）；`agentboard/models.py` 门面 re-export；Alembic 迁移 `f2a3b4c5d6e7_add_documents.py`；`service.py` / `api.py` 新增文档 CRUD、状态流转、评论端点；`mcp_server.py` 新增 11 个 MCP 工具。
- **前端**：`models.ts`（类型）、`api.service.ts`（文档 API + `patchJson` fetch 绕过）、`app.routes.ts`（路由）、`app.ts`（signals + `renderMarkdown` + `enhanceMermaid`）、`app.html`（列表 / 详情两个 `@case`）、`app.css`（文档模块样式）；`angular.json` budget 调高以容纳产物。
- **测试**：`test_doc_api.py`（17 断言）、`test_doc_frontend.py`（Playwright 真实浏览器 15/15）。
- **文档**：`openspec/changes/documents-maintenance/{proposal,design,tasks}.md`、`docs/requirements.md` FR-18、`docs/tasks.md` Epic 35。

## 影响

- 数据库新增 2 张表（`documents` / `document_comments`），通过 `init_db()` 的 Alembic 自动 `upgrade head` 落库，向下兼容（纯增量）。
- 前端包体积略增（文档模块 ~150 行 CSS + 渲染器逻辑）；main bundle 与 app.css 超默认 budget，已调高 `angular.json` 阈值。
- MCP 工具集新增 11 个，不影响既有任务工具。
- 既有任务 `spec` 读写路径完全不变。

## 退出标准

1. Docker 生产栈（API 18000）启动后 `documents` 表自动创建，admin 可走通 list/create/get/status 流转/comment 全链路。
2. Web SPA 「项目文档」模块：列表筛选 / 搜索、详情 Markdown+Mermaid 渲染、状态条、评论增改删、新建 / 内联编辑均可在真实浏览器运行。
3. `test_doc_api.py` 17 断言 ALL PASS；`test_doc_frontend.py` Playwright 15/15 PASS，零 page / console / 本机 `.js+.css` 错误。
4. 任务 `spec` 契约与既有 API 未变；MCP 任务工具未变。
5. 相关 Epic / Story / Task 在 AgentBoard MCP（project 3）状态更新为 done（Epic 106 / Story 164–172 / Task 863–871）。
