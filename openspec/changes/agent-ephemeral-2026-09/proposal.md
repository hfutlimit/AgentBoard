# Change: Agent Ephemeral Registry (Worker-owned, Server-cache only)

**status**: draft
**date**: 2026-09-02
**author**: architecture-driven (operator request, 2026-09-02 09:24)

## Why

Today, agent configuration lives in **two places** that must stay in
sync, and the server is the source of truth:

- **Server DB** — `agents` (logical) + `agent_instances` (per-worker) tables,
  written by worker portal's `POST /api/agents/{id}/instances` (upsert) and
  `DELETE /api/agents/{id}/instances` (added 2026-09-02, commit `e4909df`).
- **Worker host** — CLI binary location, OAuth login state, codebuddy CLI
  version, environment variables. These never round-trip to the server.

Consequences:

1. The server's `cli_command` is a **stale string** the moment any of the
   worker-side state changes (CLI upgrade, OAuth re-login, env var edit).
2. Swapping worker hosts for the same logical agent requires manual
   `DELETE` on the old + `POST` on the new — and there's no way to know
   which worker actually owns the configuration.
3. The "operator swaps the 4 codebuddy presets" flow (2026-09-02) had to
   wait for the server to gain a DELETE endpoint that the worker portal
   could call — a 4-file cross-stack change to remove 4 rows.

The user-stated principle (2026-09-02 09:24):

> "前端只是根据 heart-beat 来展示 agent；但是后端其实不需要维护 agent；
> agent 全部在 worker 端维护，服务器端只需要知道当前有几个 agent 可用，
> 可以主动选择哪个 agent 来做；但是后端不用持久化存储"

The server's job shrinks to: **route inbound intent to a live agent**.
Worker hosts own configuration. The server holds a transient in-memory
cache that is rebuilt from heartbeats; if the cache is empty, dispatch
fails fast.

## What changes

- **Worker portal** writes the operator's agent config to a local SQLite
  DB on the worker host (not the server).
- **Worker runtime** (`.NET ProposalWorker` + Python `agentboard.worker`)
  starts a WebSocket connection to the server at boot. On connect, the
  worker pushes its current agent set (one frame). On every state change
  (operator edit, CLI restart, OAuth re-login) the worker pushes a
  delta frame. The server updates its in-memory cache.
- **Server cache** is a per-process `dict[agent_id, AgentCacheEntry]`
  in the FastAPI app, plus a `set[(worker_id, agent_id)]` for
  cross-worker dispatch lookups. **No DB rows are written for the new
  path.** Existing `agent_instances` rows are left untouched (operator
  will manually clean up; see "DB policy" below).
- **Server dispatch** (review / task-execution / agent routing) reads
  the cache. If a task arrives for an agent that is not in the cache, the
  server returns 503 with a `Retry-After: 30` header instead of failing
  silently. The MQ producer is unchanged — it already tolerates
  transient unavailability (re-claim by another worker on stale claim).
- **Admin / AgentBoard portals** that today read
  `GET /api/agents` from the DB now read it from the cache. The wire
  shape stays the same: `[{agent_id, worker_id, cli_command, model,
  online, last_heartbeat, ...}]` — server fills the same fields it
  filled before, just from memory.
- **Admin dispatch UI** gets an explicit "pick agent" control per task
  (current default: random pick from agents matching the task's
  capability hint). When set, the picker records the choice on the
  Task row so retry stays on the same agent.

### Out of scope (deliberate)

- **DB schema changes.** The `agents` and `agent_instances` tables stay
  exactly as they are. The new path simply does not write to them.
  Operator (jason) will manually delete legacy rows after migration.
  No migration script in this change. See "DB policy" below.
- **Cross-worker shared logical agent.** A user with two worker hosts
  does not see them merged in the UI. Each worker is its own
  configuration namespace; the server routes per-worker. A task that
  the operator wants to run "on whichever worker picks it up first"
  gets a fan-out MQ message; only the workers that registered the
  matching agent_id pick it up. (If two workers register the same
  agent_id, the MQ routing key decides — RabbitMQ direct exchange with
  a per-(worker,agent) queue, same as today.)
