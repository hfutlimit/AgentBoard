# AgentBoard Boundary Hardening Design

**Date:** 2026-08-24  
**Status:** Approved for implementation

## Goal

Complete the second-stage boundary hardening identified in the repository
review without changing public API routes, persisted data, message contracts,
or existing Python import compatibility.

## Current verified facts

- The production source roots are `src/backend-fastapi`,
  `src/backend-dotnet`, `src/frontend`, and `src/workers`.
- The Python worker implementation is still physically under
  `agentboard/features/workers/`, while the independently deployed proposal
  worker is under `src/workers/`.
- `agentboard/schemas.py` is still a shared 541-line import target for several
  feature routers.
- `agentboard/mq.py` is a 1,409-line messaging implementation.
- `agentboard/service.py` and `agentboard/mcp_server.py` are compatibility
  facades, but their size and compatibility exports need explicit boundary
  tests.
- The review's `AgentBoard.WorkerService` source path is stale; only its
  documentation/design references remain.

## Architecture decisions

### Worker ownership

All independently deployed worker processes remain under `src/workers/`.
The Python proposal/agent execution implementation moves to
`src/backend-fastapi/agentboard/agent_runtime/`. The old
`agentboard.features.workers` and `agentboard.worker` import paths remain
compatibility shims only and must not contain new implementation logic.

### Schemas

Feature-owned request/command models move into feature-local `schemas.py`
modules. Shared transport models move into
`agentboard/core/api/schemas.py`. The old `agentboard.schemas` module remains
a re-export facade during migration.

### Facades

`service.py`, `api.py`, `mcp_server.py`, `schemas.py`, and `mq.py` are legacy
compatibility entry points. New production modules must import their feature,
core, or infrastructure implementation directly. Architecture tests enforce
that `features/*` and `domains/*` do not import the service facade.

### Messaging

The RabbitMQ implementation moves to
`agentboard/core/infrastructure/messaging/`. The old `agentboard.mq` module
re-exports the public messaging API so external callers and legacy tests keep
working.

### Identity and documentation

`features/identity` owns identity use cases and services; `features/auth`
owns HTTP authentication routes and adapters. This ownership is documented
and tested without a risky package merge. Stale WorkerService and old-root
paths are corrected in active documentation.

## Compatibility and safety constraints

- Preserve `from agentboard.worker import ...`,
  `from agentboard.features.workers...`, `from agentboard import mq`, and
  `from agentboard import schemas` during the migration.
- Preserve all existing environment variable names, API routes, Pydantic
  validation behavior, RabbitMQ payloads, and database schema.
- Do not add a new runtime dependency.
- Use tabs in changed C# files; no C# source behavior is intentionally changed.
- Every moved implementation receives import and focused test coverage before
  the compatibility shim is considered complete.
