# Change: Workflow Outbox (DB ↔ MQ Atomicity)

**status**: in_review
**date**: 2026-08-28
**author**: review-driven (GPT P1-1 + P1-3, hy4 静态 review)

## Why

AgentBoard's workflow settlement is split across two storage systems
that are **not** transactionally consistent:

- **DB** (SQLite / MariaDB) — source of truth for task state, review
  comment, status_reason, etc.
- **MQ** (RabbitMQ) — best-effort delivery of "go look at this task"
  events to the .NET Worker.

The current code path on `POST /api/tasks/{tid}/review` does:

1. `set_status()` — Task state machine writes the new status, commits
   in its own transaction, and triggers `finalize_task_assignment` /
   `schedule_judge` as side effects.
2. `create_comment()` — review comment is written in a separate
   commit, **after** the status commit.
3. `publish_workflow_event(EVENT_TASK_REVIEWED)` — best-effort
   `try/except Exception: pass` outside the router's try block. If it
   fails, the event is lost.
4. `for successor in ...: publish_workflow_event(EVENT_TASK_AVAILABLE)` —
   wrapped in `try/except Exception: pass`. Silent loss.
5. `if story done: complete_story(...)` — also `try/except Exception:
   pass`. Silent loss.
6. `api_helpers._notify_webhooks(...)` — outside the try block.
7. `return service._ser(t)` — return to caller.

This is GPT P1-1 + P1-3 from the 2026-08-26 review (commit
``6e5ce0c``): **DB state and MQ event delivery are not in the same
transaction**, and three different `try/except Exception: pass` blocks
silently swallow workflow intent. The commit message of
``6e5ce0c`` already flagged this as a known gap ("Outbox for DB+MQ
atomicity not yet implemented"); this change closes that gap.

The user-visible consequence is that production workflows can get
permanently stuck when the MQ is degraded: a reviewer approves a task,
the DB shows `done`, the `task.reviewed` event never reaches a worker,
and the implementation successor stays in `todo` forever. The
`AGENTBOARD_WORKER_TASK_POLL=1` opt-in is the only fallback today,
and it is **not** on by default.

## What changes

- Add a new ``workflow_outbox`` table that records every workflow
  event the system wants to publish, **in the same DB transaction
  as the state change** that produced it. The router writes the
  state change and the outbox row atomically; the application
  commits once.
- Add an ``OutboxPublisher`` background service (FastAPI startup hook
  or standalone worker) that scans unpublished outbox rows, calls
  ``publish_workflow_event`` for each, and marks the row as published
  on success. Failures retry with exponential backoff up to a cap.
- Replace the three ``try/except Exception: pass`` blocks in
  ``review_task`` with one outbox write. The story-completion and
  successor-unlock events are folded into the same outbox write so
  the workflow intent is durable.
- On MQ unavailability, the outbox row simply stays ``published_at
  IS NULL`` until the publisher can drain it. Operators see
  ``workflow_outbox.published_at IS NULL`` as the system of record
  for "events that need re-publishing".

### Out of scope (deliberate)

- A full rewrite of the proposal-to-DAG path (P1-2 from the GPT
  review). That is a separate change. This change only covers
  ``review_task`` settlement, the highest-blast-radius silent-loss
  path.
- The ``handler postcondition`` validator (P1-4) and the
  ``owner_response`` invalid-action fix (P2-1) — also separate.
- The ``.NET 10 Proposal Worker`` side. The outbox is purely a
  publisher; consumers (Worker / agent handlers) are unchanged.

## Design principles

1. **DB is the source of truth; MQ is a hint.** Already true today
   (``rabbitmq.py:13``); outbox makes the durability of "the hint we
   meant to send" match the durability of the state that triggered
   it.
2. **Idempotent consumers.** Existing consumers already
   re-read state from the DB on every message
   (``rabbitmq.py:14-22``). At-least-once delivery from the outbox
   is therefore safe.
3. **No new event types.** The outbox row carries the same
   ``(event, entity_type, entity_id, ref_id, agent_id)`` tuple that
   the existing ``publish_workflow_event`` accepts. The publisher is
   a thin adapter.
4. **Bounded blast radius.** Only the ``review_task`` path is
   migrated in this change. The remaining ~17 ``publish_workflow_event``
   call sites are left alone until a follow-up change generalizes
   the pattern.

## Wire-up sketch

```python
# work_items/router.py:review_task
with s.begin():                            # explicit transaction
    before = s.get(Task, tid)
    t = service.review_task(               # commits inside
        s, task_id=tid, ...)
    outbox.add(s, EVENT_TASK_REVIEWED, ...) # same transaction

# background loop (or BackgroundTasks)
while not stopping:
    for row in outbox.fetch_unpublished(s, limit=100):
        try:
            publish_workflow_event(...)
            for succ in successors: outbox.add(s, EVENT_TASK_AVAILABLE, ...)
            if story_done: outbox.add(s, EVENT_STORY_DONE, ...)
            outbox.mark_published(s, row.id)
        except MQError as e:                # transient
            outbox.mark_retry(s, row.id, error=str(e))
        except Exception as e:              # poison
            outbox.mark_dead(s, row.id, error=str(e))
    sleep(0.5)
```

## Acceptance

1. ``POST /api/tasks/{tid}/review`` (approve) writes one
   ``workflow_outbox`` row in the same DB commit as the status
   change. The row is visible to the publisher before the HTTP
   response is sent.
2. With MQ broken (test injects a publisher that always raises), the
   HTTP response is still 200/201, the row stays ``published_at IS
   NULL``, and re-pointing MQ later drains it without data loss.
3. With the publisher disabled, the existing
   ``test_run_authorization.py`` and
   ``test_run_read_authorization.py`` merge-run passes 18/18 (the
   single ``test_last_event_id_replays_only_newer_events`` flakiness
   noted in P0-fix is fixed as a side benefit, since the outbox is
   drained deterministically rather than racing the broker).
4. New e2e ``tests/test_workflow_outbox.py::test_review_durable_when_mq_down``
   pins the contract end-to-end.
5. The existing ``core/infrastructure/messaging/rabbitmq.py`` API
   (``publish_workflow_event``) is **not** removed. The outbox
   publisher calls into it; production code that still uses
   ``publish_workflow_event`` directly keeps working. Removal is
   out of scope.

## Migration

- New table ``workflow_outbox``: ``id, event, entity_type, entity_id,
  ref_id, agent_id, payload_json, created_at, published_at,
  attempts, last_error``. See Alembic migration
  ``workflow_outbox_2026_08_28``.
- New module ``core/infrastructure/outbox.py``:
  ``WorkflowOutbox`` model, ``OutboxRepository`` (add /
  fetch_unpublished / mark_published / mark_retry / mark_dead),
  ``OutboxPublisher`` background service.
- New module ``core/infrastructure/messaging/outbox_publisher.py``:
  thin adapter that drains outbox rows by calling the existing
  ``publish_workflow_event``.
- Modified: ``features/work_items/router.py::review_task`` — the
  three best-effort publish + the ``create_comment`` call are folded
  into a single ``outbox.add(...)`` block in a new ``with
  s.begin():`` transaction.

## Open questions

- Should the publisher be a FastAPI startup background task (simpler,
  lives with the API) or a standalone worker process (more
  isolation, scales independently)? The current spec is **startup
  background task**. It can be promoted to standalone later without
  changing the schema or the ``outbox.add`` API.
- Should the outbox include a ``payload_json`` column for
  forward-compatible events that the current
  ``publish_workflow_event`` does not accept? Yes — keeping
  ``payload_json`` makes it cheap to add a new field without a
  schema migration per event type.
