# AgentBoard .NET BFF

> Dual-stack BFF — see [`openspec/changes/dual-stack-bff-restructure/`](../openspec/changes/dual-stack-bff-restructure/)
> for the full design. This README covers the **.NET workspace only**.
> 运维 & 切流手册：[`docs/dual-stack-bff-runbook.md`](../docs/dual-stack-bff-runbook.md)；
> 架构图：[`docs/architecture-v2.md`](../docs/architecture-v2.md)。

## Projects

| Project | Layer | Purpose |
|---|---|---|
| `src/AgentBoard.Api` | API | WebAPI entry point + SignalR Hub (only project exposed externally) |
| `src/AgentBoard.Application` | Application | `IProvider` / `IService` / `IRepository<T>` / DTO / 业务编排（无 EF Core 引用） |
| `src/AgentBoard.Domain` | Domain | 纯 C# 领域模型（Entity / ValueObject / DomainEvent / Enum） |
| `src/AgentBoard.Infrastructure` | Infrastructure | EF Core `AppDbContext` + 3 SaveChanges Interceptors + `Repository<T>` 实现 |
| `../nodes/AgentBoard.Node` | Worker | 独立部署的 .NET Proposal Worker（不属于当前 BFF solution） |
| `tests/AgentBoard.Api.Tests` | Tests | xUnit 单元 / 集成 / 契约测试 |
| `tests/AgentBoard.Infrastructure.Tests` | Tests | 仓储 CRUD + 拦截器 + NetArchTest 架构护栏 |

## Layered architecture

```
Controller (HTTP route + DTO mapping, Api project)
    ↓
BaseController<TProvider> (exception mapping + UserContext, Api project)
    ↓
Provider (cross-Service orchestration, transactions, cache strategy, Application project)
    ↓
Service (operations on a single Domain aggregate, Application project)
    ↓
IRepository<T> / IDbContext (Application.Abstractions)
    ↓
Repository<T> (Infrastructure project, EF Core 实现)
    ↓
Domain (Entity / ValueObject / DomainEvent, Domain project)
```

**严格约束**（NetArchTest 守护，写在 `tests/AgentBoard.Infrastructure.Tests/Architecture/LayeredArchitectureTests.cs`）：

- `Api` 不能直接引用 `IDbContext`（必须经过 `IRepository<T>` 或 `IDbContext.CanConnectAsync` 通过 `Provider → Service`）
- `Api` 不能引用 `EntityFrameworkCore` 命名空间
- `Application` 不能引用 `Microsoft.EntityFrameworkCore.*`
- `Domain` 不能引用任何外层命名空间
- `Infrastructure` 可以引用 `Application` + `Domain`，但不能引用 `Api`

## EF Core performance rules

1. **Never** use `Include` / `ThenInclude`. They cause Cartesian-product queries
   that degrade non-linearly as the graph grows.
2. Use `Select` projections to DTOs so the database only returns the columns
   the API needs.
3. Use explicit LINQ `Join` (or a hand-written SQL view) for related data.
4. Aggregations belong in `GroupBy(...).Select(g => new { Count = g.Count()... })`
   so we get a single round-trip instead of N+1.
5. All read queries use `AsNoTracking()` unless change tracking is required.
6. **Migration stages**: .NET reads use `AsNoTracking` and query-specific projections; selected project/task writes now have explicit transactions. FastAPI/Alembic remains the contract/schema source of truth until endpoint-level cutover is explicitly approved.
7. **Alembic 是 DB schema 真源**；`dotnet ef migrations add` 仅作本地影子比对，**不 apply 到生产**。

See [`openspec/changes/dual-stack-bff-restructure/code-structure.md`](../openspec/changes/dual-stack-bff-restructure/code-structure.md) §2.3.2 for an example.

## Naming conventions

| 类型 | 命名 | 位置 |
|---|---|---|
| Controller | `XxxController` | `Api/Features/<Xxx>/` |
| Provider | `XxxProvider : IxxxProvider` | `Application/<Xxx>/` |
| Service | `XxxService : IxxxService` | `Application/<Xxx>/` |
| Repository | `XxxRepository : Repository<X>, IxxxRepository` | `Infrastructure/Persistence/Repositories/` |
| Domain Entity | `Xxx : Entity` | `Domain/<Xxx>/` |
| Request DTO | `XxxRequest` | `Application/<Xxx>/Dtos/` |
| Response DTO | `XxxResponseDto` (snake_case via `[JsonPropertyName]`) | `Application/<Xxx>/Dtos/` |

JSON wire format = **snake_case** (FastAPI 兼容)，用 `[JsonPropertyName("snake_name")]` 标注。

## Observability