- **Admin override of CLI templates.** The server has no business
  knowing what `codex --model gpt-5.6-sol` means. Operators that want a
  different CLI bind the same `agent_id` to a different physical CLI on
  a different worker.
- **Historical execution audit.** Existing run records stay in DB and
  are not affected.

## Decisions (operator-confirmed 2026-09-02 09:25)

| Key | Choice | Rationale |
|---|---|---|
| **A. Worker storage** | Local SQLite (`~/.codebuddy/agents.db` on the worker host) | Survives CLI restart; portable for backup; no external DB dep per worker |
| **B. Heartbeat transport** | WebSocket (replaces current 30-60s HTTP POST heartbeat) | Lower latency on operator-driven edits; cheaper per-frame; pushes deltas without polling |
| **C. Cross-worker sharing** | None. One machine, one worker, independent agent_id namespace. Same user can have multiple workers — tasks may be cross-assigned at the operator's discretion, but each worker has its own config. | Simplest mental model; aligns with "one machine one worker" physical layout |
| **D. Dispatch selection** | Random pick from eligible agents by default; operator can pin a specific agent on a per-task basis (new optional field on Task) | Default keeps current behavior; pin lets humans steer when needed |
| **E. Server-restart cache miss** | Return 503 `Retry-After: 30` if dispatch target not in cache | Fail-fast; the 30s gives the worker time to re-register via WebSocket reconnect; alternative was buffering intent (more code, more state) |
| **F. Migration of legacy rows** | None. Operator manually deletes from `agent_instances` after worker handoff; no script in this change | Database schema is "off-limits" for now per operator instruction; legacy rows are inert (new dispatch never reads them) |
| **G. Database policy** | **Read-only** for `agent_instances` going forward. New code never `INSERT` / `UPDATE` into `agent_instances`. Old code paths that still do are gated behind a feature flag `AGENTBOARD_EPHEMERAL_AGENTS=1` (off by default) so an operator can A/B test. The tables stay in the schema; legacy data is preserved. | Preserves blast radius; rollback is just toggling the flag off |

## Design principles

1. **Worker is the source of truth.** The server never writes a CLI
   command. The server's job is to route intent to a live worker, not
   to define what a worker can do.
2. **Cache is reconstructable.** If the FastAPI process restarts, the
   next 30 seconds see some 503s while workers reconnect via WebSocket
   and push their full agent set. The system is "boring eventually
   consistent" — same property RabbitMQ gives us for messages.
3. **MQ is still the source of truth for "who got the message".** A
   worker's WebSocket connection is **advisory** — the actual delivery
   goes through the existing per-worker MQ queue. The cache only
   controls *who we choose to address the next message to*.
4. **Backward compat is flag-gated.** Setting
   `AGENTBOARD_EPHEMERAL_AGENTS=1` switches the new server-side
   dispatch + admin reads onto the cache. Unset = the old DB path
   keeps working. Default unset in this change.
5. **No operator-visible breakage.** While the flag is unset, the
   current `worker_portal.py` upsert/DELTE path continues to work and
   the existing 4 legacy agents (tf-codex-1, tf-codex-2,
   tf-codebuddy-1, tf-minimax-1 on worker `TF-JASONZHONG`) stay
   addressable. The flag is the operator's migration on/off switch.

## Wire-up sketch

