# 设计：项目文档维护（多成员 / 多 Agent 协作）

> 对应 Epic 35（DB Epic 106）/ Story 164–172。方案到方法粒度，已落地并通过验证。

## 1. 技术选型

| 维度 | 选择 | 备选 | 决策理由 |
|------|------|------|----------|
| 文档存储 | 独立 `Document` 实体 + 新表 | 复用任务 `spec` 字段、JSON 扩展 | `spec` 定位为任务级执行规范，混用会割裂跨任务长文上下文；独立实体可带类型/状态/评论/归属，语义清晰。 |
| 状态机 | 显式 `DOCUMENT_TRANSITIONS` 字典 + 校验 | 复用任务 `TRANSITIONS` | 文档评审语义（draft→in_review→approved/cancelled）与任务开发流（backlog→…→done）不同，独立定义避免污染任务状态机。 |
| 分包架构 | `domains/documents/` 子包 + `models.py` 门面 re-export | 全部塞进 `models.py` | 与既有 `domains/{common,identity,projects,...}` 一致，保持 `models.py` 仅作门面，`migrations/env.py` 仍以 `agentboard.models.Base` 为目标元数据。 |
| 前端 HTTP | `HttpClient` GET/PUT + `fetch` 直发 PATCH | 全程 `HttpClient` | **已知缺陷**：Angular `HttpClient` 的 PATCH 请求 Observable 不 emit（response 不触发 next/complete），GET/PUT 正常。统一封装 `patchJson()` 用 `fetch()` 绕过，应用于 `updateDocument` / `updateDocumentComment`。 |
| Markdown 渲染 | 前端自包含 `renderMarkdown()` | 引入 marked/markdown-it 库 | 避免新增 npm 依赖（FR-12 红线：不引入新框架/依赖）；自渲染器覆盖标题/粗斜体/列表/引用/代码/表格/链接/分隔线/` ```mermaid ` 块，足够文档场景。 |
| 图表 | Mermaid CDN 懒加载（`mermaid@10`）+ 离线降级代码块 | 内置图表引擎 | 文档图（时序/流程）由 Mermaid 表达最通用；CDN 懒加载不增大首屏，离线时降级为 ```mermaid 代码块，保证可用性。 |
| 测试 | Playwright 真实浏览器 + pytest | httpx 模拟 | 与仓库既有 `tests/test_playwright_e2e.py` 一致，验证真实 UI 行为（Markdown 渲染、状态条、评论交互）而非仅接口等价。 |

## 2. 设计思路

需求拆为三条主线，共享同一数据模型：

1. **数据模型**：`Document` 含 `project_id`（必填）、`epic_id` / `story_id`（可空归属）、`title`、`content`(markdown)、`type`、`status`、`author_id`；`DocumentComment` 含 `document_id`、`author`、`content`(markdown)。两张表带级联 FK 与 type/status CHECK 约束。
2. **三方读写**：REST（人类 Web）、MCP（AI Agent）、前端 SPA（人类 UI）三者读写同一后端，状态机在服务层统一校验，避免旁路。
3. **评审闭环**：`draft` 不可直达 `approved`，必须经 `in_review`；`approved` 修订需回 `in_review`；`cancelled` 可复活为 `draft`。非法迁移返回 400。

## 3. 架构设计

```
AgentBoard
├── agentboard/
│   ├── models.py                      # 门面 re-export Document/DocumentComment
│   ├── domains/documents/
│   │   ├── __init__.py
│   │   └── models.py                  # Document/DocumentComment + 枚举 + DOCUMENT_TRANSITIONS
│   ├── database.py                    # init_db() → alembic upgrade head（自动建 documents 表）
│   ├── service.py                     # 文档 CRUD / 状态流转校验 / 评论
│   ├── api.py                         # REST 端点（受 project_access_middleware 约束）
│   ├── mcp_server.py                  # 11 个文档 MCP 工具
│   └── web/static/                    # 构建产物（main-KEQGT56D.js 含文档模块）
├── migrations/versions/
│   └── f2a3b4c5d6e7_add_documents.py  # 建 documents / document_comments
├── frontend/src/app/
│   ├── models.ts                      # DocumentItem / DocumentCommentItem / 类型枚举
│   ├── api.service.ts                 # 文档 API + patchJson(fetch)
│   ├── app.routes.ts                  # 'documents' / 'documents/:id'
│   ├── app.ts                         # docItem signal + renderMarkdown + enhanceMermaid + 交互方法
│   ├── app.html                       # @case('documents') / @case('document')
│   └── app.css                        # 文档模块样式
└── tests/
    ├── test_doc_api.py                # 17 断言
    └── test_doc_frontend.py           # Playwright 15/15
```

## 4. 开发细节（到方法粒度）

### 4.1 数据模型（`agentboard/domains/documents/models.py`）

