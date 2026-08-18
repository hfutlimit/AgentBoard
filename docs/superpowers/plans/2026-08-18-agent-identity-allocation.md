# Agent Identity, Allocation, and Capability Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give AgentBoard credential-derived Agent identity, atomic assignment history, Agent-level outcome attribution, structured capability profiles, opt-in arbitration, and deterministic task/reviewer matching.

**Architecture:** Keep User as the authorization principal and Agent as the execution principal. Bind API keys to Agents, reserve tasks through one CAS assignment service, link Run and Outcome to the resulting assignment, and isolate deterministic matching in a pure scheduling module consumed by claim, arbitration, scheduler, and reviewer flows.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2, Alembic, SQLite/MariaDB, FastMCP, pytest.

**Spec:** `docs/superpowers/specs/2026-08-18-agent-identity-allocation-design.md`

## Global Constraints

- Work directly on the current `main` branch; do not create a worktree.
- Preserve the HTTP-only MCP boundary; MCP tools call REST and never import DB/session services.
- Preserve existing REST/MCP clients through additive defaults and compatibility fields.
- Derive Agent identity only from a verified Agent-bound API key, never caller-supplied task payload.
- Keep SQLite and MariaDB migration behavior equivalent.
- Do not guess ambiguous historical Agent attribution.
- Use TDD for every production behavior change and push only after final verification.

---

### Task 1: Persistence model and additive migration

**Files:**
- Modify: `agentboard/features/identity/models.py`
- Modify: `agentboard/features/projects/models.py`
- Modify: `agentboard/features/work_items/models.py`
- Modify: `agentboard/features/scheduling/models.py`
- Modify: `agentboard/features/learning/models.py`
- Modify: `agentboard/models.py`
- Create: `migrations/versions/g2h3i4j5k6l7_agent_identity_allocation.py`
- Create: `tests/test_agent_identity_allocation.py`

**Interfaces:**
- Produces: `TaskAssignment`, `TaskApplication`, and additive `agent_registry_id`, `assignment_id`, profile, and API-key binding columns.
- Produces invariant: `UNIQUE(task_id, active_slot)` permits one active assignment per task.

- [ ] **Step 1: Write model-contract tests**

```python
def test_assignment_models_enforce_one_active_slot(session, seeded_task_and_agents):
    first = TaskAssignment(task_id=tid, agent_registry_id=a1.id,
                           source="claim", status="active", active_slot="active")
    session.add(first); session.commit()
    session.add(TaskAssignment(task_id=tid, agent_registry_id=a2.id,
                               source="schedule", status="active", active_slot="active"))
    with pytest.raises(IntegrityError):
        session.commit()

def test_profile_and_attribution_columns_exist():
    assert {"needed_capabilities", "complexity", "domain_tags",
            "assignment_mode", "current_assignment_id"} <= set(Task.__table__.columns.keys())
    assert {"agent_registry_id", "assignment_id"} <= set(AgentRun.__table__.columns.keys())
```

- [ ] **Step 2: Run RED**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_agent_identity_allocation.py -x`

Expected: import/column assertions fail because assignment models and new columns do not exist.

- [ ] **Step 3: Add ORM models and migration**

Implement these exact persisted concepts:

```python
class TaskAssignment(Base):
    __tablename__ = "task_assignments"
    __table_args__ = (UniqueConstraint("task_id", "active_slot",
                                       name="uq_task_assignment_active_slot"),)
    # task_id, agent_registry_id, user_id, source, status, active_slot,
    # match_score, match_reason, created_at, completed_at

class TaskApplication(Base):
    __tablename__ = "task_applications"
    __table_args__ = (UniqueConstraint("task_id", "agent_registry_id",
                                       name="uq_task_application_agent"),)
    # task_id, agent_registry_id, user_id, score, reason, status,
    # created_at, resolved_at
```

Migration `g2h3i4j5k6l7` revises `f1g2h3i4j5k6`, creates both tables, adds all nullable attribution FKs and task profile defaults, widens capabilities to text, creates indexes, maps exact `AgentRun.agent` values, and backfills outcome registry IDs only for unique user-to-Agent mappings.

- [ ] **Step 4: Run GREEN and migration graph checks**

Run:

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_agent_identity_allocation.py
.venv/Scripts/python.exe -m alembic heads
```

Expected: tests pass and the only head is `g2h3i4j5k6l7`.

- [ ] **Step 5: Commit**

```powershell
git add agentboard/features migrations/versions/g2h3i4j5k6l7_agent_identity_allocation.py tests/test_agent_identity_allocation.py
git commit -m "feat(allocation): add agent attribution persistence"
```

### Task 2: Credential-derived ActorContext and Agent-bound API keys

