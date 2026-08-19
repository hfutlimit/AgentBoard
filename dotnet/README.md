# AgentBoard .NET BFF

> Dual-stack BFF — see `openspec/changes/dual-stack-bff-restructure/` for the
> full design. This README covers the **.NET workspace only**.

## Projects

| Project | Layer | Purpose |
|---|---|---|
| `src/AgentBoard.Api` | API | WebAPI entry point + SignalR Hub (only project exposed externally) |
| `src/AgentBoard.WorkerService` | API | Windows Service / Linux daemon (placeholder; lands after Stage 2) |
| `tests/AgentBoard.Api.Tests` | Tests | xUnit unit / integration / contract tests |

The remaining projects (`AgentBoard.Domain`, `AgentBoard.Application`,
`AgentBoard.Infrastructure`) are added in **S0-2 / S0-3** with the
Repository Pattern foundation and the layered skeleton.

## Layered architecture

```
Controller (HTTP route + DTO mapping)
    ↓
BaseController<TProvider> (exception mapping + UserContext)
    ↓
Provider (cross-Service orchestration, transactions, cache strategy)
    ↓
Service (operations on a single Domain aggregate)
    ↓
IRepository<T> / IDbContext
    ↓
Domain (Entity / ValueObject / DomainEvent)
```

Strictly enforced by NetArchTest in `AgentBoard.Api.Tests/Architecture/`.

## EF Core performance rules

1. **Never** use `Include` / `ThenInclude`. They cause Cartesian-product queries
   that degrade non-linearly as the graph grows.
2. Use `Select` projections to DTOs so the database only returns the columns
   the API needs.
3. Use explicit LINQ `Join` (or a hand-written SQL view) for related data.
4. Aggregations belong in `GroupBy(...).Select(g => new { Count = g.Count()... })`
   so we get a single round-trip instead of N+1.
5. All read queries use `AsNoTracking()` unless change tracking is required.

See `code-structure.md` §2.3.2 for an example.

## Local development

```powershell
# 1. Build everything
cd dotnet
dotnet build

# 2. Run the API (binds 0.0.0.0:18000)
dotnet run --project src/AgentBoard.Api

# 3. Hit the smoke endpoint
curl http://localhost:18000/
# → 200 { "service": "AgentBoard.Api", "stage": "S0-1", ... }

# 4. Run tests
dotnet test
```

## Docker

```powershell
# Build (context = repo root)
docker build -f Dockerfile.dotnet -t agentboard-api-dotnet .

# Run
docker run --rm -p 18000:8080 agentboard-api-dotnet
```

The image is a multi-stage build (sdk → aspnet) and runs as the
non-root `agentboard` user. `HEALTHCHECK` polls `/` every 30s.

## Roadmap (Stage 0)

| Story | Title | Status |
|---|---|---|
| S0-1 | Solution scaffold | ✅ this commit |
| S0-2 | Repository Pattern foundation (EF Core, no `Include`) | next |
| S0-3 | Layered skeleton + Auth feature sample | pending |
| S0-4 | OpenAPI contract freeze (sync + drift check + NSwag) | pending |
| S0-5 | `/api/health` and `/api/meta` 1:1 with FastAPI | pending |
| S0-6 | docker-compose `api-dotnet` service | pending |
| S0-7 | Serilog + OpenTelemetry | pending |
| S0-8 | Runbook + architecture doc | pending |

Full breakdown: `openspec/changes/dual-stack-bff-restructure/tasks.md`.