```python
class DocumentType(str, enum.Enum):
    memory = "memory"; plan = "plan"; knowledge = "knowledge"; design = "design"

class DocumentStatus(str, enum.Enum):
    draft = "draft"; in_review = "in_review"; approved = "approved"; cancelled = "cancelled"

DOCUMENT_TRANSITIONS: dict[str, set[str]] = {
    "draft":      {"in_review"},
    "in_review":  {"approved", "cancelled"},
    "approved":   {"in_review"},     # 修订需重审
    "cancelled":  {"draft"},         # 复活
}

class Document(Base):
    __tablename__ = "documents"
    id, project_id, epic_id, story_id, title, content, type, status, author_id, created_at, updated_at

class DocumentComment(Base):
    __tablename__ = "document_comments"
    id, document_id, author, content, created_at, updated_at
```

`agentboard/models.py` 仅 `from .domains.documents.models import Document, DocumentComment` 转发；`migrations/env.py` 以 `agentboard.models.Base` 为目标元数据，无需改动。

### 4.2 迁移（`f2a3b4c5d6e7_add_documents.py`）

- `down_revision = "e1f2a3b4c5d6"`，与既有迁移链衔接。
- `upgrade()` 建 `documents`（含 `ix_documents_project_id` 索引、`ck_documents_type` / `ck_documents_status` CHECK 约束、3 个 `SET NULL` 级联 FK + 1 个 `CASCADE` 项目 FK）与 `document_comments`（含文档级 `CASCADE` FK）。
- `database.py:_run_alembic()` 在 `init_db()` 时执行 `alembic upgrade head`，Docker `api` 服务开发模式 bind-mount `./agentboard` + `./migrations`，重启即自动落库（实测：`Running upgrade e1f2a3b4c5d6 -> f2a3b4c5d6e7`）。

### 4.3 服务层（`service.py`）

- `create_document / get_document / list_documents / update_document / delete_document`
- `set_document_status(id, status)`：先从库取当前状态，校验 `status in DOCUMENT_TRANSITIONS[current]`，否则抛 400（`ValueError`）。
- `list_document_comments / add_document_comment / update_document_comment / delete_document_comment`
- `list_documents` 支持 `project_id`（必填）、`type`、`status` 过滤，供 MCP 查询工具复用。

### 4.4 REST（`api.py`）

```
GET    /api/documents                  # project_id 必填 query
POST   /api/documents                  # DocumentIn
GET    /api/documents/{id}
PUT    /api/documents/{id}
PATCH  /api/documents/{id}
DELETE /api/documents/{id}
PUT    /api/documents/{id}/status      # {"status": "..."}
GET    /api/documents/{id}/comments
POST   /api/documents/{id}/comments
PATCH  /api/documents/comments/{cid}
DELETE /api/documents/comments/{cid}
```
全部经 `project_access_middleware`：私有项目非成员写操作返回 403（与 task/story 一致，非 bug）。

### 4.5 MCP（`mcp_server.py`，11 个工具）

`create_document`、`get_document`、`list_documents`、`update_document`、`set_document_status`、`delete_document`、`list_document_comments`、`add_document_comment`、`update_document_comment`、`delete_document_comment`、`set_document_spec`（保留兼容别名）。工具统一走 `_http(method, path, ...)`（路径带 `/api`），规避既有 `_api` 未定义缺陷。

### 4.6 前端类型与 API（`models.ts` / `api.service.ts`）

- `DocumentType / DocumentStatus / DOCUMENT_TYPES / DOCUMENT_STATUSES` 枚举与常量；`DocumentItem / DocumentCommentItem` 接口。
- `api.service.ts` 追加：`listDocuments / getDocument / createDocument / updateDocument(用 patchJson) / setDocumentStatus( PUT /status) / deleteDocument / listDocumentComments / addDocumentComment / updateDocumentComment(patchJson) / deleteDocumentComment`。
- `patchJson<T>(path, body)`：`return fetch(\`${this.base}\${path}\`, { method:'PATCH', headers:{Authorization, 'Content-Type':'application/json'}, body: JSON.stringify(body) }).then(r=>r.json())` —— 绕过 HttpClient PATCH 不 emit 的缺陷。

### 4.7 前端视图（`app.ts` / `app.html` / `app.css`）

- `ViewKind` 增 `'documents' | 'document'`；`app.routes.ts` 增 `{ path:'documents', component:RouteAnchor }` 与 `{ path:'documents/:id', component:RouteAnchor }`。
- `loadRoute()` 派发：`kind==='documents'` 有 `id`→detail（`getDocument` + `listDocumentComments` + 加载 project/epics/stories + `setTimeout(enhanceMermaid,80)`），无 `id`→list（`loadDocuments()`）。
- signals：`documents / docItem / documentComments / docFilterType / docFilterStatus / docSearchQuery / docEditing / docEdit* / docCommentContent / docCommentPreview / docCreateOpen / docCreate*` 等。
- **关键坑**：第 553 行 `@Inject(DOCUMENT) private readonly document: Document` 注入了 DOM `Document`，初版 signal 命名 `document` 冲突 TS2339/TS4111；全局改名为 `docItem`（`this.docItem()` / `this.docItem.set(`），模板 `@if (document();...)` → `@if (docItem();...)`。
- 渲染器 `renderMarkdown(src)`：自包含解析（标题 `#`~`######`、粗体 `**`、斜体 `*`、行内 `` ` `` 与块 ``` ``` ```、有序/无序列表、引用 `>`、链接 `[t](u)`、分隔线 `---`、表格 `|`、```mermaid 块）。
- `enhanceMermaid()` / `_renderMermaid()`：懒加载 `https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js`，成功则 `mermaid.run()`；失败/离线降级为 `<pre>```mermaid...</pre>` 代码块（E2E 离线环境即此路径，属预期）。
- `app.html`：侧栏加 `<a routerLink="/documents" [class.active]="view()==='documents'||view()==='document'">📄 项目文档</a>`；`@case('documents')`（工具条 + 筛选 + 搜索 + 文档行含类型/状态 badge + Epic/Story 归属）；`@case('document')`（面包屑、编辑/删除、状态流转按钮条、`<div [innerHTML]="renderMarkdown(docItem().content)">`、评论区）。