**Files:**
- Modify: `agentboard/api_helpers.py`
- Modify: `agentboard/schemas.py`
- Modify: `agentboard/features/identity/service.py`
- Modify: `agentboard/features/auth/router.py`
- Modify: `tests/test_agent_identity_allocation.py`

**Interfaces:**
- Produces: `ActorContext` and `resolve_actor_context(authorization, s, required_permission=None)`.
- Produces: API key create/patch field `agent_ref: str | None` and response fields `agent_registry_id`/`agent_ref`.

- [ ] **Step 1: Write failing identity tests**

```python
def test_agent_bound_api_key_resolves_exact_agent(client, session):
    # one user owns two Agents; create a key bound to the second
    actor = resolve_actor_context(f"Bearer {plaintext}", session)
    assert actor.user_id == user.id
    assert actor.agent_registry_id == second.id
    assert actor.agent_ref == second.agent_id

def test_api_key_cannot_bind_other_users_agent(client):
    response = client.post("/api/api-keys", json={"name": "bad",
        "permissions": ["api:write"], "agent_ref": other_agent.agent_id}, headers=headers)
    assert response.status_code == 422
```

- [ ] **Step 2: Run RED**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_agent_identity_allocation.py -x`

Expected: `ActorContext`/`agent_ref` is missing.

- [ ] **Step 3: Implement identity resolution**

```python
@dataclass(frozen=True)
class ActorContext:
    user_id: int
    is_admin: bool
    api_key_id: int | None = None
    agent_registry_id: int | None = None
    agent_ref: str | None = None

def resolve_actor_context(authorization: str | None, s: Session,
                          required_permission: str | None = None) -> ActorContext:
    # parse login token or hash/lookup abk_; enforce permission; join bound Agent
```

Extend API-key create/update service methods to resolve `agent_ref`, require `Agent.user_id == user_id`, and persist the registry FK. Keep `_current_user()` and `_caller_uid_admin()` behavior compatible by delegating to the new resolver where a route session is available.

- [ ] **Step 4: Run GREEN plus auth regressions**

Run:

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_agent_identity_allocation.py tests/test_admin_api_key_scope.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add agentboard/api_helpers.py agentboard/schemas.py agentboard/features/identity agentboard/features/auth tests/test_agent_identity_allocation.py
git commit -m "feat(auth): bind API keys to agent identities"
```

### Task 3: Structured profiles and deterministic matcher

**Files:**
- Create: `agentboard/features/scheduling/matching.py`
- Modify: `agentboard/features/scheduling/service.py`
- Modify: `agentboard/features/work_items/service.py`
- Modify: `agentboard/schemas.py`
- Create: `tests/test_task_matching_arbitration.py`

**Interfaces:**
- Produces: `normalize_capabilities(value) -> list[dict]`.
- Produces: `MatchResult(eligible, score, reason, components)`.
- Produces: `score_agent_for_task(s, agent, task, role="developer")` and `rank_agents_for_task(...)`.

- [ ] **Step 1: Write failing normalization and ranking tests**

```python
def test_legacy_capability_normalizes_to_structured_entry():
    assert normalize_capabilities('["Frontend"]') == [
        {"name": "frontend", "level": 3, "confidence": 0.5}
    ]

def test_matching_prefers_capability_history_and_lower_load(session, seeded):
    ranked = rank_agents_for_task(session, task, role="developer")
    assert ranked[0].agent.agent_id == "frontend-senior"
    assert ranked[0].result.eligible is True

def test_missing_required_capability_is_ineligible(session, seeded):
    result = score_agent_for_task(session, backend_only, frontend_task)
    assert result.eligible is False
    assert "frontend" in result.reason
```

- [ ] **Step 2: Run RED**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_task_matching_arbitration.py -x`

Expected: matching module is missing.

- [ ] **Step 3: Implement the pure matching module and profile validation**

Use the documented weights exactly:

```python
score = round(0.35 * coverage + 0.25 * proficiency +
              0.10 * confidence + 0.20 * history +
              0.10 * load_factor, 6)
```

Validate task fields at create/update boundaries: capabilities/domain tags are normalized JSON lists, complexity is null or 1..5, and assignment mode is `claim|arbitrated`. Register/update Agent normalizes legacy or structured capabilities before persistence.

- [ ] **Step 4: Run GREEN**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_task_matching_arbitration.py`

Expected: all matcher tests pass deterministically.

- [ ] **Step 5: Commit**

```powershell
git add agentboard/features/scheduling/matching.py agentboard/features/scheduling/service.py agentboard/features/work_items/service.py agentboard/schemas.py tests/test_task_matching_arbitration.py
git commit -m "feat(matching): add structured capability scoring"
```

