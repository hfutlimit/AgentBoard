"""Discussion persistence/validation only. Workers explicitly offer every turn."""
import json
from fastapi import HTTPException
from .worker_work_models import WorkerDiscussion, WorkerWork
from ..projects.models import Agent
from ..work_items.service import create_comment
from ...core.service_helpers import _ser


def active(s, task_id):
    return s.query(WorkerDiscussion).filter_by(task_id=task_id, active_slot="active").first()


def view(d):
    data = _ser(d)
    data["messages"] = json.loads(d.messages)
    return data


def validate_turn(s, obj, row):
    d = s.get(WorkerDiscussion, row.discussion_id)
    if (not d or d.task_id != obj.id or d.status != "open" or d.turn != row.iteration
            or d.review_round != (obj.review_round or 0) or obj.status != "in_review"):
        raise HTTPException(409, "discussion turn changed or closed")
    kind = d.review_kind if d.turn % 2 else d.review_kind.removesuffix("_review")
    target = d.reviewer_agent if d.turn % 2 else d.owner_agent
    if row.kind != kind or row.target_agent != target:
        raise HTTPException(409, "discussion scope or recipient mismatch")
    return d


def append(s, d, agent, result, target):
    messages = json.loads(d.messages)
    previous = messages[-1]["comment_id"] if messages else None
    decision = result["decision"]
    content = (f"### 讨论 #{d.id} · 第 {len(messages) + 1} 条 · {decision}\n"
               f"{agent.agent_id} → {target or '讨论结束'}"
               + (f" · 回复评论 #{previous}" if previous else "")
               + "\n\n" + result["summary"])
    if result.get("evidence"):
        if not isinstance(result["evidence"], list) or any(not isinstance(x, str) for x in result["evidence"]):
            raise HTTPException(422, "discussion evidence must be a string array")
        content += "\n\n证据：\n" + "\n".join("- " + x for x in result["evidence"])
    comment = create_comment(s, task_id=d.task_id, author=agent.agent_id, content=content)
    messages.append({"comment_id": comment.id, "reply_to_comment_id": previous,
        "agent_id": agent.agent_id, "target_agent": target, "turn": d.turn,
        "decision": decision, "position": result.get("position"),
        "body": result["summary"], "evidence": result.get("evidence", [])})
    d.messages = json.dumps(messages, ensure_ascii=False)


def start(s, obj, row, agent, result):
    if active(s, obj.id):
        raise HTTPException(409, "Task already has an active discussion")
    source = s.query(WorkerWork).filter_by(entity_type="task", entity_id=obj.id,
        kind=row.kind.removesuffix("_review"), iteration=obj.review_round or 0, state="completed").filter(
            WorkerWork.discussion_id.is_(None)).order_by(WorkerWork.id.desc()).first()
    owner = s.get(Agent, source.agent_id) if source else None
    if not owner or owner.id == agent.id:
        raise HTTPException(422, "discussion requires an independent author and reviewer")
    subject = result.get("subject", "review_findings")
    if subject not in ("review_findings", "qa_defects") or (subject == "qa_defects" and
            (row.kind != "qa_review" or json.loads(source.result).get("tests_passed") is not False)):
        raise HTTPException(422, "qa_defects discussion requires failed QA evidence")
    d = WorkerDiscussion(project_id=obj.project_id, task_id=obj.id, source_work_id=source.id,
        review_kind=row.kind, subject=subject, review_round=obj.review_round or 0, owner_agent=owner.agent_id,
        reviewer_agent=agent.agent_id, status="open", active_slot="active", turn=0, max_rounds=3, messages="[]")
    s.add(d)
    s.flush()
    append(s, d, agent, result, d.owner_agent)
    return d


def apply_turn(s, obj, row, agent, result):
    """Return an explicit final verdict or None when discussion stays open/escalates."""
    d = validate_turn(s, obj, row)
    decision = result["decision"]
    if d.turn % 2 == 0:
        if decision != "respond" or result.get("position") not in ("agree", "disagree", "clarify"):
            raise HTTPException(422, "author must respond with agree/disagree/clarify and evidence")
        append(s, d, agent, result, d.reviewer_agent)
        d.turn += 1
        return None
    if decision not in ("confirm", "withdraw", "discuss", "escalate"):
        raise HTTPException(422, "reviewer must confirm, withdraw, discuss or escalate")
    if decision == "confirm" and json.loads(d.messages)[-1].get("position") != "agree":
        raise HTTPException(422, "unresolved disagreement requires discussion or human arbitration, not unilateral confirmation")
    if decision == "discuss":
        if d.turn >= d.max_rounds * 2 - 1:
            raise HTTPException(422, "discussion limit reached; withdraw or escalate")
        append(s, d, agent, result, d.owner_agent)
        d.turn += 1
        return None
    append(s, d, agent, result, None)
    d.status = {"confirm": "confirmed", "withdraw": "withdrawn", "escalate": "escalated"}[decision]
    d.active_slot = None
    d.turn += 1
    if decision == "escalate":
        from ..work_items.service import set_status
        set_status(s, obj.id, "blocked", reason=f"讨论 #{d.id} 需要人工裁决", status_reason="pending_requirement_change")
        create_comment(s, story_id=obj.story_id, author=agent.agent_id,
            content=f"Task #{obj.id} 的讨论 #{d.id} 未达成一致，已暂停，等待人工裁决。\n\n" + result["summary"])
        return None
    # A withdrawn failed-QA report needs corrected QA evidence, not Bug creation.
    source = s.get(WorkerWork, d.source_work_id)
    failed_qa = d.review_kind == "qa_review" and json.loads(source.result).get("tests_passed") is False
    if failed_qa and d.subject == "qa_defects":
        return "approve" if decision == "confirm" else "reject"
    if failed_qa and decision == "withdraw":
        return None  # Reassess the report separately; this did not confirm its defects.
    return "reject" if decision == "confirm" else "approve"
