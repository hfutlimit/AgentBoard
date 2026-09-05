"""Worker-owned execution: Server validates commands and relays durable offers.

There is deliberately no agent selector or workflow scheduler here. Workers
read business state, offer the next work, and choose their own local Agent.
Lease/result writes and business mutations share the request transaction.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from ... import api_helpers
from ...core.common.models import utc_now
from ...core.infrastructure.database import get_session
from ...core.service_helpers import _ser
from ..projects.models import Agent, Epic, Story
from ..projects.service import user_is_project_member
from ..proposals.models import Proposal, ProposalQuestion
from ..work_items.models import Task, TaskDependency
from ..work_items.service import get_task_readiness
from .worker_work_models import WorkerWork, WorkerDiscussion
from . import worker_discussions as discussions

WorkKind = Literal["proposal", "design", "design_review", "dev", "dev_review", "qa", "qa_review"]
WORK_KINDS = ("proposal", "design", "design_review", "dev", "dev_review", "qa", "qa_review")
EXCHANGE = "agentboard.work.v2"


def enabled() -> bool:
    value = os.getenv("AGENTBOARD_WORKER_OWNED_ENABLED", "0")
    if value not in ("0", "1"):
        raise ValueError("AGENTBOARD_WORKER_OWNED_ENABLED must be 0 or 1")
    return value == "1"


def queue_name(project_id: int, kind: str, target_agent: str | None = None) -> str:
    if project_id <= 0 or kind not in WORK_KINDS:
        raise ValueError("explicit project and supported work kind required")
    queue = f"{EXCHANGE}.project.{project_id}.{kind}"
    return queue + ".agent." + hashlib.sha256(target_agent.encode()).hexdigest() if target_agent else queue


class Offer(BaseModel):
    project_id: int = Field(gt=0)
    entity_type: Literal["proposal", "task"]
    entity_id: int = Field(gt=0)
    kind: WorkKind
    iteration: int = Field(ge=0)
    discussion_id: int | None = Field(default=None, gt=0)
    target_agent: str | None = Field(default=None, min_length=1, max_length=100)


class Claim(BaseModel):
    project_id: int = Field(gt=0)
    kind: WorkKind
    worker_id: str = Field(min_length=1, max_length=100)
    agent_id: str = Field(min_length=1, max_length=100)
    # The token is generated and persisted by the Worker before requesting a
    # claim. A lost HTTP reply can therefore be retried without another run.
    token: str = Field(min_length=32, max_length=64)


class Completion(Claim):
    result: dict


router = APIRouter(prefix="/api/worker-work", tags=["worker-work"])


def authorize(s: Session, project_id: int, authorization: str | None, permission="api:write"):
    actor = api_helpers.resolve_actor_context(authorization, s, required_permission=permission)
    if not enabled():
        raise HTTPException(409, "worker-owned execution is disabled")
    if not (actor.is_admin or user_is_project_member(s, project_id, actor.user_id)):
        raise HTTPException(403, "project membership required")
    # Reused business services still enforce ownership/exclusions. Only this
    # authenticated, fenced command boundary may bypass legacy-dispatch gates.
    s.info["worker_owned_command"] = True
    s.info["auto_commit"] = False
    return actor


def entity(s, entity_type, entity_id):
    model = Proposal if entity_type == "proposal" else Task
    obj = s.query(model).filter(model.id == entity_id).with_for_update().populate_existing().first()
    if obj is None:
        raise HTTPException(404, "work item not found")
    return obj


def expected(obj):
    if isinstance(obj, Proposal):
        if obj.status not in ("queued", "answered"):
            return None
        return "proposal", obj.current_round or 0
    if obj.type not in ("design", "dev", "bug", "qa"):
        return None
    kind = "dev" if obj.type == "bug" else obj.type
    if obj.status == "in_review":
        return kind + "_review", obj.review_round or 0
    if obj.status == "todo":
        return kind, obj.review_round or 0
    return None


def check_offer(s, body: Offer):
    obj = entity(s, body.entity_type, body.entity_id)
    if obj.project_id != body.project_id:
        raise HTTPException(409, "work no longer matches business state")
    if body.discussion_id is not None:
        if not isinstance(obj, Task):
            raise HTTPException(409, "discussion requires a Task")
        discussions.validate_turn(s, obj, body)
    elif (body.target_agent is not None or expected(obj) != (body.kind, body.iteration)
          or isinstance(obj, Task) and discussions.active(s, obj.id)):
        raise HTTPException(409, "work no longer matches business state")
    if isinstance(obj, Task):
        story = s.get(Story, obj.story_id)
        if (not story or story.status in ("backlog", "blocked", "done")
                or obj.needs_human_confirmation or not get_task_readiness(s, obj)["ready"]):
            raise HTTPException(409, "dependencies, Story or human gate prevent this work")
        if obj.status == "todo" and obj.current_assignment_id is not None:
            raise HTTPException(409, "existing assignment must be drained first")
    return obj


def context(s, obj, work):
    retry = {"previous_attempts": json.loads(work.attempt_history)}
    if isinstance(obj, Proposal):
        from ..proposals.service import list_proposal_rounds
        return {**retry, "item": _ser(obj), "history": list_proposal_rounds(s, obj.id)}
    from .service import _upstream_task_ids
    ids = _upstream_task_ids(s, obj.id) | {obj.id}
    evidence = s.query(WorkerWork).filter(WorkerWork.entity_type == "task",
        WorkerWork.entity_id.in_(ids), WorkerWork.state == "completed",
        WorkerWork.discussion_id.is_(None)).order_by(WorkerWork.id).all()
    story = s.get(Story, obj.story_id)
    from ..work_items.models import Comment
    comments = s.query(Comment).filter(or_(Comment.task_id == obj.id,
        Comment.story_id == obj.story_id)).order_by(Comment.id.desc()).limit(50).all()
    discussion = s.get(WorkerDiscussion, work.discussion_id) if work.discussion_id else None
    return {**retry, "item": _ser(obj), "story": _ser(story),
        "discussion": discussions.view(discussion) if discussion else None,
        "comments": [_ser(c) for c in reversed(comments)], "evidence": [
        {"work_id": w.id, "task_id": w.entity_id, "kind": w.kind,
         "agent_id": w.agent_id, "result": json.loads(w.result or "{}")} for w in evidence]}


def fingerprint(s, obj):
    # State/assignment changes caused by our own claim are intentionally not
    # included. A human changing the requested work invalidates a late result.
    fields = (obj.title, getattr(obj, "content", ""), getattr(obj, "spec", ""),
              getattr(obj, "description", ""), getattr(obj, "owner_user_id", None),
              getattr(obj, "author_id", None), getattr(obj, "type", None),
              getattr(obj, "story_id", None), getattr(obj, "review_round", None),
              getattr(obj, "current_round", None))
    if isinstance(obj, Task):
        dependencies = s.query(TaskDependency).filter_by(task_id=obj.id).order_by(TaskDependency.depends_on_id).all()
        fields += ([(edge.depends_on_id, edge.dependency_type) for edge in dependencies],)
    else:
        questions = s.query(ProposalQuestion).filter_by(proposal_id=obj.id).order_by(ProposalQuestion.id).all()
        fields += ([(q.id, q.question, q.answer, q.unsure) for q in questions],)
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def materialize_worker_plan(s, proposal, result):
    """Persist a Worker-supplied DAG; do not infer stages from spec on Server."""
    from ..proposals import service as proposals
    from ..proposals.conversion_service import ConversionPlan, ProposalConversionService
    raw = result.get("ticket_plan")
    if not isinstance(raw, dict):
        raise HTTPException(422, "Worker must supply ticket_plan")
    tasks, dependencies = raw.get("tasks"), raw.get("dependencies")
    if (not isinstance(tasks, list) or not 3 <= len(tasks) <= 100
            or not isinstance(dependencies, list) or len(dependencies) > 1000
            or any(not isinstance(t, dict) or t.get("type") not in ("design", "dev", "qa")
                   or not isinstance(t.get("title"), str) or not 1 <= len(t["title"].strip()) <= 300
                   or not isinstance(t.get("description", ""), str) for t in tasks)):
        raise HTTPException(422, "invalid Worker task plan")
    titles = {t["title"] for t in tasks}
    if len(titles) != len(tasks) or {t["type"] for t in tasks} != {"design", "dev", "qa"}:
        raise HTTPException(422, "plan needs unique tasks and independent design/dev/QA")
    if any(not isinstance(edge, list) or len(edge) != 2 or any(not isinstance(x, str) or x not in titles for x in edge) for edge in dependencies):
        raise HTTPException(422, "invalid plan dependency")
    parents = {title: set() for title in titles}
    for source, target in dependencies:
        parents[target].add(source)
    closure_cache = {}
    def ancestors(title, visiting):
        if title in visiting:
            raise HTTPException(422, "plan must be acyclic")
        if title not in closure_cache:
            closure_cache[title] = parents[title] | set().union(*(ancestors(p, visiting | {title}) for p in parents[title]))
        return closure_cache[title]
    closures = {title: ancestors(title, set()) for title in titles}
    design = {t["title"] for t in tasks if t["type"] == "design"}
    development = {t["title"] for t in tasks if t["type"] == "dev"}
    if any(not design <= closures[d] for d in development) or any(
            not development <= closures[t["title"]] for t in tasks if t["type"] == "qa"):
        raise HTTPException(422, "design and development must gate downstream work")
    proposals._validate_ticket_parents(s, proposal, type="story", epic_id=proposal.target_epic_id, story_id=None)
    plan = ConversionPlan(epic_id=proposal.target_epic_id,
        story={"title": proposal.title, "description": proposal.converged_spec},
        tasks=tasks, dependencies=[tuple(edge) for edge in dependencies], create_qa=False)
    ProposalConversionService.validate(plan, project_id=proposal.project_id)
    request = proposals.create_ticket_request(s, proposal.id, type="auto_story")
    applied = ProposalConversionService.apply(s, plan, proposal, commit=False)
    request.status, request.ticket_id, request.resolved_type = "done", applied.story_id, "story"
    proposal.ticket_type, proposal.ticket_id = "story", applied.story_id
    if result.get("activate_story") is True:
        from ..projects.service import confirm_story
        confirm_story(s, applied.story_id, changed_by=proposal.author_id)


def qa_defects(result):
    defects = result.get("defects", [])
    if (not isinstance(defects, list) or len(defects) > 50
            or any(not isinstance(d, dict) or set(d) != {"title", "description"}
                   or not isinstance(d["title"], str) or not 1 <= len(d["title"].strip()) <= 300
                   or not isinstance(d["description"], str) or not d["description"].strip()
                   for d in defects)):
        raise HTTPException(422, "QA defects require bounded title and reproducible description")
    if result.get("tests_passed") is False and not defects:
        raise HTTPException(422, "failed QA requires actionable defects (including test/deployment blockers)")
    if result.get("tests_passed") is True and defects:
        raise HTTPException(422, "QA cannot pass with unresolved defects")
    return defects


def materialize_qa_followup(s, qa, execution, result, agent):
    """Validate/persist the Worker's explicit bug + retest command atomically.

    Original QA evidence remains immutable. No central Agent selection, offers,
    or planning: local Workers offer these new business items on later scans.
    """
    from ..work_items.service import create_task, create_comment
    evidence = json.loads(execution.result or "{}")
    defects = qa_defects(evidence)
    plan = result.get("qa_followup")
    if (not isinstance(plan, dict) or set(plan) != {"source_work_id", "bugs", "retest"}
            or type(plan["source_work_id"]) is not int or plan["source_work_id"] != execution.id
            or plan["bugs"] != defects):
        raise HTTPException(422, "Worker must supply qa_followup for every accepted QA defect")
    retest = plan["retest"]
    if (not isinstance(retest, dict) or set(retest) != {"title", "description"}
            or not isinstance(retest["title"], str) or not 1 <= len(retest["title"].strip()) <= 300
            or not isinstance(retest["description"], str) or not retest["description"].strip()):
        raise HTTPException(422, "Worker must supply an independent retest Task")
    created = []
    for spec in [*defects, retest]:
        task = create_task(s, project_id=qa.project_id, story_id=qa.story_id,
            title=spec["title"], description=spec["description"],
            type="qa" if len(created) == len(defects) else "bug",
            owner_user_id=qa.owner_user_id, created_by_user_id=agent.user_id,
            created_by_agent_id=agent.id, needs_human_confirmation=False,
            labels=json.dumps([f"qa-source-task:{qa.id}", f"qa-source-work:{execution.id}"]), commit=False)
        parents = [item.id for item in created] if task.type == "qa" else [qa.id]
        for parent in parents:
            s.add(TaskDependency(task_id=task.id, depends_on_id=parent, dependency_type="blocks"))
        created.append(task)
    create_comment(s, author=agent.agent_id, task_id=qa.id, content=json.dumps({
        "source_work_id": execution.id, "bug_task_ids": [t.id for t in created[:-1]],
        "retest_task_id": created[-1].id}, ensure_ascii=False))


@router.get("/snapshot")
def snapshot(project_id: int, entity_type: Literal["proposal", "task"],
             after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
             authorization: str | None = Header(None), s: Session = Depends(get_session)):
    authorize(s, project_id, authorization, "api:read")
    model = Proposal if entity_type == "proposal" else Task
    rows = s.query(model).filter(model.project_id == project_id, model.id > after_id).order_by(model.id).limit(limit).all()
    # Raw business state, not a Server-chosen execution plan. Worker computes
    # readiness and offers each next work explicitly (offer revalidates it).
    items = []
    for obj in rows:
        item = _ser(obj)
        if isinstance(obj, Task):
            item["ready"] = get_task_readiness(s, obj)["ready"]
            story = s.get(Story, obj.story_id)
            item["story_status"] = story.status if story else "missing"
            discussion = discussions.active(s, obj.id)
            item["discussion"] = discussions.view(discussion) if discussion else None
        items.append(item)
    return {"protocol": "worker-work.discussions.v1", "items": items,
            "next_after_id": rows[-1].id if len(rows) == limit else 0}


@router.post("/offers")
def offer(body: Offer, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    authorize(s, body.project_id, authorization)
    obj = check_offer(s, body)
    key = f"{body.entity_type}:{body.entity_id}:{body.kind}:{body.iteration}:{fingerprint(s, obj)}"
    if body.discussion_id:
        key += f":discussion:{body.discussion_id}"
    elif body.entity_type == "task" and body.kind.endswith("_review"):
        previous = s.query(WorkerDiscussion).filter_by(task_id=obj.id,
            review_round=obj.review_round or 0).order_by(WorkerDiscussion.id.desc()).first()
        if previous:
            key += f":after:{previous.id}"
    existing = s.query(WorkerWork).filter_by(work_key=key).first()
    if existing:
        return {"id": existing.id, "state": existing.state}
    from sqlalchemy.exc import IntegrityError
    row = WorkerWork(work_key=key, project_id=body.project_id, entity_type=body.entity_type,
        entity_id=body.entity_id, kind=body.kind, iteration=body.iteration, input_hash=fingerprint(s, obj),
        discussion_id=body.discussion_id, target_agent=body.target_agent)
    try:
        with s.begin_nested():
            s.add(row)
            s.flush()
    except IntegrityError:
        row = s.query(WorkerWork).filter_by(work_key=key).one()
    return {"id": row.id, "state": row.state}


def resolve_claim(s, work_id, body, authorization):
    row = s.get(WorkerWork, work_id)
    if not row:
        raise HTTPException(404, "work not found")
    actor = authorize(s, row.project_id, authorization)
    if row.project_id != body.project_id or row.kind != body.kind:
        raise HTTPException(409, "message does not match work scope")
    agent = s.query(Agent).filter_by(agent_id=body.agent_id).first()
    if row.target_agent is not None and row.target_agent != body.agent_id:
        raise HTTPException(403, "discussion reply belongs to its original participant")
    if (not agent or agent.user_id != actor.user_id
            or (actor.agent_registry_id is not None and actor.agent_registry_id != agent.id)):
        raise HTTPException(403, "Agent must belong to the authenticated caller")
    return row, agent


def fenced(s, work_id, body, authorization):
    row, agent = resolve_claim(s, work_id, body, authorization)
    changed = s.execute(update(WorkerWork).where(WorkerWork.id == work_id,
        WorkerWork.state == "leased", WorkerWork.agent_id == agent.id,
        WorkerWork.worker_id == body.worker_id, WorkerWork.lease_token == body.token,
        WorkerWork.lease_until > utc_now()).values(lease_until=utc_now() + timedelta(minutes=3)))
    if changed.rowcount != 1:
        raise HTTPException(409, "lease expired or belongs to another attempt")
    s.refresh(row)
    return row, agent


@router.post("/{work_id}/claim")
def claim(work_id: int, body: Claim, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    row, agent = resolve_claim(s, work_id, body, authorization)
    if row.state == "completed":
        return {"state": "completed"}
    if row.state == "failed":
        raise HTTPException(409, "work failed; manual reconciliation required")
    # A crashed third attempt must become terminal, not an endless RabbitMQ
    # redelivery that can never acquire another lease. Expiry is transport
    # cleanup, not a Server decision about the next business stage.
    if row.state == "leased" and row.lease_until <= utc_now() and row.attempts >= 3:
        obj = entity(s, row.entity_type, row.entity_id)
        changed = s.execute(update(WorkerWork).where(WorkerWork.id == work_id,
            WorkerWork.state == "leased", WorkerWork.lease_until <= utc_now(),
            WorkerWork.attempts >= 3).values(state="failed", active_slot=None,
                result=json.dumps({"summary": "Three execution attempts exhausted after lease expiry"})))
        if changed.rowcount != 1:
            raise HTTPException(409, "lease changed during expiry reconciliation")
        if row.input_hash == fingerprint(s, obj):
            block_failed_execution(s, obj, "Three execution attempts exhausted after lease expiry")
        return {"state": "failed"}
    if row.state == "leased" and row.lease_token == body.token and row.lease_until <= utc_now():
        raise HTTPException(409, "new_token_required after lease expiry")
    if row.state == "leased" and row.lease_token == body.token and row.lease_until > utc_now():
        row, agent = fenced(s, work_id, body, authorization)
        return {"state": "leased", "work": _ser(row), "context": context(s, entity(s, row.entity_type, row.entity_id), row)}
    recovering = row.state == "leased" and row.lease_until <= utc_now()
    if recovering:
        obj = entity(s, row.entity_type, row.entity_id)
        valid_state = "in_review" if row.discussion_id or row.kind.endswith("_review") else ("analyzing" if isinstance(obj, Proposal) else "in_progress")
        if obj.status != valid_state or row.input_hash != fingerprint(s, obj):
            changed = s.execute(update(WorkerWork).where(WorkerWork.id == work_id,
                WorkerWork.state == "leased", WorkerWork.lease_until <= utc_now()).values(
                    state="failed", active_slot=None,
                    result=json.dumps({"summary": "Expired work changed; manual reconciliation required"})))
            if changed.rowcount != 1:
                raise HTTPException(409, "lease changed during reconciliation")
            return {"state": "failed"}  # Never overwrite the changed business item.
    else:
        obj = check_offer(s, Offer(project_id=row.project_id, entity_type=row.entity_type,
            entity_id=row.entity_id, kind=row.kind, iteration=row.iteration,
            discussion_id=row.discussion_id, target_agent=row.target_agent))
    if row.discussion_id:
        discussions.validate_turn(s, obj, row)
    if row.input_hash != fingerprint(s, obj):
        raise HTTPException(409, "offered work is stale")
    owner = obj.author_id if isinstance(obj, Proposal) else obj.owner_user_id
    if owner is None or owner != agent.user_id:
        raise HTTPException(403, "work owner does not match Agent owner")
    if isinstance(obj, Task) and not row.discussion_id:
        from .service import get_assignment_exclusion
        exclusion = get_assignment_exclusion(s, obj, "review" if row.kind.endswith("_review") else "task")
        if agent.id in exclusion.agent_registry_ids:
            raise HTTPException(403, "independent reviewer/QA Agent required")
    previous_attempt = {"attempt": row.attempts, "agent_id": row.agent_id, "worker_id": row.worker_id,
                        "result": json.loads(row.result) if row.result else None, "expired": recovering}
    history = json.loads(row.attempt_history)
    if row.attempts:
        history.append(previous_attempt)
    from sqlalchemy.exc import IntegrityError
    try:
        changed = s.execute(update(WorkerWork).where(WorkerWork.id == work_id,
        or_(WorkerWork.state == "available", (WorkerWork.state == "leased") & (WorkerWork.lease_until <= utc_now())),
        WorkerWork.attempts < 3).values(state="leased", active_slot="active", result=None,
        attempt_history=json.dumps(history, ensure_ascii=False),
        worker_id=body.worker_id, agent_id=agent.id, lease_token=body.token,
        lease_until=utc_now() + timedelta(minutes=3), attempts=WorkerWork.attempts + 1))
    except IntegrityError:
        s.rollback()
        raise HTTPException(409, "work item already has an active attempt") from None
    if changed.rowcount != 1:
        raise HTTPException(409, "work already claimed or attempts exhausted")
    if recovering and not row.discussion_id and not row.kind.endswith("_review"):
        reset_execution(s, obj)
    if row.discussion_id:
        pass  # Discussion must preserve the execution/review assignment and status.
    elif isinstance(obj, Proposal):
        from ..proposals.service import claim_proposal
        if claim_proposal(s, obj.id, agent=body.worker_id, user_id=agent.user_id) is None:
            raise HTTPException(409, "proposal claim lost")
    elif row.kind.endswith("_review"):
        obj.reviewer_id, obj.reviewer_agent_id = agent.user_id, agent.id
    else:
        from ..work_items.service import try_assign_task
        try_assign_task(s, obj.id, user_id=agent.user_id, agent_registry_id=agent.id,
                        source="worker", workload_type="task", commit=False)
    s.flush()
    s.refresh(row)
    return {"state": "leased", "work": _ser(row), "context": context(s, obj, row)}


def reset_execution(s, obj):
    if isinstance(obj, Proposal):
        from ..proposals.service import set_proposal_status
        set_proposal_status(s, obj.id, "queued")
    else:
        from .models import TaskAssignment
        from ..work_items.service import set_status
        old = s.get(TaskAssignment, obj.current_assignment_id) if obj.current_assignment_id else None
        if old:
            old.status, old.active_slot = "completed", None
            old.completed_at = utc_now()
        obj.current_assignment_id = None
        set_status(s, obj.id, "todo", reason="Worker retry after fenced attempt")


def block_failed_execution(s, obj, summary):
    if isinstance(obj, Proposal):
        from ..proposals.service import set_proposal_status
        if obj.status == "analyzing":
            set_proposal_status(s, obj.id, "failed", error=summary)
    elif obj.status in ("in_progress", "in_review"):
        from ..work_items.service import set_status
        set_status(s, obj.id, "blocked", reason=summary, status_reason="pending_requirement_change")
        discussion = discussions.active(s, obj.id)
        if discussion:
            from ..work_items.service import create_comment
            discussion.status, discussion.active_slot = "escalated", None
            create_comment(s, story_id=obj.story_id, author="worker",
                content=f"Task #{obj.id}, discussion #{discussion.id}: execution retries exhausted; human reconciliation required.\n\n{summary}")


@router.post("/{work_id}/fail")
def fail(work_id: int, body: Completion, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    row, agent = fenced(s, work_id, body, authorization)
    obj = entity(s, row.entity_type, row.entity_id)
    summary = body.result.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 4000:
        raise HTTPException(422, "bounded failure summary required")
    # Retain failure evidence rather than quietly succeeding on CLI exit 0.
    failure_json = json.dumps(body.result, ensure_ascii=False, sort_keys=True)
    if len(failure_json) > 100_000:
        raise HTTPException(422, "failure evidence too large")
    row.result = failure_json
    row.active_slot = None
    valid_state = "in_review" if row.discussion_id or row.kind.endswith("_review") else ("analyzing" if isinstance(obj, Proposal) else "in_progress")
    if row.input_hash != fingerprint(s, obj) or obj.status != valid_state:
        row.state = "failed"
        return {"state": "failed"}  # Retain the failure, do not undo human changes.
    if row.attempts >= 3:
        row.state = "failed"
        block_failed_execution(s, obj, summary)
    else:
        if not row.discussion_id and not row.kind.endswith("_review"):
            reset_execution(s, obj)
        row.state, row.published_at = "available", None
    return {"state": row.state}


@router.get("/discussions")
def list_discussions(project_id: int = Query(gt=0), task_id: int | None = Query(None, gt=0),
        story_id: int | None = Query(None, gt=0), authorization: str | None = Header(None),
        s: Session = Depends(get_session)):
    authorize(s, project_id, authorization, "api:read")
    if task_id is None and story_id is None:
        raise HTTPException(422, "Task or Story scope required")
    query = s.query(WorkerDiscussion).join(Task, Task.id == WorkerDiscussion.task_id).filter(
        WorkerDiscussion.project_id == project_id)
    if task_id is not None:
        query = query.filter(WorkerDiscussion.task_id == task_id)
    if story_id is not None:
        query = query.filter(Task.story_id == story_id)
    return {"items": [discussions.view(d) for d in query.order_by(WorkerDiscussion.id.desc()).limit(100)]}


@router.get("/{work_id}")
def work_status(work_id: int, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    row = s.get(WorkerWork, work_id)
    if not row:
        raise HTTPException(404, "work not found")
    authorize(s, row.project_id, authorization, "api:read")
    # Lease capability is never exposed by a read endpoint.
    return {"id": row.id, "state": row.state, "project_id": row.project_id,
            "kind": row.kind, "attempts": row.attempts, "result": json.loads(row.result or "{}"),
            "attempt_history": json.loads(row.attempt_history)}


@router.post("/{work_id}/heartbeat")
def heartbeat(work_id: int, body: Claim, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    row, _ = fenced(s, work_id, body, authorization)
    return {"state": row.state}


@router.post("/{work_id}/complete")
def complete(work_id: int, body: Completion, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    row, agent = resolve_claim(s, work_id, body, authorization)
    result_json = json.dumps(body.result, ensure_ascii=False, sort_keys=True)
    if len(result_json) > 100_000:
        raise HTTPException(422, "result too large; use evidence references")
    if row.state == "completed" and row.lease_token == body.token and row.agent_id == agent.id and row.worker_id == body.worker_id:
        if row.result != result_json:
            raise HTTPException(409, "completed result is immutable")
        return {"state": "completed"}
    row, agent = fenced(s, work_id, body, authorization)
    obj = entity(s, row.entity_type, row.entity_id)
    if row.input_hash != fingerprint(s, obj):
        raise HTTPException(409, "work changed during execution; result not applied")
    if isinstance(obj, Task):
        story = s.get(Story, obj.story_id)
        if (not story or story.status in ("backlog", "blocked", "done")
                or obj.needs_human_confirmation or not get_task_readiness(s, obj)["ready"]):
            raise HTTPException(409, "dependencies, Story or human gate changed during execution")
    decision = body.result.get("decision")
    summary = body.result.get("summary", "")
    if not isinstance(summary, str) or not summary.strip():
        raise HTTPException(422, "result summary is required")
    if isinstance(obj, Proposal):
        from ..proposals import service as proposals
        if obj.status != "analyzing" or obj.claimed_by != body.worker_id:
            raise HTTPException(409, "proposal changed during execution")
        if decision == "ask":
            questions = body.result.get("questions")
            if not isinstance(questions, list) or not questions or any(not isinstance(q, str) or not q.strip() for q in questions):
                raise HTTPException(422, "grill requires nonempty questions")
            proposals.add_proposal_questions(s, proposal_id=obj.id, questions=questions,
                round_no=row.iteration + 1, summary=summary, agent=agent.agent_id)
        elif decision == "finalize":
            spec = body.result.get("spec")
            if not isinstance(spec, str) or not spec.strip():
                raise HTTPException(422, "converged spec is required")
            proposals.update_proposal(s, obj.id, converged_spec=spec)
            proposals.set_proposal_status(s, obj.id, "converged")
            # Explicit Worker instruction; do not run an automatic Server
            # materializer. Existing conversion remains the single validator.
            if body.result.get("create_ticket") is True:
                if not obj.auto_create_ticket:
                    raise HTTPException(409, "proposal requires human conversion approval")
                materialize_worker_plan(s, obj, body.result)
        else:
            raise HTTPException(422, "proposal decision must be ask or finalize")
    else:
        from ..work_items.service import set_status, create_comment
        if row.discussion_id:
            discussion = discussions.validate_turn(s, obj, row)
            if "qa_followup" in body.result and not (discussion.subject == "qa_defects"
                    and row.kind == "qa_review" and decision == "confirm"):
                raise HTTPException(422, "only confirmed QA defects may provide followup Tasks")
            decision = discussions.apply_turn(s, obj, row, agent, body.result)
            if decision is None:
                row.state, row.result, row.active_slot = "completed", result_json, None
                s.flush()
                return {"state": "completed"}
        if row.kind.endswith("_review"):
            if obj.status != "in_review" or obj.reviewer_agent_id != agent.id:
                raise HTTPException(409, "review assignment changed")
            if decision == "discuss" and not row.discussion_id:
                if "qa_followup" in body.result:
                    raise HTTPException(422, "discussion cannot create Bug Tasks before confirmation")
                discussions.start(s, obj, row, agent, body.result)
                row.state, row.result, row.active_slot = "completed", result_json, None
                s.flush()
                return {"state": "completed"}
            if decision not in ("approve", "reject") or decision == "reject" and not row.discussion_id:
                raise HTTPException(422, "raise findings with discuss; rejection requires a confirmed discussion")
            if row.kind == "qa_review" and decision == "approve":
                execution = s.query(WorkerWork).filter_by(entity_type="task", entity_id=obj.id,
                    kind="qa", iteration=obj.review_round or 0, state="completed").filter(
                        WorkerWork.discussion_id.is_(None)).order_by(WorkerWork.id.desc()).first()
                evidence = json.loads(execution.result or "{}") if execution else {}
                if not isinstance(evidence.get("tests_passed"), bool):
                    raise HTTPException(422, "QA Review requires accepted QA execution evidence")
                if evidence["tests_passed"] is False:
                    if not row.discussion_id or body.result.get("decision") != "confirm":
                        raise HTTPException(422, "failed QA requires discussion and confirmation before Bug creation")
                    materialize_qa_followup(s, obj, execution, body.result, agent)
                elif "qa_followup" in body.result:
                    raise HTTPException(422, "passing QA must not create defect followups")
            elif "qa_followup" in body.result:
                raise HTTPException(422, "only approved failed QA can create defect followups")
            from .service import review_task
            review_task(s, task_id=obj.id, reviewer_user_id=agent.user_id,
                reviewer_agent_id=agent.id, reviewer_agent_name=agent.agent_id,
                verdict=decision, comment=summary)
            if decision == "reject" and obj.status == "in_progress":
                from .models import TaskAssignment
                assignment = s.get(TaskAssignment, obj.current_assignment_id) if obj.current_assignment_id else None
                if assignment:
                    assignment.status, assignment.active_slot = "completed", None
                    assignment.completed_at = utc_now()
                obj.current_assignment_id = None
                set_status(s, obj.id, "todo", changed_by=agent.user_id, reason="Worker requested rework")
        else:
            if obj.status != "in_progress" or obj.assignee_id != agent.user_id:
                raise HTTPException(409, "execution assignment changed")
            if decision != "submit":
                raise HTTPException(422, "execution decision must be submit")
            if row.kind == "qa":
                if not isinstance(body.result.get("tests_passed"), bool):
                    raise HTTPException(422, "QA requires explicit tests_passed boolean")
                for field in ("deployment_steps", "test_steps", "test_results"):
                    values = body.result.get(field)
                    if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
                        raise HTTPException(422, f"QA requires nonempty {field}")
                qa_defects(body.result)
            create_comment(s, author=agent.agent_id, content=result_json, task_id=obj.id)
            set_status(s, obj.id, "in_review", changed_by=agent.user_id, reason="Worker submitted execution evidence")
    row.state, row.result, row.active_slot = "completed", result_json, None
    s.flush()
    return {"state": "completed"}


@router.post("/stories/{story_id}/complete")
def complete_story(story_id: int, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    story = s.get(Story, story_id)
    epic = s.get(Epic, story.epic_id) if story else None
    if not story or not epic:
        raise HTTPException(404, "Story not found")
    actor = authorize(s, epic.project_id, authorization)
    if story.owner_user_id != actor.user_id:
        raise HTTPException(403, "Story owner required")
    from .service import complete_story as apply_completion
    if story.status in ("backlog", "blocked"):
        raise HTTPException(409, "Story cannot be automatically completed")
    return _ser(apply_completion(s, story_id, changed_by=actor.user_id, reason="Worker verified all tasks and reviews"))
