"""Authenticated business-side intake. SQL todo state is the durable backlog.

No model invocation and no HTTP calls inside these DB transactions. The .NET
consumer re-reads readiness before committing its idempotent run/outbox.
"""
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import update
from datetime import timedelta

from ... import api_helpers
from ...core.infrastructure.database import get_session
from ...core.common.models import utc_now
from ..projects.models import Story, Epic
from ..projects.service import user_is_project_member
from ..work_items.models import Task
from ..work_items.service import get_task_readiness
from .durable_routing import durable_project_enabled

router = APIRouter(tags=["durable-intake"])
logger = logging.getLogger(__name__)


def authorize(s, project_id, authorization, permission):
    actor = api_helpers.resolve_actor_context(authorization, s, required_permission=permission)
    from .worker_work import enabled
    if enabled():
        raise HTTPException(409, "Worker-owned mode disables Server durable intake")
    if not (actor.is_admin or user_is_project_member(s, project_id, actor.user_id)):
        raise HTTPException(403, "durable intake requires project membership")
    if not durable_project_enabled(project_id):
        raise HTTPException(409, "project is not configured for durable intake")


@router.get("/api/durable/ready-tasks")
def ready_tasks(project_id: int, after_id: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                authorization: str | None = Header(None), s: Session = Depends(get_session)):
    authorize(s, project_id, authorization, "api:read")
    # Keyset paging scans even blocked candidates, so one waiting task cannot
    # starve later work. Never take over an existing legacy assignment.
    rows = (s.query(Task).join(Story, Task.story_id == Story.id).join(Epic, Story.epic_id == Epic.id)
            .filter(Task.project_id == project_id, Epic.project_id == project_id, Task.id > after_id,
                    Task.status == "todo", Task.current_assignment_id.is_(None),
                    Task.needs_human_confirmation.is_(False),
                    Story.status.notin_(["backlog", "blocked", "done"]))
            .order_by(Task.id).limit(limit).all())
    items = []
    for task in rows:
        if task.owner_user_id is None or not get_task_readiness(s, task)["ready"]:
            continue
        story = s.get(Story, task.story_id)
        from ..work_items.models import TaskDependency
        dependencies = s.query(TaskDependency).filter_by(task_id=task.id, dependency_type="blocks").all()
        items.append({"id": task.id, "story_id": task.story_id, "type": task.type,
                      "dependency_ids": sorted(d.depends_on_id for d in dependencies),
                      "context": {"task_id": task.id, "title": task.title,
                                  "description": task.description, "spec": task.spec,
                                  "story_description": story.description}})
    return {"items": items, "next_after_id": rows[-1].id if len(rows) == limit else 0}


@router.post("/api/durable/materialize")
def materialize(project_id: int, authorization: str | None = Header(None), s: Session = Depends(get_session)):
    authorize(s, project_id, authorization, "api:write")
    from ..proposals.models import Proposal, ProposalTicketRequest
    from ..proposals.service import execute_ticket_request
    from ...core.exceptions import InvalidValue
    # Conversion has its own committed CAS claim. Recover only stale AUTO
    # requests in this authorized project; apply() reuses proposal.story_id.
    now = utc_now()
    stale_ids = [row[0] for row in s.query(ProposalTicketRequest.id)
        .join(Proposal, ProposalTicketRequest.proposal_id == Proposal.id)
        .filter(Proposal.project_id == project_id, ProposalTicketRequest.type == "auto_story",
                ProposalTicketRequest.status == "processing",
                ProposalTicketRequest.updated_at < now - timedelta(minutes=10)).limit(20).all()]
    if stale_ids:
        s.execute(update(ProposalTicketRequest).where(ProposalTicketRequest.id.in_(stale_ids),
            ProposalTicketRequest.status == "processing", ProposalTicketRequest.updated_at < now - timedelta(minutes=10))
            .values(status="pending", updated_at=now))
        s.commit()
    rows = (s.query(ProposalTicketRequest).join(Proposal, ProposalTicketRequest.proposal_id == Proposal.id)
            .filter(Proposal.project_id == project_id, ProposalTicketRequest.type == "auto_story",
                    ProposalTicketRequest.status == "pending")
            .order_by(ProposalTicketRequest.id).limit(20).all())
    completed = []
    deferred = []
    for row in rows:
        try:
            execute_ticket_request(s, row.proposal_id, request_id=row.id)
            completed.append(row.id)
        except InvalidValue:
            s.rollback()  # Another materializer may have won the existing CAS.
            deferred.append(row.id)
            logger.warning("Durable materialization deferred request %s in project %s", row.id, project_id)
    return {"completed_request_ids": completed, "deferred_request_ids": deferred}
