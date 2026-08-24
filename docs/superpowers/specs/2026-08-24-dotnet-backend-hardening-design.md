# .NET Backend Hardening Design

## Goal

Bring the current .NET BFF to a safe migration boundary before production
traffic or SignalR: reject unsafe production configuration, remove fake AI
success responses, make multi-aggregate writes atomic, move hot reads into
database-side queries, complete project deletion scope, and expose existing
application events without coupling business code to SignalR.

## Scope and non-goals

- Preserve existing public route names and snake_case JSON conventions.
- Keep FastAPI/Alembic as the compatibility and schema source during the
  migration.
- Do not introduce a second database migration system.
- Do not rewrite every provider or add a generic CQRS framework.
- Do not make SignalR a direct dependency of a Provider; the first event
  delivery path is an application event dispatcher, with an outbox seam for
  durable delivery.

## Design

### Runtime safety

`RuntimeSecurityConfiguration` resolves the JWT secret and CORS origins from
configuration. Development and Testing may use the local fallback. Any other
environment rejects missing, placeholder, or short secrets and rejects the
wildcard CORS origin. Development wildcard CORS uses `AllowAnyOrigin` without
credentials; explicit origins may use credentials.

### AI endpoint

`POST /api/tasks/{id}/generate-subtasks` becomes a transparent proxy to the
FastAPI endpoint. The .NET controller forwards the request and response body,
status, and content type, so the endpoint cannot claim success with locally
fabricated rows. Upstream failures are returned as a controlled 502 while a
FastAPI 404 remains a 404.

### Transactions

`IUnitOfWork` gains an async transaction scope backed by EF Core. Project
creation and owner membership insertion run inside one transaction. The same
seam is available to future audit/event writes.

### Query model

`IProjectReadRepository` exposes SQL-translated member and notification page
queries plus overview aggregation. The implementation projects directly to
DTOs, applies filtering/order/pagination/count in the database, and uses
`AsNoTracking`. Existing generic repositories remain for simple CRUD and are
not used for these hot paths.

### Delete scope

Project deletion loads and removes every .NET-owned project-scoped entity:
documents, revisions, document comments/folders, epics, stories, tasks,
comments, members, sprints, attachments, dependencies, histories, webhooks,
notifications and audit records where the schema exposes a project link.
Entities without a project link are removed through their owning parent IDs.
The operation runs in one transaction and has a regression test for every
owned child family.

### Application events

The existing domain-event dispatcher remains the application boundary. A
`TaskUpdatedEvent` and `ProjectDeletedEvent` are raised by write operations;
handlers can publish to an event sink. An `IApplicationEventPublisher` and an
outbox-ready `IOutboxStore` interface are added without adding RabbitMQ or
SignalR runtime behavior in this change.

### Documentation and cutover

README, architecture, and progress metadata state that .NET write routes are
implemented but not automatically traffic-ready. The root diagnostic stage is
updated to the current hardening milestone.

## Verification

- Unit tests cover runtime safety decisions and transaction API behavior.
- API tests cover proxy status forwarding and production startup rejection.
- Infrastructure tests cover SQL-side query results and project deletion.
- `dotnet test dotnet/AgentBoard.sln` and `dotnet build dotnet/AgentBoard.sln`
  must pass.
- Changed C# files must use tabs for indentation.