```python
# worker-side (new): agentboard/worker/registry.py
class WorkerAgentRegistry:
    def __init__(self, db_path="~/.codebuddy/agents.db"):
        self._db = sqlite3.connect(db_path)
        self._db.execute("CREATE TABLE IF NOT EXISTS agents ("
            "agent_id TEXT PRIMARY KEY, cli_command TEXT, "
            "model TEXT, enabled INT, roles TEXT, "
            "updated_at TEXT)")

    def list_active(self) -> list[dict]: ...

# worker-side (new): agentboard/worker/wsclient.py
class ServerWebSocketClient:
    """Persistent WSS to the server. Reconnects on drop.

    On (re)connect, sends a HELLO frame with the full agent set:
      {"type": "HELLO", "worker_id": "TF-JASONZHONG",
       "agents": [{"agent_id": "hy4-agent", "cli_command": "...",
                   "model": "hy4-preview", "enabled": true, ...}]}
    On local SQLite change, sends a DELTA frame:
      {"type": "DELTA", "worker_id": "...",
       "add_or_update": [...], "remove": [...]}
    Receives PING every 15s; replies PONG. No inbound command channel
    in this change (server -> worker push is read-only via the
    existing MQ).
    """

# server-side (new): agentboard/agent_registry_cache.py
class AgentRegistryCache:
    """In-memory, per-process. Holds the full live agent set."""
    def __init__(self): self._by_id: dict[str, AgentCacheEntry] = {}
    def apply_hello(self, worker_id, agents): ...  # full replace
    def apply_delta(self, worker_id, adds, removes): ...
    def pick_eligible(self, capability_hint=None, pinned=None) -> str | None:
        if pinned and pinned in self._by_id: return pinned
        eligible = [a for a in self._by_id.values() if a.enabled and a.online]
        if not eligible: return None
        return random.choice(eligible).agent_id
    def snapshot(self) -> list[dict]: ...  # for admin portal

# server-side (modified): agentboard/worker_portal.py
# - AgentBoardProxy and the /api/agents endpoint: when flag is set,
#   read from AgentRegistryCache.snapshot(); when unset, fall back
#   to the existing proxy.get("/api/agents") path.
# - worker_portal.py itself: still runs on the worker host (NOT on
#   the server) so it can render CLI templates with local paths.
#   The only server-side change is the cache read; the upsert/DELTE
#   paths are kept (flag-gated) for the migration window.

# server-side (modified): features/scheduling/router.py
# - dispatch / review / task_execution paths: when flag is set,
#   look up agent via AgentRegistryCache.pick_eligible() and
#   include the agent_id in the MQ routing key. If the picker
#   returns None, return 503 with Retry-After: 30.

# worker-side (modified): appsettings.Local.template.json / agentboard/worker config
# - Remove the per-agent-section (WorkBuddy/Codex/M3/M27/Hy4/Glm53F)
#   the 4 codebuddy-CLI presets we added in commit d3be194.
#   Worker reads from local SQLite instead.
```

## Phases (single PR per phase, gate-gated)

