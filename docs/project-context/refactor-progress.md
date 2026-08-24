# AgentBoard 重构目标与进度

> 本文档追踪 **当前活跃的重构/演进** 状态,不是历史归档。
> 维护规则：每条目标 / 每个任务如有变更,先改本文件再改代码。
> 评审周期：双周一次（与 Sprint 同步）,重大变更即时更新。

最后更新：2026-08-24

---

## 1. 总览

| # | 重构线 | 状态 | 关键节点 | 文档 |
|---|---|---|---|---|
| A | 后端 9 阶段垂直切片 | ✅ 完成 | 2026-08-14 全量 push | `docs/refactor-plan.md` |
| B | Epic 149 前端拆 tab | 🟡 进行中 5/8 | 阶段 0 冻结契约 / 5 个 tab 已抽 | `docs/design-prototypes/layout-rebuild/codex/MIGRATION.md` |
| C | 双栈 BFF 演进 | 🟡 Stage 1 backend hardening | 事务、query layer、AI proxy、security gates、application events | `docs/architecture-v2.md` |
| D | 仓库清理 2026-08-19 | ⬜ 未开始 | 4 任务 / 0 完成 | `docs/superpowers/plans/2026-08-19-repository-cleanup.md` |
| E | Epic 11 持续前端优化 | ✅ P/A/B 44 项 | 2026-07-11 收口 | `docs/tasks.md` §Epic 11 |
| F | P1 后端欠债（来自 8-17 评审） | ⬜ 排队中 | 详见 §6 | `MCP project 3 → knowledge` 评审文档 |
| G | **Epic 152 工作台多 Tab** (v2 修) | ✅ 完成 | 2026-08-21 | `docs/e2e-plan.md` §14 |
| H | **Epic 152 工作台 v3 修 (Step 1)** | 🟡 进行中 | 2026-08-21 | `docs/e2e-plan.md` §14 (Step 2 backlog) |

---

## 2. A · 后端 9 阶段垂直切片重构 ✅

**目标**：把 5300 行 `service.py` / 4000 行 `api.py` / 90+ 函数 `mcp_server.py` 拆成企业级垂直切片,201 个测试零破坏。

### 完成矩阵

| # | 范围 | Commit | 状态 |
|---|---|---|---|
| 0 | plan 文档 + 目录骨架 | `4170f3f` | ✅ |
| 1 | `core/` 8 文件 + facade shims | `4170f3f` | ✅ |
| 2 | `features/*/models.py` + `models.py` facade | `b0b29ed` | ✅ |
| 3 | `core/state_machine.py` + Task SM + 8 单测 | `f3a78fa` | ✅ |
| 4 | service 拆 254→8 service + 67 单测 | `871ee2f` 等 5 commit | ✅ |
| 5 | api 拆 179→10 router + schemas + helpers | `3372d0a` | ✅ |
| 6 | mcp helper 拆 features/mcp（87 helper） | `2c93435` | ✅ |
| 7 | worker 迁 features/workers | `ae6dd03` | ✅ |
| 8 | conftest + 工厂 fixture + 6 自检 | `fc8909f` | ✅ |
| 9 | service 删 201 re-bind 老定义 | 5449→2926 行 | ✅ |

### 关键指标对比

| 指标 | 改造前 | 改造后 | 变化 |
|---|---|---|---|
| `api.py` 行数 | ~4000 | 435 | **-89%** |
| `service.py` 行数 | 5449 | 2926 | **-46%** |
| `mcp_server.py` 行数 | 1984 | 1633 | -18% |
| APIRouter 数 | 0 | 10 | 全局 endpoint 可按 feature 测 |
| Pydantic schemas | 散在 api.py | `schemas.py`（58 个） | router 跨模块 ForwardRef 解决 |
| 共享 helper | 内联 | `api_helpers.py`（22 个） | 10 router 共享鉴权/序列化 |
| 测试 fixture | 0 conftest | `tests/conftest.py` + 工厂 + 6 自检 | Phase 8 入口 |

### 收口经验（写入下次重构 plan）

- **facade 模式有效**：业务代码零改动,但需 9 个 commit 增量替换
- **状态机基类复用**：`core/state_machine.py` 让所有 feature 状态机共享
- **AST 架构护栏**：测试守护 facade 边界,防后续漂移

---

## 3. B · Epic 149 前端拆 tab（"上帝组件"拆分） 🟡

**目标**：把 7718 行 `app.ts` / 4700 行 `app.html` / 298 个 signal / 44 处 any 的"上帝组件"按一路由一组件拆开,前端"组件化成熟期"。

### 阶段 0 冻结设计契约（Story 316）

- **v7 静态原型** → `docs/design-prototypes/layout-rebuild/codex/agentboard-home-workspace.html`
- **迁移契约** → `MIGRATION.md`
- **三大组件边界**：
  - `HomeComponent`（`home/projects` / `home/agents`）
  - `WorkspaceComponent`（8 lazy routes）
  - `ManagedListComponent`（独立复用列表）

