# AgentBoard 代码规范

> 一切规范的 **真源** 是各 plan / spec / 子文档，本文件是 **跨文件索引 + 当前生效条款速查**。
> 新规范先写 OpenSpec/Superpowers proposal，评审通过后再回填本文件。
> 最近更新：2026-08-20

---

## 1. 提交规范（commit message）

### 1.1 格式

```
<type>(<scope>): <subject>
```

- **type**：`feat` / `fix` / `refactor` / `perf` / `test` / `docs` / `build` / `ci` / `chore` / `style`
- **scope**：模块名（`agentboard` / `frontend` / `dotnet` / `workers` / `docs` / `api` / `mcp` / `web` / `tasks` ...）
- **subject**：中文一句话，祈使语气，无句号

### 1.2 提交策略

- **每个 Story 拆多个小 commit**（后端 9 阶段 9 commit 范本）
- 任何阶段失败可 `git revert <commit>` 回到上一阶段
- **一次提交只做一件事**，便于评审 + 故障定位

### 1.3 工作流偏好（用户级，2026-08-18）

> 全局偏好：每次改完代码必须自动 `git add` + `commit` + `push origin <branch>`，**无需用户单独确认**。
> 例外：探索性/临时调试（后续丢弃）可不 commit。

---

## 2. 前端持续优化迭代纪律（Epic 11，强制）

> 来源：`docs/tasks.md` §Epic 11
> 适用：所有 "前端小优化" / UI 风格类 Story

### 2.1 五条铁律（R1~R5）

| # | 规则 | 解释 |
|---|---|---|
| **R1** | **单交付** | 每周期只做 **一项**（A-xx / P-xx / 经评估的 B-xx）；做完即交付、即 commit，不囤积 |
| **R2** | **范围红线** | 单文件改动为主；一次交付在 `app.js` / `style.css` / `index.html` 等全部前端文件中的新增代码合计 **< ~80 行**；不引入新 npm/打包依赖；不改 `models.py` / `api.py` 契约（除非该项标注"需后端"）。**不得只统计 JS 或"逻辑行"规避口径** |
| **R3** | **完成标准** | 本地起 `api` + `web` 并真实操作该交互；HTTP 200/静态资源关键字检查只算部署冒烟。改 DOM 交互或通用函数（`md()` / `api()` 等）必须补 Playwright；浏览器环境暂不可用时 **必须记录未验证项**，不能写成"手测通过" |
| **R4** | **超限即拆** | 某项偏大时编码前拆回更细子项，本轮只做其一，剩余回写 backlog（保持 unchecked）；审查后发现超限则记录 **流程例外** 与待补浏览器回归，不以"前端逻辑 <~80 行"视为合规 |
| **R5** | **记录** | 完成即勾选对应项 + 追加"完成记录"（日期 + 一句话）；积累 5~8 项可写一份前端演化小结（非强制） |

### 2.2 提交规范

- `feat(ui): 前端小优化 - <一句话描述>`

### 2.3 流程例外处理

- 超 R2 红线 → 保留完成事实，但 **记为流程例外** + 补齐真实浏览器回归
- 不以"逻辑行 <80"为合规借口，**全文件新增合计**才是真口径

---

## 3. 双栈 BFF 契约冻结（强制）

> 来源：`docs/contracts/contract-freeze.md`
> CI 守门：`.github/workflows/dotnet-contract-check.yml`

### 3.1 核心原则

```
[FastAPI /openapi.json] → [dotnet/contracts/openapi-v3.json]
                            ↓ sha256 pin
                            ↓
                          [NSwag] → [dotnet/src/AgentBoard.Api/Clients/AgentBoardFastApiClient.cs]
                            ↓
                          [dotnet build] → [dotnet test]
                            ↓
                          [CI gate]
```

**FastAPI 是公开 REST 契约的单一真源**。.NET 端必须 1:1 镜像。任何契约变更先改 FastAPI → commit OpenAPI 快照 → 重生 C# 客户端。

### 3.2 冻结属性（变更即破坏契约）

| 属性 | 规则 |
|---|---|
| URL path | 完全匹配（大小写、连字符、版本） |
| HTTP method | 完全匹配 |
| Path / query 参数名 | 完全匹配 |
| Request body schema | JSON keys / types / required 1:1 |
| Response body schema | 同上 |
| HTTP status codes | 业务语义 1:1 |
| Error 格式 | `{"detail": "..."}`（**绝不**切 ProblemDetails） |
| Bearer token | `Authorization: Bearer v1.<payload>.<sig>` |
| API Key 格式 | `abk_<digest>` |

### 3.3 变更流程

1. 在 `openspec/changes/<id>/` 提 RFC（proposal + design + tasks）
2. 评审通过 → 先改 FastAPI 契约
3. commit OpenAPI 快照
4. NSwag 重生 C# 客户端
5. `.NET` 端适配编译
6. CI 跑通