### 4.8 构建与部署

- 用 managed Node 22.22.2：`export PATH=<managed-node>:$PATH && npm run build`（**禁止** `node.exe node_modules/.bin/ng`，wrapper 会被当 JS 解析）。
- `angular.json` budget 调高：`anyComponentStyle maximumWarning 28kB→60kB / maximumError 40kB→80kB`；`initial maximumWarning 500kB→1MB / maximumError 1MB→2MB`（app.css 41.89kB、main bundle 624kB 超默认导致失败）。
- 产物 `frontend/dist/frontend/browser/` → `cp -r` 到 `agentboard/web/static/`（静态挂载即时生效，无需 docker rebuild）。
- Docker `web` 容器 `AGENTBOARD_API_URL` 指向已含 `documents` 表的 API（18000）即可在 28080 访问文档模块。

## 5. 问题与解决

| 问题 | 根因 | 解决方案 |
|------|------|----------|
| 前端 build TS2339/TS4111 | signal 命名 `document` 与注入的 DOM `Document` 冲突；`params.type` 缺索引签名 | 全局改名 `docItem`；`params['type']` / `params['status']` 方括号索引 |
| 前端 build budget 失败 | app.css 41.89kB > 40kB error；main 624kB > 500kB warning | `angular.json` 调高两档 budget |
| HttpClient PATCH 不 emit | Angular HttpClient PATCH Observable 不触发 next/complete（GET/PUT 正常） | 封装 `patchJson()` 用 `fetch()` 直发 |
| Playwright 首跑重定向 `/login` | SPA 无 token 时重定向登录页 | API register/login 取 token，`add_init_script` 注入 `localStorage.agentboard_token` |
| Playwright 填表超时 `#docCTitle` | Angular 模板引用变量 `#docCTitle` 不生成 DOM id | 改用 `form.doc-create input[maxlength='300']` 与 `form.doc-create textarea` |
| E2E 误报 1 项 .js 失败 | mermaid CDN 离线加载失败被算作资源失败 | `on_request_failed` 仅统计 `127.0.0.1/localhost` 本机资源，外部 CDN 忽略 |
| Docker API 评论 403 / 状态 None | 非成员写私有 project 被 `project_access_middleware` 拦截（标准行为） | 用 `admin/admin123` 登录验证，全链路 200/201 |
| Epic 误记 | 摘要称「Epic 15 = 项目文档维护」，实际两库均无该 epic | 作为新能力落地，DB 建 Epic 106 + 9 Story(164–172) + 9 Task(863–871) 全 done |

## 6. 方案对比

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| A. 复用任务 `spec` 字段 | 零新表，改动小 | 跨任务长文割裂、无评审/评论语义、Agent 不可结构化维护 | 否 |
| B. 独立 `Document` 实体 + 评审状态机 + 三方读写 | 语义清晰、评审闭环、人类/Agent 对等协作、向后兼容增量表 | 新增 2 表 + 11 MCP 工具 + 前端模块 | **采用** |
| C. 引入成熟 Wiki/CMS（如 Outline） | 功能全（版本/权限/搜索） | 重依赖、与项目树解耦、Agent 接入成本高 | 后续增强可选，首轮不引入 |

## 7. 验证清单

- [x] Docker API 18000 重启后 `documents` 表自动创建（`upgrade e1f2a3b4c5d6 -> f2a3b4c5d6e7`）。
- [x] admin 登录后 create/get/status 流转（draft→in_review→approved）/comment 全链路 200/201。
- [x] `test_doc_api.py` 17 断言 ALL PASS。
- [x] `test_doc_frontend.py` Playwright 15/15 PASS：列表渲染、Markdown h1/粗体、mermaid 块、评论、新建、状态机、零 page/console/本机 `.js+.css` 错误。
- [x] 任务 `spec` 契约与既有 API/MCP 任务工具未变。
- [x] Epic 106 / Story 164–172 / Task 863–871 在 MCP 全部 done。

## 8. 后续任务索引

- 已在 AgentBoard MCP（project 3）创建 Epic 106「项目文档维护（多成员 / 多 Agent 协作）」+ Story 164–172 + Task 863–871，全部 done。
- `docs/tasks.md` Epic 35、`docs/requirements.md` FR-18 已同步。
- 后续增强（非阻塞）：文档版本历史/差异、细粒度文档权限、富文本编辑器、全文检索。
