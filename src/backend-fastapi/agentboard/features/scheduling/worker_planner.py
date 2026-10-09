"""Server-owned workflow planner for worker-owned execution.

Since the ``serverplan`` protocol the **Server** — not the Worker — decides the
next unit of work for every mapped project, materialises the ``worker_work``
row and lets the relay publish it. Workers are pure executors: they consume the
queues they subscribed to, execute, and report the result back.

Two invariants are preserved from the Worker-planned era:

* **No Agent selection.** The planner decides *what* work exists, never *who*
  runs it. Competing consumers pick a message off the shared queue.
* **Business state is the single source of truth.** Every offer is derived from
  Task/Proposal state and re-validated by ``check_offer`` inside
  :func:`materialize_offer`, so a stale plan can never mutate a moved item.

Capability *coverage* only decides whether a row is worth creating at all
(otherwise rows pile up for kinds no online Agent can serve). When coverage is
unknown the planner fails open and relies on the queue.
"""
from __future__ import annotations

import logging
import os
import time

from fastapi import HTTPException
from sqlalchemy import func

from ...core.infrastructure import database
from ..projects.models import Agent, AgentInstance, Story, WorkerProjectMapping
from ..proposals.models import Proposal
from ..work_items.models import Task
from ..work_items.service import get_task_readiness
from . import worker_discussions as discussions
from .matching import normalize_capabilities
from .worker_work import WORK_KINDS, Offer, enabled, materialize_offer

log = logging.getLogger("agentboard.features.scheduling.worker_planner")

# Planning is a full business-state scan, so it is throttled independently of
# the outbox tick (which is sub-second). Mirrors the Worker-era reconcile
# cadence (``WorkerOwned:ReconcileSeconds`` = 5).
DEFAULT_PLAN_INTERVAL_SECONDS = 5.0
PLAN_INTERVAL_ENV = "AGENTBOARD_WORKER_PLAN_SECONDS"

# task.type -> (execution kind, review kind). ``bug`` is implemented by the
# dev-capable Agents, exactly like the Worker-era ``WorkerWorkKinds.ForTask``.
_TASK_KINDS: dict[str, tuple[str, str]] = {
    "design": ("design", "design_review"),
    "dev": ("dev", "dev_review"),
    "bug": ("dev", "dev_review"),
    "qa": ("qa", "qa_review"),
}

_last_plan_at = 0.0


def plan_interval_seconds() -> float:
    try:
        value = float(os.getenv(PLAN_INTERVAL_ENV, ""))
    except (TypeError, ValueError):
        return DEFAULT_PLAN_INTERVAL_SECONDS
    return value if value > 0 else DEFAULT_PLAN_INTERVAL_SECONDS


def mapped_projects(s) -> list[int]:
    """Projects at least one Worker declared itself responsible for."""
    rows = (
        s.query(WorkerProjectMapping.project_id)
        .filter(WorkerProjectMapping.enabled.is_(True))
        .distinct()
        .all()
    )
    return [row[0] for row in rows]


def covered_kinds(s, project_id: int) -> set[str] | None:
    """Kinds an **online** AgentInstance of a mapped Worker advertises.

    ``None`` means "unknown" (no reachable instance, or unreadable capability
    data) — callers must fail open rather than silently stalling work.
    """
    workers = [
        row[0]
        for row in s.query(WorkerProjectMapping.worker_id)
        .filter(
            WorkerProjectMapping.project_id == project_id,
            WorkerProjectMapping.enabled.is_(True),
        )
        .all()
    ]
    if not workers:
        return None
    agent_ids = [
        row[0]
        for row in s.query(AgentInstance.agent_id)
        .filter(
            AgentInstance.worker_id.in_(workers),
            AgentInstance.enabled.is_(True),
            AgentInstance.online.is_(True),
        )
        .all()
    ]
    if not agent_ids:
        # A mapped Worker exists but nothing is reachable right now. An empty
        # set is the honest answer: creating rows now only grows the queue.
        return set()
    declared: set[str] = set()
    # ``AgentInstance.agent_id`` is the logical string id (``Agent.agent_id``),
    # not the integer primary key — mixing them silently yields no rows.
    for (raw,) in s.query(Agent.capabilities).filter(Agent.agent_id.in_(agent_ids)).all():
        try:
            declared |= {entry["name"] for entry in normalize_capabilities(raw)}
        except Exception:  # malformed capability JSON must never stop planning
            return None
    return declared & set(WORK_KINDS)


def is_discussion(s, task) -> "object | None":
    return discussions.active(s, task.id)