---

## 4. 业务铁律（必读）

> 完整版见 `docs/project-context/business-logic.md` §5/§8
> 这里只列 **编码时** 必须遵守的红线

### 4.1 数据层

- ✅ Task 不嵌套（`story_id` 唯一）
- ✅ `description` / `spec` 自由 markdown，无固定 schema
- ✅ API Key 只存 SHA-256 摘要
- ❌ 绝不向 SPEC 强加模板（仅提供生成器）

### 4.2 状态机

- ✅ 单向收敛（`done` 不回 `in_progress`，Bug 例外有 `verifying` 中转）
- ✅ 5 轮未收敛 → 护栏 `blocked`
- ❌ 不允许状态机 "副作用在前" 与 "副作用在后" 混用（Proposal 已踩坑）

### 4.3 事件总线

- ✅ 消息只带实体 ID，状态/数据一律回查 DB
- ✅ at-least-once + CAS 认领 + 租约 + 死信 + 断线自愈
- ✅ 无 MQ 时回退 DB 轮询
- ❌ 不允许消息体携带业务快照

### 4.4 双栈

- ✅ .NET 端 Stage 0/1 永远只读
- ✅ `.NET` 端 EF Core 用 `AsNoTracking()`，NO `Include`
- ✅ 跨栈 trace 透传 `traceparent`
- ❌ 绝不擅自在 .NET 端加 endpoint

### 4.5 安全

- ✅ `validate_runtime_security()` 生产环境 fail-fast
- ✅ 鉴权端点（`register` / `probe`）走 schema 白名单
- ❌ 绝不把 `.env` 烤进 Docker 镜像（`.dockerignore` 必须含 `.env*`）
- ❌ 绝不在前端 `localStorage` 存敏感信息时配合 markdown XSS 漏洞

---

## 5. 测试规范

### 5.1 三层结构

```
tests/
├── unit/           # 纯单元测试（无网络、无 DB）
├── e2e_epic149/    # 真实浏览器 Playwright E2E
├── admin_portal/   # 管理门户集成
├── factories/      # 测试数据工厂
└── conftest.py     # 共享 fixture
```

### 5.2 规则

- ✅ 单元测试 **不** `httpx` 调服务进程，依赖直接注入
- ✅ E2E 用 Playwright 真实浏览器（**不**用 `httpx` 模拟）
- ✅ CI 必跑：unit + integration（当前缺，§refactor-progress F-4 待修）
- ❌ 不在测试里 `del sys.modules` 重载（67 处历史债，逐步清理）
- ❌ 不绑死外部运行环境（56% 用例待迁，§refactor-progress F-6）

### 5.3 完成判定

| 改动类型 | 完成要求 |
|---|---|
| DOM 交互 | Playwright 真实浏览器断言 |
| 通用函数（`md()` / `api()` 等） | Playwright 覆盖 |
| 后端 service | 单测覆盖正常 + 异常 + 边界 |
| REST 端点 | 集成测试 + OpenAPI 快照一致 |
| MCP 工具 | FastMCP 客户端调用验证 |
| 纯样式 | 手测 + 视觉回归（待建） |

---

## 6. 设计 Token 约定（前端）

> 来源：`docs/design-prototypes/layout-rebuild/codex/MIGRATION.md` §3
> 强制：所有 token 在 `:root`（及 `styles/_tokens.scss`）集中管理

### 6.1 体系选择

- **主色**：`navy` 体系（`--navy: #10243e` / `--blue: #2864dc` / `--blue-dark: #174db3`）
- **保留**：`--grad` 渐变（`linear-gradient(135deg, #6366f1 0%, #8b5cf6 55%, #a855f7 100%)`，品牌锚点）
- **保留**：`--violet: #7c3aed`（与 `--grad` 同族，Proposals 徽标用）
- **替换**：`--brand-500/600/700/soft/ring/sh-brand` 全部改为 navy 体系

### 6.2 暗色主题

- `[data-theme="dark"]` 覆盖中性/品牌提亮
- 切换键：`localStorage` 键 `agentboard_theme`

### 6.3 新增 token 流程

1. 在 `MIGRATION.md` §3.3 加新 token
2. 评审通过
3. 改 `:root` + `data-theme="dark"`
4. 禁止组件内硬编码色值

---

## 7. 设计系统组件（Angular）

### 7.1 拆 tab 契约（Epic 149）

- **8 lazy routes**（固定顺序）：`overview` / `kanban` / `epics` / `workitems` / `proposals` / `documents` / `members` / `settings`
- 每个 route 一个 `*-tab` 目录，结构：`*.ts` / `*.html` / `*.css`
- 顶层 `app.ts` 仅负责装配 + 跨 tab 共享 signal

### 7.2 复用列表

