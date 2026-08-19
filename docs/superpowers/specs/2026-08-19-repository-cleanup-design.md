# AgentBoard Repository Cleanup Design

## Goal

Reduce repository noise and local disk usage without deleting credentials, working databases, uploaded data, Agent memory, active source code, or reusable design documentation.

## Safety boundary

The cleanup must preserve these local-only paths:

- `.env`
- `agentboard.db`
- `data/`
- `agentboard_data/`
- `.workbuddy/`

Tracked deletions remain recoverable from Git history. Recursive local deletion is limited to explicit generated or scratch paths inside the repository. The cleanup must not use a repository-wide `git clean -X` because that would also remove the preserved local state above.

## Local generated files to remove

- Frontend dependencies and outputs: `frontend/node_modules/`, `frontend/.angular/`, `frontend/dist/`.
- .NET outputs: all `bin/` and `obj/` directories under `dotnet/` and `workers/`.
- Python caches: `__pycache__/`, `.pytest_cache/`, `pycache_check/`.
- Scratch and evidence artifacts: `tmp/`, `screenshots/`, ignored root `overview-*.md`, debug logs, debug screenshots, and disposable test databases.
- Empty `.worktrees/` left by the abandoned worktree attempt.

These paths are reproducible and accounted for approximately 804 MiB during the 2026-08-19 inventory.

## Tracked files to remove

- Historical database dumps:
  - `backups/agentboard-20260714-2148.sql`
  - `db-backups/agentboard-20260719-1840.sql`
- Obsolete root artifacts:
  - `debug_error.png`
  - `project_detail.png`
  - `overview.md`
  - `web_app_new.py`
  - `migrate_to_mariadb.py`, superseded by the maintained migration tooling under `scripts/`
  - `tests/debug_spa.py`
- Duplicate prototypes:
  - root `agentboard-task-view-prototype.html` (keep `deliverables/agentboard-story-view.html`)
  - `deliverables/proposal-qa-workbench-prototype.html` (keep `deliverables/proposal-qa-workbench.html`)
- One-off E2E scripts:
  - `deliverables/e2e_s3_health_check.py`
  - `deliverables/e2e_settings_leftmenu.py`
  - `deliverables/e2e_tabs_redesign.py`
  - `deliverables/e2e_tickets_view.py`
- Runtime evidence screenshots:
  - `deliverables/e2e_overview_tab.png`
  - `deliverables/e2e_s3_login.png`
  - `deliverables/e2e_s3_project.png`
  - `deliverables/e2e_settings_dropdown.png`
  - `deliverables/e2e_settings_leftmenu.png`
  - `deliverables/e2e_settings_subtabs.png`
  - `deliverables/e2e_tickets_all.png`
  - `deliverables/e2e_tickets_done.png`
  - `deliverables/e2e_tickets_incomplete.png`
  - `deliverables/quick_view_drawer.png`

Formal Markdown reports, product strategy documents, architecture reviews, and named HTML prototypes remain tracked.

## Manual verification scripts

The three root document/Markdown verification scripts are historical manual checks rather than pytest tests. Move and rename them under `scripts/manual/` so pytest does not collect them from the repository root:

- `test_doc_api.py` → `scripts/manual/verify_documents_api.py`
- `test_doc_frontend.py` → `scripts/manual/verify_documents_frontend.py`
- `test_mermaid_e2e.py` → `scripts/manual/verify_mermaid_rendering.py`

Update maintained references to the new paths and add a short `scripts/manual/README.md` stating that the scripts require explicitly started local services and are not part of CI.

## Intentional duplicates retained

- Mermaid runtime assets under `agentboard/web/static/` and `frontend/public/static/` serve different packaging roots.
- Favicon copies under backend/frontend or separate Angular portal projects serve independent build outputs.

## Ignore rules

Add narrow patterns for local database backups and one-off evidence artifacts. Do not ignore maintained documentation or design prototypes broadly.

## Verification

After cleanup:

1. Confirm preserved local-state paths still exist.
2. Confirm `git status` contains only the approved tracked cleanup.
3. Run syntax/collection checks that do not require external services.
4. Run the maintained Python, frontend, and .NET test/build commands available in the workspace.
5. Report any pre-existing failures separately; do not hide them by deleting tests.