def offer_for(s, project_id: int, entity_type: str, item) -> Offer | None:
    """Raw business state -> the next unit of work (port of WorkPlanner.Next)."""
    if entity_type == "proposal":
        if item.status not in ("queued", "answered"):
            return None
        return Offer(
            project_id=project_id,
            entity_type="proposal",
            entity_id=item.id,
            kind="proposal",
            iteration=item.current_round or 0,
        )

    kinds = _TASK_KINDS.get(item.type)
    if kinds is None or item.status not in ("todo", "in_review"):
        return None
    story = s.get(Story, item.story_id)
    if story is None or story.status in ("backlog", "blocked", "done"):
        return None
    if item.needs_human_confirmation or not get_task_readiness(s, item)["ready"]:
        return None
    if item.status == "todo" and item.current_assignment_id is not None:
        return None

    discussion = discussions.active(s, item.id)
    if discussion is not None:
        if item.status != "in_review" or discussion.status != "open":
            return None
        # Turn parity mirrors discussions.validate_turn: odd turns belong to the
        # reviewer, even turns hand the floor back to the original author.
        reviewer_turn = discussion.turn % 2 == 1
        kind = (
            discussion.review_kind
            if reviewer_turn
            else discussion.review_kind.removesuffix("_review")
        )
        target = discussion.reviewer_agent if reviewer_turn else discussion.owner_agent
        return Offer(
            project_id=project_id,
            entity_type="task",
            entity_id=item.id,
            kind=kind,
            iteration=discussion.turn,
            discussion_id=discussion.id,
            target_agent=target,
        )

    return Offer(
        project_id=project_id,
        entity_type="task",
        entity_id=item.id,
        kind=kinds[1] if item.status == "in_review" else kinds[0],
        iteration=item.review_round or 0,
    )


def plan_project(s, project_id: int) -> int:
    covered = covered_kinds(s, project_id)
    created = 0
    for entity_type, model in (("proposal", Proposal), ("task", Task)):
        for item in s.query(model).filter(model.project_id == project_id).all():
            offer = offer_for(s, project_id, entity_type, item)
            if offer is None:
                continue
            if covered is not None and offer.kind not in covered:
                # Not a failure: no online Agent advertises this kind yet. The
                # row is created on a later pass as soon as one appears.
                log.info(
                    "Worker plan: %s #%s kind=%s skipped — no online Agent advertises it",
                    entity_type, item.id, offer.kind,
                )
                continue
            try:
                _, created_row = materialize_offer(s, offer)
            except HTTPException as error:
                # State moved (or is not yet offerable); the next pass re-derives
                # everything from business state, so skipping is the right answer.
                log.debug(
                    "Worker plan: %s #%s not offerable (%s)",
                    entity_type, item.id, error.detail,
                )
                continue
            if created_row:
                created += 1
    return created


def complete_finished_stories(s, project_id: int) -> int:
    """Close stories whose Tasks are all done — the Server owns story closure."""
    story_ids = [
        row[0]
        for row in s.query(Task.story_id)
        .filter(Task.project_id == project_id, Task.story_id.isnot(None))
        .group_by(Task.story_id)
        .having(func.count(Task.id) > 0)
        .all()
    ]
    from .service import complete_story

    closed = 0
    for story_id in story_ids:
        if s.query(Task.id).filter(Task.story_id == story_id, Task.status != "done").first():
            continue
        story = s.get(Story, story_id)
        if story is None or story.status in ("done", "blocked", "backlog"):
            continue
        try:
            complete_story(
                s, story_id,
                changed_by=story.owner_user_id,
                reason="Worker verified all tasks and reviews",
            )
        except Exception as error:  # guards inside complete_story are authoritative
            log.debug("Worker plan: story #%s not closable (%s)", story_id, error)
            continue
        closed += 1
    return closed


def plan_once(force: bool = False) -> int:
    """One throttled planning pass over every mapped project."""
    global _last_plan_at
    if not enabled():
        return 0
    now = time.monotonic()
    if not force and now - _last_plan_at < plan_interval_seconds():
        return 0
    _last_plan_at = now
    created = 0
    with database.SessionLocal() as s:
        try:
            for project_id in mapped_projects(s):
                created += plan_project(s, project_id)
                created += complete_finished_stories(s, project_id)
            s.commit()
        except Exception:
            s.rollback()
            raise
    if created:
        log.info("Worker plan: materialised %d work row(s)", created)
    return created


__all__ = [
    "plan_once",
    "plan_project",
    "offer_for",
    "covered_kinds",
    "mapped_projects",
    "complete_finished_stories",
    "plan_interval_seconds",
    "DEFAULT_PLAN_INTERVAL_SECONDS",
]
