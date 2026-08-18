# Agent Identity, Allocation, and Capability Matching Design

## Status and scope

This design upgrades AgentBoard's task allocation foundation without replacing its event-driven and CAS-based collaboration flow. It covers authenticated Agent attribution, task assignment history, opt-in application/arbitration, structured task and Agent capability profiles, scheduler reservation, outcome attribution, and deterministic reviewer selection.

The existing human/user identity remains the authorization and notification principal. The Agent registry becomes the execution and capability-learning principal. Existing REST and MCP entry points remain available; new fields and endpoints are additive unless this document explicitly states otherwise.

Frontend forms for editing the new profile fields are not part of this change. The fields are available through REST and MCP, and existing UI continues to operate with defaults.

## Problem statement

Today, task ownership and outcomes are attributed to `users.id`, while executions are attributed to `AgentRun.agent`, a string. `Agent.user_id` is nullable and non-unique, and REST/MCP authentication resolves API keys to a user only. Consequently, two Agents bound to one service account cannot be distinguished reliably. The scheduler also creates a run without atomically reserving the selected task, so different schedules can dispatch the same `todo` task.

Capability metadata exists as an unconsumed JSON list, task capability requirements do not exist, and reviewer selection is random after eligibility filtering. These gaps prevent trustworthy Agent-level learning and matching.

## Alternatives considered

### 1. Add `agent_ref` strings to Task and TaskOutcome

This is the smallest schema patch, but the claim endpoint still cannot determine a trustworthy Agent identity. It also overwrites attribution when a task is reassigned and has no execution-attempt history. Rejected.

### 2. Make `agents.user_id` unique and treat User as Agent

This would make the current bridge joinable, but it prevents multiple Agent configurations/models from sharing one service account and keeps authorization identity coupled to execution identity. Rejected.

### 3. Add authenticated Agent context plus assignment/application records

API keys optionally bind to one Agent, task ownership is represented by an immutable assignment record, and runs/outcomes reference that record. Existing user attribution is retained for permissions and compatibility. This is the selected approach.

## Identity model

`ApiKey` gains nullable `agent_registry_id -> agents.id`. API key create/update accepts an external `agent_ref`; the server resolves it and verifies that the Agent belongs to the API key owner. An Agent-scoped API key may identify exactly one Agent. User login tokens remain human/manual actors and have no Agent identity.

`api_helpers.resolve_actor_context()` returns:

```text
ActorContext(user_id, is_admin, api_key_id, agent_registry_id, agent_ref)
```

The context is derived from the verified credential. Request bodies and headers cannot assert or override Agent identity. Existing `_current_user()` and `_caller_uid_admin()` remain compatible wrappers.

The legacy `Agent.auth_key` string remains readable during this release but is deprecated and never used as proof of identity.

## Allocation data model

### TaskAssignment

`task_assignments` records each ownership attempt:

- `task_id -> tasks.id`
- `agent_registry_id -> agents.id`, nullable for human/manual assignments
- `user_id -> users.id`, nullable for system Agents without service accounts
- `source`: `claim`, `arbitration`, `schedule`, `manual`, or `worker`
- `status`: `active`, `completed`, `released`, or `cancelled`
- `active_slot`: the literal `active` for an active assignment, otherwise null
- `match_score` and `match_reason`: decision snapshot
- timestamps

`UNIQUE(task_id, active_slot)` provides a cross-SQLite/MariaDB invariant of at most one active assignment while allowing unlimited historical null slots.

`Task.current_assignment_id` points to the active or final assignment. `Task.assignee_id` remains the user responsible for permissions, notifications, and compatibility.

### TaskApplication

`task_applications` stores applications for arbitrated tasks:

- unique `(task_id, agent_registry_id)`
- applicant `user_id`
- score and reason captured when applying
- `pending`, `accepted`, `rejected`, or `withdrawn`
- timestamps

`Task.assignment_mode` is `claim` by default or `arbitrated` when applications must be collected and resolved. Existing tasks therefore retain current first-eligible-claim behavior.

### CAS behavior

All allocation sources call one `try_assign_task()` transaction. It inserts the active assignment, conditionally updates `Task` only when `status=todo` and `current_assignment_id IS NULL`, writes status history, and commits both changes together. A unique-slot or conditional-update conflict returns the existing conflict response and leaves no orphan assignment.

When a task reaches `done` or `blocked`, its active assignment becomes `completed` and releases `active_slot`; `current_assignment_id` remains as the final attribution pointer.

## Execution and outcome attribution

`AgentRun` gains:

- `agent_registry_id -> agents.id`
- `assignment_id -> task_assignments.id`

