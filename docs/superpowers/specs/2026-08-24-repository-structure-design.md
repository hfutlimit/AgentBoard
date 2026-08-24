# AgentBoard Repository Structure Design

**Date:** 2026-08-24
**Status:** Approved direction; implementation completed pending commit

## Goal

Reorganize the repository so production code lives under `src/`, each runnable
application owns its local build/runtime configuration, cross-application
deployment configuration is isolated under `config/`, and operational data is
kept outside source directories.

The migration is structural only. It must not change business behavior,
public API contracts, Python package import names, database contents, or the
existing .NET project-layer boundaries.

## Target structure

```text
/
├─ src/
│  ├─ backend-fastapi/
│  │  ├─ agentboard/
│  │  ├─ migrations/
│  │  ├─ requirements.txt
│  │  ├─ pytest.ini
│  │  ├─ alembic.ini
│  │  └─ Dockerfile
│  ├─ backend-dotnet/
│  │  ├─ src/
│  │  │  ├─ AgentBoard.Api/
│  │  │  ├─ AgentBoard.Application/
│  │  │  ├─ AgentBoard.Domain/
│  │  │  └─ AgentBoard.Infrastructure/
│  │  ├─ tests/
│  │  │  ├─ AgentBoard.Api.Tests/
│  │  │  └─ AgentBoard.Infrastructure.Tests/
│  │  ├─ contracts/
│  │  ├─ AgentBoard.slnx
│  │  ├─ Directory.Build.props
│  │  ├─ global.json
│  │  ├─ security-allowlist.json
│  │  ├─ SECURITY-AUDIT.md
│  │  └─ Dockerfile
│  ├─ frontend/
│  │  ├─ src/
│  │  ├─ public/
│  │  ├─ package.json
│  │  ├─ package-lock.json
│  │  ├─ angular.json
│  │  ├─ tsconfig*.json
│  │  └─ vitest.config.ts
│  └─ workers/
│     └─ AgentBoard.ProposalWorker/
│        ├─ src/
│        ├─ appsettings.json
│        ├─ AgentBoard.ProposalWorker.csproj
│        └─ README.md
├─ tests/
│  ├─ conftest.py
│  ├─ unit/
│  ├─ e2e*/
│  └─ test_*.py
├─ config/
│  ├─ docker/
│  │  ├─ docker-compose.yml
│  │  └─ docker-compose.dev.yml
│  └─ deployment/
│     ├─ web.config
│     ├─ env.webapi.example
│     ├─ env.mcp.example
│     └─ service scripts
├─ scripts/
├─ docs/
├─ data/
├─ logs/
└─ tmp/
```

The root keeps only repository-level metadata and entry-point documentation:
`.gitignore`, `.dockerignore`, `.dockerignore.dotnet`, `README.md`, `.github/`,
`docs/`, `scripts/`, `src/`, `tests/`, `config/`, and operational directories.
Root-level
dependency/config files that belong to one application are removed after
their references are updated.

## Migration rules

### FastAPI application

- Move the current `agentboard/` package to `src/backend-fastapi/agentboard/`.
- Move `migrations/`, `requirements.txt`, `pytest.ini`, and `alembic.ini` next
  to that application.
- Preserve the import name `agentboard`; launch commands use
  `PYTHONPATH=src/backend-fastapi` or an equivalent configured working path.
- Keep packaged web assets under the FastAPI package so the existing static
  serving and release-artifact checks continue to work.

### .NET backend

- Move the existing `dotnet/src/`, `dotnet/tests/`, `dotnet/contracts/`, and
  .NET solution/configuration files under `src/backend-dotnet/`.
- Update all project references, solution paths, Docker copies, CI workflow
  paths, contract-generation scripts, and documentation.
- Preserve the four project names and their dependency direction.

### Frontend

- Move the current `frontend/` project to `src/frontend/` without changing
  Angular source imports or npm scripts.
- Update Docker build context, static-asset synchronization, frontend tests,
  and documentation to use the new location.

### Worker

- Move the C# proposal worker project and its tests into the worker area under
  `src/workers/`.
- Keep worker runtime configuration beside the worker project.
- Update worker README, scripts, solution/project references, and packaging
  paths.

### Tests and tools

- Keep repository-level tests under `tests/` with their existing `unit/`,
  `e2e*`, fixture, and root test-file layout. This preserves the shared
  `tests/conftest.py` and the existing repository-root path assumptions.
- Keep application-owned .NET tests under `src/backend-dotnet/tests/` so they
  remain next to the solution they test.
- Keep reusable operational scripts under `scripts/`; update them rather than
  creating compatibility copies in old locations.

### Cross-application configuration

- Move Compose files and deployment-only configuration into `config/`.
- Keep `.dockerignore` and `.dockerignore.dotnet` at the repository root;
  Docker resolves ignore files from the build context root, so relocating them
  would weaken the secret and generated-artifact boundary.
- Keep secrets out of Git; retain only examples/templates.
- Do not move runtime databases, logs, temporary files, or local virtual
  environments into `src/`.

## Compatibility and risk controls

- This migration must use `git mv` semantics so history remains trackable.
- No business logic refactoring is included.
- No public API route, DTO, environment variable, database schema, or package
  import name is intentionally changed.
- If a historical test hard-codes a source path, update that test to the new
  canonical path rather than retaining a duplicate source tree.
- Generated build output (`bin/`, `obj/`, `node_modules/`, frontend `dist/`)
  remains ignored and is not moved as source.
- The existing root database and other local runtime artifacts are not
  committed or overwritten during migration.

## Required reference updates

The implementation must search and update, at minimum:

- Dockerfiles and Compose volume/build paths.
- GitHub Actions workflow path filters and commands.
- Python launch commands, `PYTHONPATH`, Alembic script location, and pytest
  test discovery.
- Angular build and static synchronization scripts.
- .NET solution, project, contract, and worker commands.
- Deployment scripts and packaging scripts.
- README, architecture docs, release notes, and test fixtures that assert
  source locations.

## Acceptance criteria

1. No production application source remains in the old root-level
   `agentboard/`, `frontend/`, `dotnet/`, or `workers/` locations.
2. All production source is under `src/`; repository tests remain isolated
   under `tests/`, and .NET application tests live beside their solution under
   `src/backend-dotnet/tests/`.
3. Each runnable application has its own dependency/build/runtime files.
4. `dotnet build src/backend-dotnet/AgentBoard.slnx` succeeds.
5. The focused .NET tests and worker tests pass from their new paths.
6. Python import, FastAPI startup, migration configuration, and focused tests
   pass with the new `PYTHONPATH`.
7. Frontend production build and tests pass from `src/frontend/`.
8. Docker Compose configuration validates and its build contexts point only to
   the new paths.
9. No tracked database, log, build, or temporary artifact is accidentally
   introduced by the move.
10. `git diff --check` is clean and the final worktree contains only the
    intended structural changes.
