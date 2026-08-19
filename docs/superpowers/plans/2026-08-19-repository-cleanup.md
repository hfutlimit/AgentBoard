# AgentBoard Repository Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove reproducible and obsolete repository artifacts while preserving local credentials, business data, Agent memory, maintained source code, reports, and prototypes.

**Architecture:** Perform cleanup in three isolated layers: explicit ignored local artifacts, explicit tracked deletions, and manual-script organization. Verify preserved state before and after every destructive layer, then run repository builds/tests and review the complete Git diff.

**Tech Stack:** Git, PowerShell, Python/pytest, Angular/npm, .NET 10/xUnit

**Spec:** `docs/superpowers/specs/2026-08-19-repository-cleanup-design.md`

## Global Constraints

- Work directly on the current `main` branch as explicitly requested by the user.
- Never delete `.env`, `agentboard.db`, `data/`, `agentboard_data/`, or `.workbuddy/`.
- Do not use repository-wide `git clean -X`; every destructive command must name its target paths.
- Do not alter maintained application behavior as part of file cleanup.
- Preserve formal Markdown reports, architecture reviews, and named HTML prototypes.

---

### Task 1: Remove explicit local generated artifacts

**Files:**
- Delete ignored build outputs under `frontend/`, `dotnet/`, and `workers/`.
- Delete ignored scratch files under `tmp/`, `screenshots/`, root debug outputs, caches, and disposable test databases.
- Preserve the five local-state paths in Global Constraints.

- [ ] Record existence and sizes of the five preserved paths.
- [ ] Dry-run each explicit `git clean -ndX -- <paths>` command.
- [ ] Execute the corresponding `git clean -fdX -- <paths>` commands.
- [ ] Re-check that all five preserved paths still exist.

### Task 2: Remove obsolete tracked files and add narrow ignore rules

**Files:**
- Modify: `.gitignore`
- Delete: exact tracked paths listed in the cleanup design under “Tracked files to remove”.

- [ ] Add ignore rules for `backups/*.sql`, `db-backups/*.sql`, and one-off `deliverables/e2e_*` evidence.
- [ ] Delete only the exact tracked backup, duplicate, debug, obsolete, and evidence paths from the design.
- [ ] Confirm retained deliverable reports and prototypes remain present.
- [ ] Run `git diff --check` and inspect `git status --short`.

### Task 3: Organize manual verification scripts

**Files:**
- Create: `scripts/manual/README.md`
- Move: `test_doc_api.py` to `scripts/manual/verify_documents_api.py`
- Move: `test_doc_frontend.py` to `scripts/manual/verify_documents_frontend.py`
- Move: `test_mermaid_e2e.py` to `scripts/manual/verify_mermaid_rendering.py`
- Modify: maintained references under `docs/` and `openspec/`.

- [ ] Move and rename the three scripts without rewriting their historical behavior.
- [ ] Add execution prerequisites and non-CI status to `scripts/manual/README.md`.
- [ ] Update all maintained path references.
- [ ] Verify no root `test_*.py` manual script remains.

### Task 4: Verify, review, commit, and push

**Files:**
- Review all changed paths from Tasks 1–3.

- [ ] Confirm the preserved local-state paths still exist.
- [ ] Run Python collection/syntax checks and the maintained Python test suite feasible in this environment.
- [ ] Run the frontend production build.
- [ ] Run the .NET solution tests.
- [ ] Record pre-existing failures separately and ensure cleanup introduced no missing-file references.
- [ ] Run `git diff --check`, inspect the complete diff, and confirm no secrets or local-state files are staged.
- [ ] Commit the approved cleanup on `main` and push `origin/main` without force.

