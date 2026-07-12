# Automation Memory: AgentBoard-Code review

## 2026-07-12 13:02 Execution

**Status**: Completed successfully

**Findings**:
- Found 2 tasks in `in_review` status: Task #33 (FastAPI 暴露核心 CRUD) and Task #34 (与 MCP 共用同一 service 层)
- Both belong to AgentBoard project (id=3), Story 10 (Epic 10)

**Actions taken**:
- Git pull: already up to date
- Docker containers were running but had outdated code (missing Sprint endpoints)
- Updated API + MCP containers via `docker cp` (Docker Hub unreachable for rebuild)
- Ran 60-test CRUD smoke test: all passed
- Verified MCP service layer sharing via HTTP MCP protocol
- Updated both tasks to `done` with review comments

**Issues encountered**:
- Docker Hub unreachable, used `docker cp` workaround
- API container had AGENTBOARD_REQUIRE_AUTH=1, needed auth token for tests
- MCP container needed migration files copied + restart
- Test data conflicts (duplicate project keys) resolved with unique suffixes

**No remaining in_review tasks.**

## 2026-07-12 14:10 Execution

**Status**: Completed successfully

**Findings**:
- Found 2 tasks in `in_review` status: Task #82 (Sprint 数据模型与 REST API) and Task #83 (前端 Sprint 管理 UI)
- Both belong to AgentBoard project (id=3), Story 26 (Epic 12)

**Actions taken**:
- Git pull: already up to date
- Docker containers running with Sprint code already present
- Verified API container has Sprint model, routes, and service functions
- Ran Sprint API smoke test (46/46 passed): CRUD, activate, single-active constraint, task-sprint assignment, complete+backfill
- Installed Playwright + Chromium for UI testing
- Ran Sprint UI E2E test (13/13 passed): SPA load, login, project nav, Sprint view, create/activate/complete via UI

**Issues found and fixed**:
1. **Web container `web_app.py` outdated**: Missing catch-all route for Angular assets → JS/CSS 404. Fixed via `docker cp`.
2. **CORS 401 missing headers**: `require_business_auth` middleware returned 401 JSONResponse without CORS headers. Fixed by adding `Access-Control-Allow-Origin` and `Access-Control-Allow-Credentials` to the 401 response.
3. Docker Hub still unreachable, used `docker cp` workaround.

**Code changes committed**:
- `agentboard/api.py`: CORS fix on 401 response
- `tests/test_sprint_api_review.py`: Sprint API test script (46 tests)
- `tests/test_sprint_ui_review.py`: Sprint UI Playwright test (13 tests)
- Committed and pushed: `9514b8b`

**Both tasks updated to `done` with detailed review comments.**

**No remaining in_review tasks.**

## 2026-07-12 15:50 Execution

**Status**: Completed successfully

**Findings**:
- Found 2 tasks in `in_review` status: Task #84 (Sprint/Backlog Web 视图与 MCP 工具) and Task #85 (附件元数据模型、本地安全存储与大小/MIME 限制)
- Both belong to AgentBoard project (id=3)

**Actions taken**:
- Git pull: already up to date
- Docker containers all running (API, MCP, Web, DB)
- MCP container had outdated code (missing Attachment model + migration file) → fixed via `docker cp` (models.py, service.py, migration file) + restart
- Verified attachments table exists in MariaDB with correct schema
- Wrote comprehensive test script `tests/test_review_84_85.py` (35 tests)
- Task #84: 19/19 tests passed (Sprint API CRUD + lifecycle, Backlog web view, MCP 8 sprint tools)
- Task #85: 16/16 tests passed (Attachment upload/download/info/delete, MIME whitelist, size limit, UUID security)
- Single active sprint constraint verified: uses auto-deactivate approach (valid design)
- Both tasks updated to `done` with detailed review comments

**Issues encountered**:
- MCP container missing migration file `9e4f1d5c8a3b_add_attachments.py` → copied via `docker cp`
- MCP container missing updated `models.py` and `service.py` → copied via `docker cp`
- Docker Hub still unreachable, used `docker cp` workaround

**Committed and pushed**: `019d05a`

**No remaining in_review tasks.**