### 8 个 lazy routes 拆 tab 进度

| # | route | 模块 | Story | 状态 |
|---|---|---|---|---|
| 1 | `overview` | `OverviewModule` | 319 | ⬜ |
| 2 | `kanban` | `KanbanModule` | 319 | ⬜ |
| 3 | `epics` | `EpicsModule` | 319 | ✅ |
| 4 | `workitems` | `WorkItemsModule` | 319 | ✅（backlog + tickets） |
| 5 | `proposals` | `ProposalsModule` | 319 | ✅ |
| 6 | `documents` | `DocumentsModule` | 319 | ✅ |
| 7 | `members` | `MembersModule` | 318 | 🟡 关联 Bug #1290（成员视图缺失） |
| 8 | `settings` | `SettingsModule` | 319 | ⬜ |

> 当前已落 5/8（documents/epics/proposals/backlog/tickets/stats），成员视图待修。

### 设计 token 收口

- **navy ↔ indigo 令牌映射表**：`MIGRATION.md` §3
- 保留 `--grad` 渐变（品牌锚点）
- 替换 `--brand-*` 为 `--blue` / `--blue-dark` / `--navy`
- 新增 `--blue-bright` / `--brand-mark-accent`（P1 修复引入）
- **所有 token 在 `:root` 集中管理,禁止组件内硬编码色值**

### 关联 Bug

| Bug | 描述 | 状态 |
|---|---|---|
| #1290 | Members tab 自动化 E2E 缺成员视图 | 已 FAIL 报告，等修复 |

### 下一步

1. Members tab 修 `#1290` → Story 318 收口
2. Settings / Overview / Kanban 三个 tab 抽组件 → Story 319 8/8
3. `loadRoute()` 200 行 if-else 改为 `routerLinkActive` 驱动
4. 无 `api.service.ts` HTTP 拦截器 → 补
5. `flushOfflineQueue` 假功能 → 决定接真离线队列 or 删除代码

---

## 4. C · 双栈 BFF 演进（FastAPI → .NET 10） 🟡

**目标**：FastAPI 单体 → 双栈 BFF（.NET 80% + FastAPI 内部 AI）→ 最终 .NET 唯一对外。

### Stage 进度

| Stage | 范围 | 状态 | 关键 commit |
|---|---|---|---|
| **0** | 脚手架 + 契约冻结 + health/meta + docker-compose + Serilog/OTel | ✅ done | `ac6f623`~`6de19b4` + `8faee87`（runbook + architecture-v2） |
| 1 | 只读业务迁 .NET（GET projects/epics/stories/tasks） | ⬜ backlog | — |
| 2 | 写迁 .NET + Webhooks/Notifications/SignalR + 灰度切流 | ⬜ backlog | — |
| 3 | FastAPI 业务 router 下架，FastAPI 内部化为 AI service | ⬜ backlog | — |

### Stage 0 关键交付

- ✅ `docs/contracts/contract-freeze.md`（公开 REST 契约冻结）
- ✅ `docs/dual-stack-bff-runbook.md`（30 分钟跑通 + 切流 + 回滚 + FAQ）
- ✅ `docs/architecture-v2.md`（双栈架构图 + 特征归属矩阵 + 数据访问边界）
- ✅ `.github/workflows/dotnet-contract-check.yml`（CI 守门 OpenAPI 快照）
- ✅ `dotnet/contracts/openapi-v3.json`（FastAPI → 快照 → NSwag → 强类型 C# client）
- ✅ docker-compose `api-dotnet` 服务（read-only DB + Serilog/OTel）

### 关键约束

- 公开 REST 契约由 FastAPI 冻结，.NET 端 1:1 镜像
- .NET 读路径使用 AsNoTracking；已迁移写路径必须显式事务，并在行为/权限/恢复验收通过后才能切流
- 双栈查询结果必须 1:1 一致（由契约测试守护）
- 跨栈 trace：.NET 调 FastAPI 透传 `traceparent`

---

## 5. D · 仓库清理 2026-08-19 ⬜

**目标**：移除可重建 + 过时工件，保留本地凭据/业务数据/Agent 记忆/维护中源码/报告/原型。

### 4 任务进度

| Task | 范围 | 状态 |
|---|---|---|
| 1 | 删被忽略的构建产物 + scratch 文件 | ⬜ |
| 2 | 删过时跟踪文件 + 加 narrow ignore 规则 | ⬜ |
| 3 | 整理 `scripts/manual/`（移 3 个 test 脚本） | ⬜ |
| 4 | 验证 + 评审 + commit + push | ⬜ |

### 全局约束