### Task 4: Unified assignment CAS and claim integration

**Files:**
- Modify: `agentboard/features/work_items/service.py`
- Modify: `agentboard/features/work_items/router.py`
- Modify: `tests/test_agent_identity_allocation.py`
- Modify: `tests/test_epic122_s2m1.py`

**Interfaces:**
- Produces: `try_assign_task(..., commit=True) -> tuple[Task, TaskAssignment]`.
- Extends: `claim_development_task(..., agent_registry_id=None, source="claim")`.
- Produces: `finalize_task_assignment(s, task, status)`.

- [ ] **Step 1: Write failing CAS and attribution tests**

```python
def test_two_agents_same_user_are_attributed_separately(session, actors, task):
    claimed, assignment = claim_development_task(
        session, task.id, user_id=actors.user_id,
        agent_registry_id=actors.second_agent_id)
    assert claimed.current_assignment_id == assignment.id
    assert assignment.agent_registry_id == actors.second_agent_id

def test_assignment_conflict_leaves_one_active_row(session, task, two_agents):
    # first call succeeds; second raises InvalidValue
    assert session.query(TaskAssignment).filter_by(
        task_id=task.id, active_slot="active").count() == 1
```

- [ ] **Step 2: Run RED**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_agent_identity_allocation.py tests/test_epic122_s2m1.py -x`

Expected: claim does not return/create an assignment.

- [ ] **Step 3: Implement one transaction for assignment and Task CAS**

`try_assign_task()` inserts and flushes an active assignment, conditionally updates `Task.status/current_assignment_id/assignee_id`, writes status history, and commits once. On `IntegrityError` or zero updated rows, rollback and raise the existing conflict message. The route obtains `ActorContext` and supplies its registry ID. For `assignment_mode=arbitrated`, direct Agent claim returns a conflict directing the caller to `/apply`; a human/manual claim remains an explicit fallback.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_agent_identity_allocation.py tests/test_epic122_s2m1.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add agentboard/features/work_items tests/test_agent_identity_allocation.py tests/test_epic122_s2m1.py
git commit -m "feat(allocation): unify task claim assignments"
```

### Task 5: Application and arbitration APIs/MCP tools

**Files:**
- Modify: `agentboard/features/work_items/service.py`
- Modify: `agentboard/features/work_items/router.py`
- Modify: `agentboard/schemas.py`
- Modify: `agentboard/mcp_server.py`
- Modify: `tests/test_task_matching_arbitration.py`
- Modify: `tests/test_mcp_api_boundary.py`

**Interfaces:**
- Produces: `apply_for_task(s, task_id, actor) -> TaskApplication`.
- Produces: `arbitrate_task(s, task_id, changed_by) -> tuple[Task, TaskAssignment]`.
- Produces REST/MCP tools `apply_for_task` and `arbitrate_task`.

- [ ] **Step 1: Write failing application flow tests**

```python
def test_arbitration_accepts_highest_match_and_rejects_others(client, seeded):
    assert apply_as(agent_a).status_code == 200
    assert apply_as(agent_b).status_code == 200
    result = owner_client.post(f"/api/tasks/{task.id}/arbitrate")
    assert result.status_code == 200
    assert result.json()["assignment"]["agent_registry_id"] == agent_b.id
    assert application_statuses() == {agent_a.id: "rejected", agent_b.id: "accepted"}

def test_apply_requires_agent_scoped_key(client, human_token, task):
    assert client.post(f"/api/tasks/{task.id}/apply", headers=human_token).status_code == 422
```

- [ ] **Step 2: Run RED**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_task_matching_arbitration.py tests/test_mcp_api_boundary.py -x`

Expected: endpoints/tools are missing.

- [ ] **Step 3: Implement service, router, and HTTP-only MCP wrappers**

Application upserts the caller's row with a fresh deterministic score. Arbitration orders pending applications by score descending then Agent ID ascending, calls `try_assign_task(source="arbitration")`, updates application statuses, and publishes `EVENT_TASK_ASSIGNED` to the winning Agent. MCP wrappers only call `_http()`.

- [ ] **Step 4: Run GREEN**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_task_matching_arbitration.py tests/test_mcp_api_boundary.py`

Expected: all pass and boundary tests find no DB imports in MCP.

- [ ] **Step 5: Commit**

```powershell
git add agentboard/features/work_items agentboard/schemas.py agentboard/mcp_server.py tests/test_task_matching_arbitration.py tests/test_mcp_api_boundary.py
git commit -m "feat(allocation): add task application arbitration"
```

### Task 6: Scheduler reservation, outcome attribution, and reviewer matching