- `ManagedListComponent` 抽离主从列表模式
- 暴露 `@Input() items` / `@Input() trackBy` / `@Output() itemSelect`
- 内部管理选中索引 + 键盘可达性（`role="listbox"` / `aria-selected`）

### 7.3 浮层

- 用 `*ngIf` / `@if` / `CdkOverlay`（`@angular/cdk/overlay`）控制
- 关闭由 `Dispose` / `BackdropClick` 自动处理
- 禁止手写 `closeTransient()` + `document` 监听（历史债）

---

## 8. 后端编码风格

### 8.1 目录结构（9 阶段重构后）

```
agentboard/
├── core/                  # 跨切关注点（config / exceptions / state_machine / observability / infrastructure / api）
├── features/              # 垂直切片
│   ├── auth/  projects/  work_items/  proposals/  documents/
│   ├── scheduling/  notifications/  webhooks/  workers/  mcp/
├── api.py                 # [FACADE] → core.api.app
├── service.py             # [FACADE] → features.*.service
├── mcp_server.py          # [FACADE] → features.mcp.server
├── models.py              # [FACADE] → features.*.models
└── worker.py              # [FACADE] → features.workers.main
```

### 8.2 每个 feature 6 件套

```
features/<name>/
├── __init__.py        # 公开 API
├── models.py          # SQLAlchemy ORM
├── schemas.py         # Pydantic (in/out)
├── service.py         # <Name>Service class
├── router.py          # APIRouter
├── state_machine.py   # (可选) 状态机
└── tests/
    ├── conftest.py
    ├── test_models.py
    ├── test_service.py
    ├── test_state_machine.py
    └── test_api.py
```

### 8.3 核心设计原则

1. **显式契约**：每个 service 函数签名明确，允许 `ServiceResult` 模式或抛领域异常
2. **UoW 模式**：`with uow.transaction() as s:` 统一事务边界 + 缓存失效
3. **状态机复用**：`core.state_machine.StateMachine` 基类，所有域状态机继承
4. **APIRouter 分域**：`prefix="/api/projects"` + `tags=["projects"]`，主 `app.py` 只负责装配
5. **Observability 默认开启**：service 函数入口埋点 `log.info` / `metrics.counter.inc()` / `tracer.start_as_current_span()`
6. **Facade 兼容**：老 `from agentboard.models import X` 全部走 re-export，业务代码零改动

---

## 9. 仓库清理约束（持续生效）

> 来源：`docs/superpowers/plans/2026-08-19-repository-cleanup.md`

### 9.1 永不删

- `.env` / `agentboard.db` / `data/` / `agentboard_data/` / `.workbuddy/`

### 9.2 约束

- 不用 `git clean -X`，每条破坏性命令必须点名路径
- 不改维护中的应用行为
- 保留正式 Markdown 报告 / 架构评审 / 命名 HTML 原型

### 9.3 临时文件命名

- 测试临时 DB：`tmp_*.db` / `_test_*.db`（`.gitignore` 已收）
- 一次性报告：`deliverables/e2e_*`（被 §1.3 清理任务纳入 ignore）

---

## 10. 跨项目工作流偏好（用户级，跨项目适用）

> 适用：所有项目（DevPilot / LocalMcpTools / KnowledgeVault / AgentBoard ...）
> 来源：用户 2026-08-18 明确指示

### 10.1 Git 工作流

- 每次改完 → 自动 `commit + push`，**无需用户单独确认**
- `git status` → `git add` → `git commit -m "<type>(<scope>): <desc>"` → `git push origin <branch>`
- 例外：探索性/临时调试（后续丢弃）可不 commit

### 10.2 踩坑沉淀

- 每次踩坑 → 用 AgentBoard MCP `append_agent_memory` 写到对应项目记忆
- 格式：「规则 → 原因/证据 → 适用场景」三段式
- 触发：每条新踩坑当轮就记，不只写 workbuddy 日子

---

## 11. 文档维护规范

### 11.1 何时改

| 文档 | 触发 |
|---|---|
| `business-logic.md` | 业务模型 / ER / 铁律 / 红线变更 |
| `long-term-roadmap.md` | 路线状态 / 决策拍板 / 风险变化 |
| `refactor-progress.md` | 重构线状态变更 / 新重构启动 |
| `coding-conventions.md` | 新规范 / 老规范废弃 / 红线变化 |

### 11.2 流程

1. 业务/技术变更 → 改 plan / spec / 子文档
2. 评审通过 → 同步回 `docs/project-context/*.md`
3. 必要时同步回 AgentBoard MCP `project 3 → memory`
4. commit + push

---

## 维护

- **真源**：各 plan / spec / 子文档
- **更新策略**：新规范先在 OpenSpec/Superpowers proposal 走完，评审通过后回填本文件
- **下次评审**：每月底