- 永不删 `.env` / `agentboard.db` / `data/` / `agentboard_data/` / `.workbuddy/`
- 不用 `git clean -X`，每条破坏性命令必须点名路径
- 不改维护中的应用行为
- 保留正式 Markdown 报告 / 架构评审 / 命名 HTML 原型

---

## 6. F · P1 后端欠债（来自 2026-08-17 评审） ⬜

> 完整评审见 `MCP project 3 → knowledge`（5 路并行深审，5.8/10 综合）。
> 下面列出影响可维护性的 P1 项，进度实时同步。

| # | 主题 | 范围 | 优先级 | 状态 |
|---|---|---|---|---|
| F-1 | 后端 facade 名不副实 | `service.py` 2926 行仍含 86 个未迁 helper，与 features 存在双份实现 | 中 | ⬜ |
| F-2 | 两套状态机并存 | `core/state_machine.py` 与 `features/proposals/state_machine.py` 时序语义不同 | 中 | ⬜ |
| F-3 | `RUN_TRANSITIONS` 三份 | 散落在 service / features，含一处错字 "succeeded" | 中 | ⬜ |
| F-4 | 测试体系无 CI | 无 pytest.ini / pyproject.toml / conftest.py（部分），无 Python CI | 中 | ⬜ |
| F-5 | `del sys.modules` 67 处 | 测试模块重载导致跨文件全局状态互踩 | 中 | ⬜ |
| F-6 | 56% 测试绑死外部运行环境 | 缺 unit / integration 隔离 | 中 | ⬜ |
| F-7 | AgentRun 认领非 CAS | executor.py 多实例可能重复执行 | 中 | ⬜ |
| F-8 | 离线队列是假功能 | `flushOfflineQueue` 全仓库零调用 | 中 | ⬜ |
| F-9 | 前端状态契约漂移 | 5 值 Status vs 8 态看板列 | 中 | ⬜ |
| F-10 | .NET worker 硬伤 | MiniMaxAdapter 环境变量键名、ProcessExecutor Environment.Clear()、Retry 空操作、门户默认无鉴权 | 中 | ⬜ |

---

## 7. 跨重构线依赖

```
A(后端拆分) ─┬─► F-1(facade 收口)
            ├─► F-2/F-3(状态机/迁移表统一)
            └─► C(双栈 BFF,依赖 service 边界清晰)

B(前端拆 tab)─► F-8(离线队列决定后做)
            └─► F-9(状态契约对齐)

C(双栈 BFF) ─┬─► D(仓库清理,.NET 目录需先稳)
            └─► F-10(.NET worker 硬伤)

E(Epic 11) ── 已完成

F(评审 P1) ── 持续治理,优先级穿插 Sprint
```

---

## 8. 即将要做（Next 30 Days）

1. **Bug #1290 修复**（B 阻塞项）→ Members tab 成员视图
2. **Story 319 8/8** → Settings / Overview / Kanban 三 tab 抽完
3. **Task D-1 ~ D-3** → 仓库清理 3 任务落地
4. **F-4 测试 CI** → `pytest.ini` + `.github/workflows/python-test.yml`
5. **F-1 facade 收口** → service.py 2926 → 1500 行（再迁 86 helper）
6. **README 定位话术** → 落地产品定位一句话

---

## 9. 已完成的近期重构（参考）

| 重构 | 时间 | 关键产出 |
|---|---|---|
| 后端 9 阶段垂直切片 | 2026-08-14 | service/api 拆完，201 测试 0 破坏 |
| Epic 149 Story 319 | 2026-08-19 | 5/8 tab 抽出（documents/epics/proposals/backlog/tickets） |
| 双栈 BFF Stage 0 | 2026-08-19 | .NET 脚手架 + 契约冻结 + docker-compose + runbook |
| Epic 149 Story 320 | 2026-08-19 | indigo→navy 收口 + 暗色主题 + App 死代码清理 |
| Epic 11 UI 风格 15 项 | 2026-07-11 | P-01~P-15（设计 token / 字体 / 品牌 / 暗色） |
| Epic 11 A 类 22 项 | 2026-07-11 | A-01~A-22（看板 / 状态色 / 类型图标 / 行内编辑 / 抽屉 / 快捷键） |
| Epic 11 B 类 6 项 | 2026-07-11 | B-01~B-06（标签 / 负责人 / 截止日期 / 看板拖拽 / 评论 / 分组 + 折叠） |
| 双栈 BFF 7 阶段观测性 | 2026-08-19 | S0-7 Serilog + OpenTelemetry + request middleware |

---

## 维护

- **真源**：每个重构线都有自己的 plan / spec，本文档是 **跨线状态看板**
- **更新策略**：
  - 状态变更（⬜→🟡→✅）→ 即时改本文件
  - 新重构线启动 → 加进 §1 总览 + 新增 §子章节
  - 完成的重构 → 移入 §9 参考
- **下次评审**：2026-09-01