**Files:**
- Modify: `agentboard/features/scheduling/service.py`
- Modify: `agentboard/scheduler.py`
- Modify: `agentboard/features/learning/service.py`
- Modify: `agentboard/features/work_items/service.py`
- Modify: `tests/test_schedule_unbind.py`
- Modify: `tests/test_learning_outcome.py`
- Modify: `tests/test_epic122_s2m2.py`
- Modify: `tests/test_agent_identity_allocation.py`

**Interfaces:**
- Schedule-created runs carry `agent_registry_id` and `assignment_id`.
- Outcomes carry final assignment and Agent attribution.
- Reviewer assignment consumes `rank_agents_for_task(role="reviewer")`.

- [ ] **Step 1: Write failing integration tests**

```python
def test_two_schedules_cannot_dispatch_same_todo_task(session, schedules, task):
    assert _trigger_one(session, schedules[0], now) is True
    assert _trigger_one(session, schedules[1], now) is False
    assert session.query(AgentRun).filter_by(task_id=task.id).count() == 1

def test_outcomes_group_two_agents_on_same_user_separately(session, completed):
    rows = agent_leaderboard(session, project_id=project.id)
    assert {row["agent_ref"] for row in rows} == {"agent-a", "agent-b"}

def test_reviewer_ranking_replaces_random_choice(session, review_task):
    assigned = assign_task_reviewer(session, review_task.id)
    assert assigned.reviewer_id == best_reviewer.user_id
```

- [ ] **Step 2: Run RED**

Run:

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_schedule_unbind.py tests/test_learning_outcome.py tests/test_epic122_s2m2.py tests/test_agent_identity_allocation.py -x
```

Expected: duplicate dispatch/Agent grouping/deterministic reviewer assertions fail.

- [ ] **Step 3: Integrate assignment and matching**

Resolve or create built-in Agent registry rows for supported scheduler adapters; include `minimax` in the allow-list. `create_run()` reserves the task and creates the run atomically, while `_trigger_one()` treats allocation conflicts as a skipped run. Terminal status paths finalize assignments before recording outcomes. `record_outcome()` copies current assignment registry ID/reference. Leaderboard groups by registry Agent while retaining legacy `user_id`. Replace reviewer `random.choice` with the ranked eligible candidate.

Update the two stale review tests to patch `agentboard.features.work_items.router.publish_workflow_event`, the namespace actually called after router extraction.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_schedule_unbind.py tests/test_learning_outcome.py tests/test_epic122_s2m2.py tests/test_agent_identity_allocation.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add agentboard/features agentboard/scheduler.py tests
git commit -m "feat(allocation): connect runs outcomes and reviewers"
```

### Task 7: Migration, compatibility, and final verification

**Files:**
- Modify: `docs/agent-collaboration-requirements.md`
- Modify: `docs/superpowers/specs/2026-08-18-agent-identity-allocation-design.md` only if implementation reveals a contradiction
- Modify: `docs/superpowers/plans/2026-08-18-agent-identity-allocation.md` checkbox statuses

**Interfaces:**
- Produces a single Alembic head, focused green test evidence, documentation, commits, and pushed upstream branch.

- [ ] **Step 1: Test migration on a copied populated SQLite database**

Use a disposable copy under ignored `tmp/`, upgrade it to `g2h3i4j5k6l7`, and query column/table/index presence. Never run downgrade or destructive migration checks against `agentboard.db`.

- [ ] **Step 2: Run focused regression suite**

```powershell
.venv/Scripts/python.exe -m pytest -q `
  tests/test_agent_identity_allocation.py `
  tests/test_task_matching_arbitration.py `
  tests/test_epic122_s2m1.py `
  tests/test_epic122_s2m2.py `
  tests/test_schedule_unbind.py `
  tests/test_learning_outcome.py `
  tests/test_admin_api_key_scope.py `
  tests/test_mcp_api_boundary.py `
  tests/test_domain_boundaries.py
```

Expected: zero failures.

- [ ] **Step 3: Run structural verification**

```powershell
.venv/Scripts/python.exe -m alembic heads
.venv/Scripts/python.exe -m compileall -q agentboard
git diff --check
git status --short
```

Expected: one migration head, compile success, no whitespace errors, and only intended files changed.

- [ ] **Step 4: Review the complete diff against the spec**

Confirm credential-derived identity, atomic allocation, attribution, profiles, arbitration, scheduler reservation, reviewer ranking, compatibility, and docs each have code plus tests. Remove generated files and unrelated changes.

- [ ] **Step 5: Commit remaining docs/checklist updates and push**

```powershell
git add docs
git commit -m "docs: document agent identity and allocation"
git push
```

Expected: push succeeds to the configured upstream of `main`.
