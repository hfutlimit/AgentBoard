# Repository Structure Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move production applications under `src/`, isolate application-owned configuration, and update build, test, deployment, and documentation references without changing runtime behavior.

**Architecture:** Use application-first boundaries: `src/backend-fastapi`, `src/backend-dotnet`, `src/frontend`, and `src/workers`. Keep repository tests in their existing `tests/` layout, with .NET tests beside the .NET solution. Put cross-application deployment files under `config/` and keep runtime data outside source.

**Tech Stack:** Python/FastAPI/Alembic/pytest, Angular/npm, .NET 10/xUnit, Docker Compose, PowerShell, Bash, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-24-repository-structure-design.md`

## Global Constraints

- Preserve the Python package import name `agentboard`.
- Preserve API routes, DTOs, environment variables, database schema, and .NET project dependency direction.
- Use `git mv` for relocations; do not keep duplicate old source trees.
- Do not move or overwrite runtime databases, logs, virtual environments, `bin/`, `obj/`, `node_modules/`, or generated frontend `dist/` output.
- Use tabs for indentation in all changed C# files.
- Run `git diff --check` and a changed-C# indentation scan before completion.

---

### Task 1: Create destination roots

**Files:** Create `src/backend-fastapi/`, `src/backend-dotnet/`, `src/frontend/`, `src/workers/`, `config/docker/`, and `config/deployment/`.

- [x] Verify the worktree is clean with `git status --short`.
- [x] Create roots with `New-Item -ItemType Directory -Force src/backend-fastapi,src/backend-dotnet,src/frontend,src/workers,config/docker,config/deployment`.
- [x] Verify all roots exist before moving files.

### Task 2: Move FastAPI and Python-owned configuration

**Files:**
- Move `agentboard/` to `src/backend-fastapi/agentboard/`.
- Move `migrations/` to `src/backend-fastapi/migrations/`.
- Move `requirements.txt`, `pytest.ini`, and `alembic.ini` to `src/backend-fastapi/`.

**Interfaces:** Preserve `import agentboard`, `agentboard.api:app`, `agentboard.web_app:app`, and `agentboard.mcp_server`.

- [x] Execute:

```powershell
git mv agentboard src/backend-fastapi/agentboard
git mv migrations src/backend-fastapi/migrations
git mv requirements.txt src/backend-fastapi/requirements.txt
git mv pytest.ini src/backend-fastapi/pytest.ini
git mv alembic.ini src/backend-fastapi/alembic.ini
```

- [x] Run with `PYTHONPATH=src/backend-fastapi`: `python -c "import agentboard; import agentboard.api; import agentboard.mcp_server"`.
- [x] Keep Alembic `script_location = migrations` relative to the FastAPI application root and update all callers to run from `src/backend-fastapi` or pass the explicit config path.

### Task 3: Move the Angular project

**Files:** Move the complete `frontend/` directory to `src/frontend/`.

- [x] Execute `git mv frontend src/frontend`.
- [x] Verify `src/frontend/package.json`, `src/frontend/angular.json`, and `src/frontend/src/main.ts` exist.
- [x] Run `npm --prefix src/frontend run build` and record any pre-existing frontend failure before continuing.

### Task 4: Move the .NET solution and projects

**Files:**
- Move `dotnet/src/` to `src/backend-dotnet/src/`.
- Move `dotnet/tests/` to `src/backend-dotnet/tests/`.
- Move `dotnet/contracts/` to `src/backend-dotnet/contracts/`.
- Move `dotnet/AgentBoard.slnx`, `Directory.Build.props`, `global.json`, `security-allowlist.json`, `SECURITY-AUDIT.md`, `README.md`, `.editorconfig`, and `.gitignore` into `src/backend-dotnet/`.
- Move `dotnet/Dockerfile.dotnet` to `src/backend-dotnet/Dockerfile`.
- Keep `dotnet/migrations/README.md` as .NET migration documentation under `src/backend-dotnet/`.

**Interfaces:** The canonical build command becomes `dotnet build src/backend-dotnet/AgentBoard.slnx`; project names and dependency direction remain unchanged.

- [x] Execute the moves with `git mv`.
- [x] Update `src/backend-dotnet/AgentBoard.slnx` project paths to `src/AgentBoard...` and `tests/AgentBoard...` relative to the moved solution.
- [x] Update the .NET Dockerfile `COPY` and publish paths to `src/backend-dotnet/...` while retaining repository-root build context.
- [x] Run `dotnet build src/backend-dotnet/AgentBoard.slnx --no-restore`.

### Task 5: Move Worker projects

**Files:**
- Move `workers/AgentBoard.ProposalProcessor/` to `src/workers/AgentBoard.ProposalProcessor/`.
- Move `workers/AgentBoard.ProposalProcessor.Tests/` to `src/workers/AgentBoard.ProposalProcessor.Tests/`.

- [x] Execute both `git mv` operations.
- [x] Search `src/workers` and `src/backend-dotnet` for stale worker paths and update project references, scripts, and README commands.
- [x] Run `dotnet test src/workers/AgentBoard.ProposalProcessor.Tests/AgentBoard.ProposalProcessor.Tests.csproj --no-restore`.

### Task 6: Move cross-application Docker and deployment configuration

**Files:**
- Move `docker-compose.yml` and `docker-compose.dev.yml` to `config/docker/`.
- Move the Python `Dockerfile` to `src/backend-fastapi/Dockerfile`.
- Keep `.dockerignore` and `.dockerignore.dotnet` at the repository root;
  Docker resolves ignore files from the build context root.
- Move deployment-only files from `scripts/deploy/` to `config/deployment/`; leave verification, migration, packaging, and development scripts under `scripts/`.

- [x] Execute the moves with `git mv`.
- [x] Update Compose `context`, `dockerfile`, volume, and command paths. Because Compose paths resolve relative to `config/docker/`, repository-root paths use `../..` (for example `context: ../..` and `../../src/backend-fastapi`). Preserve container paths.
- [x] Update Docker build contexts and ignore-file usage so secrets and generated artifacts remain excluded.
- [ ] Validate with `docker compose -f config/docker/docker-compose.yml -f config/docker/docker-compose.dev.yml config` (blocked: Docker is not installed on this machine; YAML parsed statically).

### Task 7: Update all references

**Files:** Modify `README.md`, `.github/workflows/dotnet-contract-check.yml`, all affected scripts, moved application docs, and tests that assert source paths.

- [x] Find stale references with `rg` while excluding `bin`, `obj`, `node_modules`, and `.venv`.
- [x] Update Python commands to use `PYTHONPATH=src/backend-fastapi` while keeping imports as `agentboard.*`.
- [x] Update `scripts/package_windows.py`, `scripts/sync-openapi.ps1`, `scripts/schema-drift-check.py`, `scripts/generate-fastapi-client.ps1`, deployment scripts, and static synchronization paths.
- [x] Update CI path filters, .NET commands, contract paths, Docker commands, and all copy-paste documentation.
- [x] Update tests only when they assert a moved filesystem path; do not weaken their assertions.

### Task 8: Verify and commit the structural migration

- [x] Run Python import and focused/unit tests with `PYTHONPATH=src/backend-fastapi` and `python -m pytest -c src/backend-fastapi/pytest.ini tests/unit -q`.
- [x] Run `npm --prefix src/frontend run build` and frontend tests.
- [x] Run `dotnet build src/backend-dotnet/AgentBoard.slnx --no-restore`, the moved .NET test suite, and the moved Worker test suite.
- [ ] Validate Compose configuration and, if available, a FastAPI startup smoke test (Docker unavailable; static YAML validation passed).
- [x] Confirm old production roots are absent: `agentboard`, `frontend`, `dotnet`, and `workers`.
- [x] Confirm no runtime artifact was added with `git status --short` and `git ls-files`.
- [x] Run `git diff --check`; scan changed C# source content (no C# source files were edited; only relocations occurred).
- [ ] Stage and commit with `git add -A; git diff --cached --check; git commit -m "refactor: reorganize repository under src"` (pending explicit commit request).
