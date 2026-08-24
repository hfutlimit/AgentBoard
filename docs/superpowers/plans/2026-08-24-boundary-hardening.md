# AgentBoard Boundary Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden Worker, schema, facade, messaging, identity, and documentation boundaries while preserving public compatibility.

**Architecture:** Move implementation behind stable compatibility facades. Independent executables stay under `src/workers`; Python agent execution moves to `agentboard/agent_runtime`; feature contracts become local; RabbitMQ becomes core infrastructure.

**Tech Stack:** Python 3.14/FastAPI/Pydantic/pytest, SQLAlchemy, RabbitMQ/pika, .NET 10, PowerShell, Git.

**Spec:** `docs/superpowers/specs/2026-08-24-boundary-hardening-design.md`

## Global Constraints

- Preserve public API routes, environment variables, database schema, RabbitMQ payloads, and legacy Python import paths.
- Do not add runtime dependencies.
- Keep independently deployed executables under `src/workers/`.
- Keep compatibility modules small and re-export-only where possible.
- Use tabs in changed C# files.
- Run focused tests after each boundary move and run `git diff --check` before completion.

---

### Task 1: Add architecture boundary tests

**Files:**
- Create: `tests/unit/test_architecture_boundaries.py`
- Test: existing Python unit test command

- [x] Add tests that resolve the production package root and assert the Python runtime package, messaging package, feature schema modules, and worker compatibility paths exist.
- [x] Add an AST/source scan that fails when `agentboard/features/**` or `agentboard/domains/**` imports `agentboard.service`.
- [x] Add an AST/source scan that fails when implementation modules import the legacy top-level `agentboard.mq` or `agentboard.schemas`; compatibility facades may import implementation modules, not the reverse.
- [x] Run the new test file and record the expected failures before moving code.

### Task 2: Move Python worker implementation to agent_runtime

**Files:**
- Create/move: `src/backend-fastapi/agentboard/agent_runtime/`
- Modify: `src/backend-fastapi/agentboard/worker/__init__.py`
- Modify/create: `src/backend-fastapi/agentboard/features/workers/` compatibility shims
- Modify: direct worker imports and tests

- [x] Move the implementation modules and `handlers/` from `features/workers` to `agent_runtime` using rename semantics.
- [x] Update relative imports and internal documentation to use `agent_runtime`.
- [x] Replace `features/workers` implementation files with small compatibility re-exports for direct legacy imports.
- [x] Point `agentboard.worker` at `agent_runtime` and preserve all existing exported names and CLI entry points.
- [x] Add a test that imports the modern package and both legacy paths and asserts they expose the same `ProposalWorker` class.
- [x] Run worker-focused Python tests and the architecture boundary tests.

### Task 3: Split feature schemas and retain the facade

**Files:**
- Create: `src/backend-fastapi/agentboard/core/api/schemas.py`
- Create: feature-local `schemas.py` files for projects, work items, auth, scheduling, documents, proposals, and admin/notifications as needed
- Modify: `src/backend-fastapi/agentboard/schemas.py`
- Modify: feature routers and tests

- [x] Classify every existing Pydantic model by the router/use case that consumes it.
- [x] Move shared transport models to `core/api/schemas.py` and feature-owned models to their feature package.
- [x] Rewrite `agentboard.schemas` as explicit re-exports with no model definitions.
- [x] Replace wildcard router imports with feature-local or core API imports.
- [x] Add tests that validate representative models through both the modern feature path and the legacy facade path.
- [x] Run all Python unit tests covering auth, projects, work items, documents, proposals, scheduling, and API boundaries.

### Task 4: Move RabbitMQ implementation into messaging infrastructure

**Files:**
- Create/move: `src/backend-fastapi/agentboard/core/infrastructure/messaging/`
- Modify: `src/backend-fastapi/agentboard/mq.py`
- Modify: worker/runtime imports and messaging tests

- [x] Move the implementation while preserving public classes, constants, publisher factories, and payload serialization.
- [x] Add `messaging/__init__.py` with the supported public API.
- [x] Rewrite `agentboard.mq` as a compatibility re-export module.
- [x] Update runtime code to import `core.infrastructure.messaging` directly.
- [x] Run the MQ, workflow worker, proposal worker, and retry/reconnect tests.

### Task 5: Clarify identity/auth ownership and remove stale WorkerService references

**Files:**
- Modify: `src/backend-fastapi/agentboard/features/identity/README.md` or ownership documentation
- Modify: `src/backend-fastapi/agentboard/features/auth/README.md` or ownership documentation
- Modify: `src/backend-dotnet/README.md`
- Modify: active `openspec`/docs references to `AgentBoard.WorkerService` and old root paths
- Test: architecture/documentation path checks where present

- [x] Document identity ownership as users, credentials, API keys, and identity services.
- [x] Document auth ownership as HTTP login/registration/me/API-key route adapters.
- [x] Remove stale claims that a `src/backend-dotnet/src/AgentBoard.WorkerService` project exists; describe it as future design only where historical context is required.
- [x] Update active worker commands to `src/workers/AgentBoard.ProposalWorker`.
- [x] Run stale-path scans over active source, scripts, CI, and documentation.

### Task 6: Verify and hand off

- [x] Run `python -m pytest -c src/backend-fastapi/pytest.ini tests/unit -q` with the new `PYTHONPATH`.
- [x] Run the focused worker, MQ, architecture, and API boundary tests.
- [x] Run `dotnet build src/backend-dotnet/AgentBoard.slnx --no-restore` and Worker tests.
- [ ] Run frontend build if no source-level frontend changes are required, to confirm the repository remains healthy.
- [x] Run `git diff --check` and scan changed C# files for leading spaces.
- [ ] Report Docker runtime validation separately if Docker remains unavailable.
