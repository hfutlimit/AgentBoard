# .NET Backend Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the .NET BFF for safe migration and future SignalR without changing public route names.

**Architecture:** Keep FastAPI/Alembic as the migration source of truth. Add focused application interfaces for runtime security, database-side reads, EF transactions, and application events; keep SignalR/RabbitMQ out of business providers until those boundaries are stable.

**Tech Stack:** .NET 10, ASP.NET Core, EF Core, SQLite/InMemory test providers, xUnit, FluentAssertions.

**Spec:** `docs/superpowers/specs/2026-08-24-dotnet-backend-hardening-design.md`

## Global Constraints

- Preserve public API route names and snake_case JSON.
- Development/testing compatibility fallbacks are allowed; production must fail closed.
- Alembic remains the database schema owner.
- Use tabs for indentation in all changed C# files.
- Do not commit changes.

---

### Task 1: Runtime safety and AI proxy

**Files:**
- Create: `dotnet/src/AgentBoard.Api/Security/RuntimeSecurityConfiguration.cs`
- Create: `dotnet/src/AgentBoard.Api/Clients/FastApiTaskClient.cs`
- Modify: `dotnet/src/AgentBoard.Api/Program.cs`
- Modify: `dotnet/src/AgentBoard.Api/Features/Tasks/TasksController.cs`
- Test: `dotnet/tests/AgentBoard.Api.Tests/Security/RuntimeSecurityConfigurationTests.cs`
- Test: `dotnet/tests/AgentBoard.Api.Tests/Features/Tasks/TasksControllerTests.cs`

**Interfaces:** `RuntimeSecurityConfiguration.ResolveJwtSecret` and
`ResolveCorsOrigins`; `IFastApiTaskClient.ProxyGenerateSubtasksAsync` returns
status, content type, and body for transparent forwarding.

- [ ] Write failing tests for production secret rejection, wildcard CORS rejection, development fallback, and proxying a FastAPI 404/body.
- [ ] Run the focused API tests and confirm the new assertions fail for the current fallback/stub behavior.
- [ ] Implement the runtime resolver, explicit CORS policy, and typed FastAPI client.
- [ ] Replace the fake provider call with the proxy response and remove fake-row behavior.
- [ ] Run the focused API tests and confirm they pass.

### Task 2: Transactions and project deletion

**Files:**
- Modify: `dotnet/src/AgentBoard.Application/Abstractions/IUnitOfWork.cs`
- Modify: `dotnet/src/AgentBoard.Infrastructure/Persistence/AppDbContext.cs`
- Modify: `dotnet/src/AgentBoard.Application/Board/BoardProvider.cs`
- Modify: `dotnet/src/AgentBoard.Infrastructure/Persistence/Repositories/ReadOnlyRepositories.cs`
- Modify: `dotnet/src/AgentBoard.Application/Abstractions/IReadOnlyRepositories.cs`
- Test: `dotnet/tests/AgentBoard.Infrastructure.Tests/Persistence/ProjectDeletionTests.cs`
- Test: `dotnet/tests/AgentBoard.Api.Tests/Features/Projects/ProjectsControllerTests.cs`

- [ ] Write failing tests proving a project create failure rolls back the project and owner, and deletion removes every project-owned child family.
- [ ] Run the focused tests and confirm failure before implementation.
- [ ] Add `BeginTransactionAsync` to the unit-of-work abstraction and implement it with EF Core.
- [ ] Wrap project creation and deletion in the transaction scope.
- [ ] Extend deletion to documents, revisions, comments, folders, sprints, attachments, dependencies, histories, webhooks, notifications, and audit records using parent IDs where necessary.
- [ ] Run focused persistence/API tests and confirm pass.

### Task 3: Database-side read queries

**Files:**
- Modify: `dotnet/src/AgentBoard.Application/Abstractions/IReadOnlyRepositories.cs`
- Modify: `dotnet/src/AgentBoard.Infrastructure/Persistence/Repositories/ReadOnlyRepositories.cs`
- Modify: `dotnet/src/AgentBoard.Application/Board/BoardProvider.cs`
- Test: `dotnet/tests/AgentBoard.Infrastructure.Tests/Performance/ReadQueryTests.cs`

- [ ] Write failing tests for member pagination/user projection, notification filtering/count, and overview aggregation.
- [ ] Run them against the current implementation and capture the expected query-result failures or query-count evidence.
- [ ] Implement projection, ordering, pagination, and aggregate queries in `IProjectReadRepository`.
- [ ] Route `BoardProvider` hot reads through the new repository.
- [ ] Run focused read-query tests and verify results and generated SQL behavior.

### Task 4: Provider boundaries and application events

**Files:**
- Create: `dotnet/src/AgentBoard.Application/Events/IApplicationEventPublisher.cs`
- Create: `dotnet/src/AgentBoard.Application/Events/ApplicationEvents.cs`
- Create: `dotnet/src/AgentBoard.Infrastructure/Events/DomainEventApplicationPublisher.cs`
- Modify: `dotnet/src/AgentBoard.Application/Board/BoardProvider.cs`
- Modify: `dotnet/src/AgentBoard.Application/DependencyInjection.cs`
- Test: `dotnet/tests/AgentBoard.Infrastructure.Tests/Architecture/ApplicationEventTests.cs`

- [ ] Write failing tests that task/project writes publish application events without referencing Hub types.
- [ ] Run the tests and confirm the event seam is absent.
- [ ] Add the publisher and event records, then raise events after successful writes.
- [ ] Extract focused Project/Task read/write collaborators only where this reduces `BoardProvider` dependencies without changing controller contracts.
- [ ] Run architecture and event tests.

### Task 5: Documentation, formatting, and verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture-v2.md`
- Modify: `docs/project-context/refactor-progress.md`
- Modify: `dotnet/src/AgentBoard.Api/Program.cs`

- [ ] Update stage and cutover statements to distinguish implementation from traffic readiness.
- [ ] Run all focused tests, solution tests, and solution build.
- [ ] Scan changed C# files for leading-space indentation and correct any findings.
- [ ] Review `git diff`, test output, and remaining known gaps before reporting status.