- **Serilog** — CLEF JSON console + rolling file (14d retention, `Logs/agentboard-dotnet-*.log`)。
  - Enricher: `Application`, `MachineName`, `FromLogContext` (request_id), `TraceContextEnricher` (trace_id/span_id)
  - `Testing` 环境跳过 file sink（host 关闭时不会卡住）
- **OpenTelemetry** — ASP.NET Core + HttpClient instrumentation + Console exporter
  - OTLP exporter 留口子（Stage 2 接 collector）
- **Middleware** — `RequestIdMiddleware` 注入 `X-Request-Id` + `LogContext.PushProperty("request_id", id)`；
  `TraceContextMiddleware` 处理 W3C `traceparent` 头

## Local development

```powershell
# 1. Build everything
cd src/backend-dotnet
dotnet build

# 2. Run the API (binds 0.0.0.0:18099)
$env:AGENTBOARD_DOTNET_PORT = "18099"     # launchSettings.json 也可改
$env:AGENTBOARD_ENV         = "development"
$env:AgentBoard__Database__ConnectionString = "Data Source=agentboard-dev.db"
dotnet run --project src/AgentBoard.Api

# 3. Hit the smoke endpoints
curl http://localhost:18099/api/health
# → 200 { "status": "ok", "database": "ok", "version": "0.1.0", "timestamp": "..." }
# Header: X-Request-Id, traceparent

curl http://localhost:18099/api/meta | python -m json.tool
# → 6 个 snake_case enum 列表

# 4. Run tests
dotnet test
# → 24/24 通过 (Api 6 + Infrastructure 18)
```

## Docker

```powershell
# Build (context = repo root)
docker build -f src/backend-dotnet/Dockerfile -t agentboard-api-dotnet .

# Run (本地连 sqlite shadow db)
docker run --rm -p 18099:8080 agentboard-api-dotnet
```

The image is a multi-stage build (sdk → aspnet) and runs as the
non-root `agentboard` user. `HEALTHCHECK` polls `/api/health` every 30s.

## Commit conventions

Conventional Commits 格式：`type(scope): subject`：

- `feat(api):` / `feat(domain):` / `feat(application):` / `feat(infrastructure):` — 新功能
- `fix(api):` — bug fix
- `refactor(architecture):` — 重构（不改行为）
- `test(api):` / `test(infrastructure):` — 测试相关
- `docs(dotnet):` — 文档
- `chore(ci):` — CI / Docker / 脚本
- `feat(observability):` — Serilog / OTel 相关

**每次 commit 后必须 `git push origin main`**（CI 自动部署）。

## Goal proposal realtime flow

The BFF hosts the authenticated SignalR hub at `/hubs/proposals`. FastAPI
posts the identifier-only `goal` notification to
`/api/internal/realtime/proposals/questions` with the
`X-AgentBoard-Realtime-Key` header; the shared value is configured through
`AGENTBOARD_REALTIME_INTERNAL_KEY`. Connected Angular clients receive
`ProposalQuestionRaised`, then reload the proposal through the normal REST
API so question content remains behind the existing authorization boundary.

## Roadmap (Stage 0+)

| Story | Title | Status | Commit |
|---|---|---|---|
| S0-1 | Solution scaffold (slnx + Api + Tests) | ✅ done | `ac6f623` |
| S0-2 | Repository Pattern (EF Core, no Include) | ✅ done | `f38ca01` |
| S0-3 | Layered skeleton + Auth sample + NetArchTest | ✅ done | `ea282e7` |
| S0-4 | OpenAPI contract freeze (sync + drift + NSwag) | ✅ done | `34de639` |
| S0-5 | `/api/health` + `/api/meta` 1:1 with FastAPI | ✅ done | `0aaeee6` |
| S0-6 | docker-compose `api-dotnet` service | ✅ done | `b6deadd` |
| S0-7 | Serilog + OpenTelemetry + request middleware | ✅ done | `6de19b4` |
| S0-8 | Runbook + architecture-v2 + dotnet/README | ✅ done | (this commit) |
| S1-1 | Project/Epic/Story GET endpoints (read/query layer) | implemented | — |
| S1-2 | Task/Bug GET endpoints (read/query layer) | implemented | — |
| S1-3 | Contract and behavior tests for migrated endpoints | in progress | — |
| S2-1 | nginx cutover script (10% → 100%) | backlog | — |
| S2-2 | SignalR /hubs/agents | backlog | — |
| S2-3 | Write path migration to .NET | backlog | — |

Full breakdown: [`openspec/changes/dual-stack-bff-restructure/tasks.md`](../openspec/changes/dual-stack-bff-restructure/tasks.md).