The existing `agent` and `model` fields remain immutable execution snapshots. Schedule-triggered runs resolve or create the registered built-in Agent, reserve the task through `try_assign_task(source="schedule")`, and create the run in the same transaction. The built-in scheduler allow-list is aligned with registered executor adapters, including `minimax`.

`TaskOutcome` gains:

- `agent_registry_id -> agents.id`
- `assignment_id -> task_assignments.id`
- `agent_ref` snapshot

The existing numeric `agent_id -> users.id` remains as a deprecated compatibility field. New leaderboard rows group by registry Agent and return `agent_registry_id`, `agent_ref`, and `user_id`. Historical outcomes are backfilled only when one user maps to exactly one Agent; ambiguous rows remain explicitly unattributed.

## Capability and task profiles

Task gains:

- `needed_capabilities`: JSON list, default `[]`
- `complexity`: nullable integer 1 through 5
- `domain_tags`: JSON list of strings, default `[]`
- `assignment_mode`: `claim` or `arbitrated`, default `claim`

Agent `capabilities` accepts both legacy string tags and structured entries:

```json
[
  {"name": "frontend", "level": 4, "confidence": 0.8},
  {"name": "long-running", "level": 3, "confidence": 0.6}
]
```

Legacy `"frontend"` normalizes to level 3 and confidence 0.5. Capability names and domain tags are lower-cased, trimmed, deduplicated, and length-bounded. Agent capability storage is widened to text.

## Matching service

`features/scheduling/matching.py` owns parsing, eligibility, scoring, and ranking. It has no HTTP or executor dependencies.

An Agent is eligible when it is enabled, has the requested role, and meets every task capability minimum. Reviewer candidates must additionally be online project members and cannot share the task assignee's user identity.

The deterministic score is:

```text
0.35 capability coverage
+ 0.25 normalized proficiency
+ 0.10 declared confidence
+ 0.20 historical outcome score (0.5 cold-start default)
+ 0.10 load factor (1 / (1 + active work))
```

When no capability is required, coverage is 1.0 and proficiency/confidence use neutral defaults. Ties resolve by lower active load and then lower Agent primary key. The result includes a machine-readable component breakdown serialized into `match_reason` for auditability.

Random reviewer selection is replaced by this ranking. Majority-review eligibility rules remain unchanged.

## Application and arbitration flow

New endpoints and MCP tools:

- `POST /api/tasks/{id}/apply`: requires an Agent-scoped credential and an `arbitrated` `todo` task; upserts the caller's pending application with a fresh score.
- `POST /api/tasks/{id}/arbitrate`: requires project owner or admin; chooses the highest-ranked pending application and calls `try_assign_task(source="arbitration")`.

On successful arbitration, the winner becomes `accepted`, other pending applications become `rejected`, and the existing `task.assigned` event is published. Direct claim remains available for `claim` tasks. Calling direct claim on an `arbitrated` task returns a conflict directing the caller to apply.

## Migration and compatibility

The migration is additive and supports SQLite and MariaDB. It creates the two new tables, adds nullable attribution foreign keys, adds task profile columns with server defaults, widens Agent capabilities, and adds indexes.

Backfill rules:

1. Map `AgentRun.agent` to `agents.agent_id` when exact.
2. Map TaskOutcome user attribution only when exactly one Agent owns that user.
3. Do not fabricate historical assignments or guess ambiguous mappings.
4. Preserve all existing string snapshots and user IDs.

Existing task creation, claim, review, scheduling, and learning clients continue to work with default `claim` mode and empty profiles. API responses gain additive fields.

## Error handling and security

- Agent identity is credential-derived; mismatched Agent/API-key ownership is rejected.
- Applying without an Agent-scoped key is rejected with 422.
- Capability/profile JSON is validated before persistence.
- Allocation conflicts roll back assignment/application changes atomically.
- Matching failures return an eligibility explanation without leaking credentials.
- Deleting an Agent with historical attribution is not introduced; existing disable/deregister behavior remains the operational path.

## Verification

The implementation must include:

- migration upgrade tests on a populated pre-change SQLite database
- ActorContext and API-key Agent-binding tests
- same-user/two-Agent attribution tests
- assignment CAS and no-orphan conflict tests
- schedule double-dispatch prevention tests
- outcome and leaderboard Agent-level grouping tests
- legacy and structured capability normalization tests
- application/arbitration winner tests
- deterministic reviewer ranking and author-isolation tests
- MCP boundary tests proving the MCP server remains HTTP-only
- focused regression tests for claim, review, scheduler, learning, domain boundaries, and migration head

No implementation is complete until the focused suites pass, the Alembic graph has one head, `git diff --check` is clean, and the final commit is pushed to the configured upstream branch.