| Phase | Scope | Estimated diff | Gate |
|---|---|---|---|
| **P0 spec** | This document | proposal.md | operator sign-off |
| **P1 server cache + flag** | `AgentRegistryCache` in-memory, `AGENTBOARD_EPHEMERAL_AGENTS` flag, admin `/api/agents` reads cache when flag set. Fallback to DB when unset. | +200 / -50 | 174 unit tests pass; manual admin UI smoke |
| **P2 server dispatch** | Dispatch paths read cache; 503 on miss. **P2 does NOT add a `tasks.pinned_agent_id` column** — the operator-confirmed "DB schema untouched" rule (F/G) keeps DB writes off-limits for this whole change. Pin semantics (when implemented in P5) will live in the cache as a transient operator hint, not in `tasks`. | +150 / -50 | existing e2e suite (1644-style + cross-owner) still green; new e2e: dispatch hits 503 then succeeds after worker re-registers |
| **P3 worker WebSocket + SQLite** | New `worker/registry.py` (SQLite), new `worker/wsclient.py` (WebSocket), wired into both `.NET ProposalWorker` startup and Python `agentboard.worker` main loop | +400 / -0 | worker boots, connects, pushes HELLO; admin UI shows the new agents within 5s |
| **P4 worker portal UI** | `worker-portal` writes to local SQLite (not server); reads back from same SQLite; the server `/api/agents` proxy becomes a **debug viewer** of the cache (read-only) | +100 / -30 | operator manually deletes the 4 legacy `tf-*` instances from server DB; UI continues to show only the 4 new ones |
| **P5 admin dispatch pin** | Admin UI: optional "pin agent" dropdown on Task edit. Persisted as `tasks.pinned_agent_id` (new column, not in scope of this change's "no DB" rule — pin lives in tasks, a different table) | +120 / -0 | UI shows pin; dispatch respects it; unpin falls back to random |
| **P6 (deferred)** | Remove `agents` / `agent_instances` tables. `worker_portal.py` upsert/DELETE endpoints become no-ops or 410 Gone. **Not part of this change** — operator-driven after they're satisfied with the new flow. | TBD | TBD |

## Test plan

- **P1** unit tests: `AgentRegistryCache.apply_hello` /
  `apply_delta` / `pick_eligible` (with/without pinned /
  capability hint / online filter). Mock clock for staleness
  timeout.
- **P2** new e2e: dispatch a task with cache empty → 503
  `Retry-After: 30`. Start a worker that pushes HELLO → next
  dispatch succeeds. Cache stale by >30s → entry dropped →
  503 again.
- **P3** worker integration: SQLite roundtrip,
  WebSocket reconnect-on-drop, HELLO-on-reconnect replaces
  previous state (not merges), DELTA add/remove works.
- **P4** manual: operator deletes the 4 `tf-*` server-side
  instances; UI no longer shows them. New agents added via
  worker portal appear in admin UI within 5s.
- **P5** UI: pin survives reload; unpin reverts to random;
  pinned agent not in cache → dispatch returns 503 even
  with pin set (operator-visible error explains).

## Risks and open questions

1. **WebSocket lifecycle through FastAPI process restarts.** The
   server cache rebuilds on every restart; workers reconnect with
   HELLO. A worker that is *unhealthy but connected* will keep
   itself in the cache as "online" — we need a server-side staleness
   check (cache entry older than 60s without PING → mark offline).
   Decision: yes, P1 includes a 60s staleness sweep. Open:
   sweep frequency (10s? 30s?).
2. **Operator edits while WebSocket is disconnected.** The worker
   should buffer local changes and re-send on reconnect. SQLite is
   durable; only the WebSocket frame is in flight. Risk: worker
   process crash loses buffered in-flight frames. Acceptable: the
   next periodic HELLO (every 5 min?) rebuilds from SQLite. Decision
   needed: HELLO cadence.
3. **Multi-worker "fan-out" of the same task.** A task that is
   eligible for agents on two different workers — which one gets
   it? Today this is decided by MQ routing-key hash. With the
   cache, the server can either (a) keep the current hash-based
   routing, (b) round-robin, (c) load-balance by online count.
   Default: (a) — no behavior change.
4. **Pin semantics with capability mismatch.** If the pinned
   agent doesn't have a capability the task needs, do we fail or
   fall back? Default: fail loud (operator intent is explicit).
5. **Worker authentication on WebSocket.** Current HTTP path uses
   `Bearer AGENTBOARD_WORKER_TOKEN`. Reuse for WebSocket
   `Authorization` header in the WSS upgrade? Open: should we
   issue a separate `abk_ws_*` key per worker for WSS, or reuse
   the existing one? Default: reuse; revoke-on-restart if it
   becomes a problem.
6. **Operator-controlled `agent_id` collision.** If two workers
   both register `hy4-agent`, the cache keeps both as separate
   `(worker_id, agent_id)` entries. The dispatch decision needs
   to be aware of this — pick `(worker, agent)` not just `agent`.
   Open: how does admin UI show "two workers both have hy4-agent"?
   Default: list shows both, dispatch can pin to one or pick random.

## DB policy (operator decision 2026-09-02 09:26)

- **No migration** in this change. The `agents` and
  `agent_instances` tables are not modified.
- Legacy rows (e.g. `tf-codex-1` on `TF-JASONZHONG`) continue to
  exist in DB. They become **inert** when the operator toggles
  `AGENTBOARD_EPHEMERAL_AGENTS=1` (because new dispatch only reads
  the cache, not DB).
- The operator will **manually delete** legacy rows when ready.
  No script is provided in this change.
- The `.NET ProposalWorker` `appsettings.Local.template.json`
  per-agent sections added in commit `d3be194` (M3/M27/Hy4/Glm53F)
  will be **removed in P3** — workers read from local SQLite,
  not from a config file section.
