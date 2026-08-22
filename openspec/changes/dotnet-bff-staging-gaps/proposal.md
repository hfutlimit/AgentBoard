# .NET BFF Staging Gaps — 切流到 .NET 18099 后暴露的缺口

> Created: 2026-08-23 by minimax m3 (root session review of commits 845b336..228740d)
> Status: **accepted** (B1 fix shipped in 2026-08-23, B2 deferred to Stage 2)

## 背景

2026-08-22 commit `88fc556` 把前端 Angular dev proxy 从 FastAPI 18000 切到 .NET BFF 18099。同日 `228740d` 在 BFF 补了 ~78 个缺失端点 (Sprint/Attachment/AuditLog/TaskDependency/WebhookConfig/ApiKey/Document/DocumentRevision/DocumentFolder/DocumentComment/StoryStatusHistory/TaskStatusHistory + 13 controller)。本机 dev 调试发现两个 🔴 blocker。

## 范围 (Scope)

**B1 (fixed)**: dev SQLite shadow 路径不工作

- 现象: `POST /api/projects` → 500 `SQLite Error 1: 'no such table: projects'`
- 根因: `ReadOnlyConfiguration<T>` 用了 `b.ToTable("projects", t => t.ExcludeFromMigrations())`,EF Core `EnsureCreated()` 跳过这些表,本地 SQLite 是空库 → 所有 ReadOnly 实体写操作 500
- 测试 35/35 没暴露,因为全部用 InMemory provider (InMemory 忽略 ExcludeFromMigrations)
- Fix: 移除 19 处 `t.ExcludeFromMigrations()`,dev/Testing 模式 `EnsureCreated` 会建表;prod 模式 (MariaDB + Alembic) 不受影响 (Program.cs:134 env check 保证 prod 不调 EnsureCreated)
- Guard: 在 base class 注释里加 ADR,警告"不要 `dotnet ef migrations add` ReadOnly 实体 — schema 真源是 Alembic"
- 风险: 移除 ExcludeFromMigrations 后,任何 `dotnet ef migrations add` 都会为这些表生成 DDL → 跟 Alembic 漂移。已用注释 ADR 警示, 后续需要 CI guard (本 Story 不实现)

**B2 (deferred to Stage 2)**: 前端调 `/api/agents*` 端点 BFF 未实现

- 现象: 登录页右下角红色 toast "Agent 列表加载失败: .../api/agents: 404"
- 根因: `228740d` 跳过实现 AgentsController + Scheduling 模块 (5 端点: GET/POST/PUT/DELETE/probe)
- 短期 fix (本 commit): `frontend/src/app/api.service.ts:538-543` 给 `listAgents()` 加 `catchError(() => of([] as AgentRow[]))`,登录页不报红
- 长期 (Stage 2): 在 .NET BFF 加 `AgentsController` + 对应 `ISchedulingProvider`/`AgentSchedule`/`AgentRun` 实体

## 任务 (Tasks)

- [x] **B1-fix-1**: 移除 `ReadOnlyConfiguration<T>` 子类 19 处 `ExcludeFromMigrations()` (1 commit)
- [x] **B1-fix-2**: 更新 base class 注释 ADR (schema 所有权: dev 走 EnsureCreated,prod 走 Alembic,禁 dotnet ef migrations add)
- [x] **B1-verify-1**: `dotnet build` 0 errors
- [x] **B1-verify-2**: `dotnet test` 35/35 通过
- [x] **B1-verify-3**: 重启 .NET BFF,SQLite dev db 包含 19 张业务表
- [x] **B1-verify-4**: Playwright smoke `POST /api/projects` 从 500 → 201
- [x] **B2-frontend-1**: `api.service.ts:listAgents()` graceful degradation (空数组 fallback)
- [ ] **B2-backend-1** (Stage 2, deferred): `dotnet/src/AgentBoard.Api/Features/Scheduling/AgentsController.cs` + `SchedulingProvider.cs` + `AgentSchedule/AgentRun` EF entity + `AgentsRepository.cs`
- [ ] **B2-backend-2** (Stage 2, deferred): e2e 测试覆盖 5 个端点
- [ ] **B1-followup-1** (out of scope): 加 CI guard,拒绝为 `ReadOnlyConfiguration<T>` 子类跑 `dotnet ef migrations add`

## 验收 (Acceptance)

- [x] 本机 `dotnet run --project dotnet/src/AgentBoard.Api` + SQLite shadow db,所有写操作 (Project/Epic/Story/Task/Comment/Sprint/Attachment/AuditLog/TaskDependency/WebhookConfig/ApiKey/Document/DocumentRevision/DocumentFolder/DocumentComment/StoryStatusHistory/TaskStatusHistory) 端点 201
- [x] Playwright 登录页不再报 `/api/agents` 404
- [x] `dotnet test` 35/35 仍过 (回归)
- [x] prod 路径 (MariaDB + Alembic) 不受影响 (Program.cs:134 env check + base class 注释 ADR)

## 关联

- commit 88fc556: 切前端 proxy 到 .NET 18099
- commit 228740d: BFF 补 78 端点,跳过 Agents 模块
- review 报告: `tmp/2026-08-22/dotnet-debug/REVIEW.md` (14 KB, 1🔴+1🔴+3🟠+6🟡+8🔵)
