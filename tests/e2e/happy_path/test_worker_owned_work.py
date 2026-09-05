"""Protocol/business regression only; these fixtures are NOT real-provider E2E."""
import uuid
import json
from datetime import timedelta
import pytest
from conftest import setup_user_project, login_token, auth_headers
from agentboard.core.common.models import utc_now
from agentboard.features.projects.models import Agent, Story
from agentboard.features.work_items.models import Task, TaskDependency
from agentboard.features.work_items.service import create_task
from agentboard.features.scheduling.worker_work_models import WorkerWork
from agentboard.features.scheduling.worker_work import WORK_KINDS, queue_name


@pytest.fixture
def configured(client, db_session, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_WORKER_OWNED_ENABLED", "1")
    monkeypatch.setenv("AGENTBOARD_JUDGE_AUTO", "0")
    uid, pid = setup_user_project(db_session)
    from agentboard.features.projects.service import create_epic, create_story
    epic = create_epic(db_session, project_id=pid, title="Protocol fixtures")
    story = create_story(db_session, epic_id=epic.id, title="Explicit task fixture",
        create_default_tasks=False, created_by_user_id=uid)
    sid = story.id
    story.status = "in_progress"
    story.owner_user_id = uid
    for name in ("a", "b"):
        db_session.add(Agent(agent_id=name, name=name, user_id=uid, roles="[]"))
    db_session.commit()
    return uid, pid, sid, auth_headers(login_token(client, db_session, uid))


def task(db, configured, kind="dev"):
    uid, pid, sid, _ = configured
    return create_task(db, project_id=pid, story_id=sid, title=kind, type=kind,
        owner_user_id=uid, needs_human_confirmation=False)


def offer(client, configured, item, kind, iteration=0, entity_type="task"):
    _, pid, _, headers = configured
    response = client.post("/api/worker-work/offers", headers=headers, json={
        "project_id": pid, "entity_type": entity_type, "entity_id": item.id, "kind": kind, "iteration": iteration})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def claim_body(configured, kind, agent="a", token=None):
    return {"project_id": configured[1], "kind": kind, "worker_id": "w-" + agent,
            "agent_id": agent, "token": token or uuid.uuid4().hex}


def claim(client, configured, wid, body, expected=200):
    response = client.post(f"/api/worker-work/{wid}/claim", headers=configured[3], json=body)
    assert response.status_code == expected, response.text
    return response


def complete(client, configured, wid, body, result, expected=200):
    response = client.post(f"/api/worker-work/{wid}/complete", headers=configured[3], json={**body, "result": result})
    assert response.status_code == expected, response.text
    return response


def discussion_turn(client, configured, item, result=None):
    response = client.get(f"/api/worker-work/discussions?project_id={configured[1]}&task_id={item.id}", headers=configured[3])
    assert response.status_code == 200, response.text
    d = response.json()["items"][0]
    reviewer = d["turn"] % 2 == 1
    kind = d["review_kind"] if reviewer else d["review_kind"].removesuffix("_review")
    target = d["reviewer_agent"] if reviewer else d["owner_agent"]
    response = client.post("/api/worker-work/offers", headers=configured[3], json={
        "project_id": configured[1], "entity_type": "task", "entity_id": item.id,
        "kind": kind, "iteration": d["turn"], "discussion_id": d["id"], "target_agent": target})
    assert response.status_code == 200, response.text
    wid = response.json()["id"]
    body = claim_body(configured, kind, target)
    accepted = claim(client, configured, wid, body).json()
    assert accepted["context"]["discussion"]["id"] == d["id"]
    if result is not None:
        complete(client, configured, wid, body, result)
    return wid, body


def test_exactly_seven_kinds_and_project_isolation():
    assert len(WORK_KINDS) == 7
    assert queue_name(8, "dev") != queue_name(8, "qa") != queue_name(9, "qa")
    for bad in ("ticket", "review", "rework", "#", "dev.*"):
        with pytest.raises(ValueError): queue_name(8, bad)


def start_dev_discussion(client, db_session, configured):
    item = task(db_session, configured)
    wid = offer(client, configured, item, "dev")
    author = claim_body(configured, "dev")
    claim(client, configured, wid, author)
    complete(client, configured, wid, author, {"decision": "submit", "summary": "implemented"})
    db_session.expire_all()
    rid = offer(client, configured, item, "dev_review")
    reviewer = claim_body(configured, "dev_review", "b")
    claim(client, configured, rid, reviewer)
    result = {"decision": "discuss", "summary": "Boundary condition appears wrong", "evidence": ["src/app.py:12"]}
    complete(client, configured, rid, reviewer, result)
    complete(client, configured, rid, reviewer, result)
    return item


def test_multi_round_discussion_targets_original_agents_and_withdraws_false_positive(client, db_session, configured):
    from agentboard.features.scheduling.worker_work_models import WorkerDiscussion
    from agentboard.features.work_items.models import Comment
    item = start_dev_discussion(client, db_session, configured)
    db_session.expire_all()
    assert item.status == "in_review" and item.review_round == 0
    assert db_session.query(WorkerDiscussion).count() == 1
    assert client.post('/api/worker-work/offers', headers=configured[3], json={
        'project_id': configured[1], 'entity_type': 'task', 'entity_id': item.id,
        'kind': 'dev_review', 'iteration': 0}).status_code == 409
    wid, author = discussion_turn(client, configured, item)
    claim(client, configured, wid, claim_body(configured, "dev", "b"), 403)
    before = db_session.query(Comment).count()
    reply = {"decision": "respond", "position": "disagree", "summary": "Guard already covers it", "evidence": ["src/app.py:10"]}
    complete(client, configured, wid, author, reply)
    complete(client, configured, wid, author, reply)
    db_session.expire_all()
    assert db_session.query(Comment).count() == before + 1
    rid, reviewer = discussion_turn(client, configured, item)
    complete(client, configured, rid, reviewer, {"decision": "confirm", "summary": "Insist without agreement"}, 422)
    complete(client, configured, rid, reviewer, {"decision": "discuss", "summary": "What about empty input?"})
    discussion_turn(client, configured, item, {"decision": "respond", "position": "disagree", "summary": "Empty-input test proves guard", "evidence": ["tests/test_app.py:21"]})
    discussion_turn(client, configured, item, {"decision": "withdraw", "summary": "Verified counter-evidence, false positive"})
    db_session.expire_all()
    assert item.status == "done" and item.review_round == 0
    result = client.get(f"/api/worker-work/discussions?project_id={configured[1]}&story_id={configured[2]}", headers=configured[3]).json()
    discussion = result["items"][0]
    assert discussion["status"] == "withdrawn"
    assert len(discussion["messages"]) == 5
    assert all(m["reply_to_comment_id"] == p["comment_id"] for p, m in zip(discussion["messages"], discussion["messages"][1:]))
    assert client.get(f"/api/worker-work/discussions?project_id={configured[1]}&task_id={item.id}").status_code == 401


def test_discussion_retry_expiry_and_round_limit_escalate_without_rework(client, db_session, configured):
    from agentboard.features.scheduling.worker_work_models import WorkerDiscussion
    from agentboard.features.work_items.models import Comment
    item = start_dev_discussion(client, db_session, configured)
    wid, old = discussion_turn(client, configured, item)
    response = client.post(f"/api/worker-work/{wid}/fail", headers=configured[3], json={**old, "result": {"summary": "CLI unavailable"}})
    assert response.status_code == 200
    db_session.expire_all()
    assert item.status == "in_review"
    renewed = claim_body(configured, "dev")
    claim(client, configured, wid, renewed)
    row = db_session.get(WorkerWork, wid)
    row.lease_until = utc_now() - timedelta(seconds=1)
    db_session.commit()
    newest = claim_body(configured, "dev")
    claim(client, configured, wid, newest)
    reply = {"decision": "respond", "position": "disagree", "summary": "Counter-evidence"}
    complete(client, configured, wid, renewed, reply, 409)
    complete(client, configured, wid, newest, reply)
    for turn in range(3):
        rid, reviewer = discussion_turn(client, configured, item)
        if turn < 2:
            complete(client, configured, rid, reviewer, {"decision": "discuss", "summary": "Need clarification"})
            discussion_turn(client, configured, item, reply)
        else:
            complete(client, configured, rid, reviewer, {"decision": "discuss", "summary": "Endless loop"}, 422)
            complete(client, configured, rid, reviewer, {"decision": "escalate", "summary": "Requirement ambiguity needs human decision"})
    db_session.expire_all()
    assert item.status == "blocked" and item.review_round == 0
    assert db_session.query(WorkerDiscussion).one().status == "escalated"
    assert db_session.query(Comment).filter_by(story_id=configured[2]).count() >= 1


def test_target_queue_uses_same_stable_hash_as_node():
    assert queue_name(8, "dev", "a") == "agentboard.work.v2.project.8.dev.agent.ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
    assert queue_name(8, "dev", "a") != queue_name(8, "dev", "b")


def test_claim_competition_result_idempotence_and_no_self_review(client, db_session, configured):
    item = task(db_session, configured)
    wid = offer(client, configured, item, "dev")
    assert offer(client, configured, item, "dev") == wid
    body = claim_body(configured, "dev")
    claim(client, configured, wid, body)
    claim(client, configured, wid, body)  # lost HTTP reply resumes same attempt
    claim(client, configured, wid, claim_body(configured, "dev", "b"), 409)
    result = {"decision": "submit", "summary": "implemented", "commit": "abc"}
    complete(client, configured, wid, body, result)
    complete(client, configured, wid, body, result)
    complete(client, configured, wid, body, {**result, "summary": "overwrite"}, 409)
    db_session.expire_all()
    assert item.status == "in_review"
    review = offer(client, configured, item, "dev_review")
    claim(client, configured, review, claim_body(configured, "dev_review", "a"), 403)
    reviewer = claim_body(configured, "dev_review", "b")
    claim(client, configured, review, reviewer)
    complete(client, configured, review, reviewer, {"decision": "approve", "summary": "independent review"})
    db_session.expire_all()
    assert item.status == "done"
    assert db_session.get(Story, configured[2]).status != "done"  # no Server progression


def test_qa_is_independent_task_with_its_own_review_and_steps(client, db_session, configured):
    dev = task(db_session, configured)
    wid = offer(client, configured, dev, "dev")
    developer = claim_body(configured, "dev")
    claim(client, configured, wid, developer)
    complete(client, configured, wid, developer, {"decision": "submit", "summary": "implemented"})
    db_session.expire_all()
    rid = offer(client, configured, dev, "dev_review")
    reviewer = claim_body(configured, "dev_review", "b")
    claim(client, configured, rid, reviewer)
    complete(client, configured, rid, reviewer, {"decision": "approve", "summary": "reviewed"})
    qa = task(db_session, configured, "qa")
    db_session.add(TaskDependency(task_id=qa.id, depends_on_id=dev.id, dependency_type="blocks"))
    db_session.commit()
    qid = offer(client, configured, qa, "qa")
    claim(client, configured, qid, claim_body(configured, "qa", "a"), 403)
    tester = claim_body(configured, "qa", "b")
    claim(client, configured, qid, tester)
    result = {"decision": "submit", "summary": "tested"}
    complete(client, configured, qid, tester, result, 422)
    result.update(deployment_steps=["start local test app"], test_steps=["GET /health"], test_results=["HTTP 200"], tests_passed=True)
    complete(client, configured, qid, tester, result)
    db_session.expire_all()
    assert qa.status == "in_review", "QA must not silently complete without QA Review"
    qr = offer(client, configured, qa, "qa_review")
    claim(client, configured, qr, claim_body(configured, "qa_review", "b"), 403)
    checker = claim_body(configured, "qa_review", "a")
    claim(client, configured, qr, checker)
    complete(client, configured, qr, checker, {"decision": "approve", "summary": "QA steps, coverage and evidence reasonable"})
    db_session.expire_all()
    assert qa.status == "done"
    closed = client.post(f"/api/worker-work/stories/{configured[2]}/complete", headers=configured[3], json={})
    assert closed.status_code == 200, closed.text
    db_session.expire_all()
    assert db_session.get(Story, configured[2]).status == "done"


def test_expired_lease_requires_new_token_and_rejects_old_result(client, db_session, configured):
    item = task(db_session, configured)
    wid = offer(client, configured, item, "dev")
    old = claim_body(configured, "dev")
    claim(client, configured, wid, old)
    row = db_session.get(WorkerWork, wid)
    row.lease_until = utc_now() - timedelta(seconds=1)
    db_session.commit()
    assert "new_token_required" in claim(client, configured, wid, old, 409).text
    new = claim_body(configured, "dev", "b")
    claim(client, configured, wid, new)
    complete(client, configured, wid, old, {"decision": "submit", "summary": "late result"}, 409)
    complete(client, configured, wid, new, {"decision": "submit", "summary": "current result"})


def test_design_review_rejection_routes_back_to_design(client, db_session, configured):
    item = task(db_session, configured, "design")
    wid = offer(client, configured, item, "design")
    author = claim_body(configured, "design")
    claim(client, configured, wid, author)
    complete(client, configured, wid, author, {"decision": "submit", "summary": "design"})
    db_session.expire_all()
    rid = offer(client, configured, item, "design_review")
    reviewer = claim_body(configured, "design_review", "b")
    claim(client, configured, rid, reviewer)
    complete(client, configured, rid, reviewer, {"decision": "reject", "summary": "missing design details"}, 422)
    complete(client, configured, rid, reviewer, {"decision": "discuss", "summary": "missing design details"})
    discussion_turn(client, configured, item, {"decision": "respond", "position": "agree", "summary": "verified missing details"})
    discussion_turn(client, configured, item, {"decision": "confirm", "summary": "agreed after verification"})
    db_session.expire_all()
    assert item.status == "todo" and item.review_round == 1
    offer(client, configured, item, "design", 1)


def test_scope_auth_dependencies_and_old_intake_are_closed(client, db_session, configured):
    item = task(db_session, configured)
    assert client.get(f"/api/worker-work/snapshot?project_id={configured[1]}&entity_type=task").status_code == 401
    wid = offer(client, configured, item, "dev")
    claim(client, configured, wid, claim_body(configured, "qa"), 409)
    assert client.get(f"/api/durable/ready-tasks?project_id={configured[1]}", headers=configured[3]).status_code == 409
    assert client.post(f"/api/tasks/{item.id}/claim", headers=configured[3]).status_code != 200


def test_proposal_grill_and_ticket_are_one_work_kind(client, db_session, configured):
    from agentboard.features.proposals import service as proposals
    from agentboard.features.projects.models import Epic
    epic = db_session.query(Epic).filter_by(project_id=configured[1]).first()
    proposal = proposals.create_proposal(db_session, project_id=configured[1], title="Small greeting",
        content="Implement greeting with unit tests", author_id=configured[0],
        auto_create_ticket=True, target_epic_id=epic.id)
    proposals.set_proposal_status(db_session, proposal.id, "queued")
    wid = offer(client, configured, proposal, "proposal", entity_type="proposal")
    body = claim_body(configured, "proposal")
    claim(client, configured, wid, body)
    complete(client, configured, wid, body, {"decision": "finalize", "summary": "clear requirements",
        "spec": "- [ ] Implement greeting function with tests", "create_ticket": True,
        "activate_story": True, "ticket_plan": {
            "tasks": [{"title": "Design", "type": "design"}, {"title": "Code", "type": "dev"}, {"title": "Test", "type": "qa"}],
            "dependencies": [["Design", "Code"], ["Code", "Test"]]}})
    db_session.expire_all()
    assert proposal.story_id is not None
    tasks = db_session.query(Task).filter_by(story_id=proposal.story_id).all()
    assert {t.type for t in tasks} == {"design", "dev", "qa"}
    assert db_session.query(WorkerWork).filter_by(entity_type="proposal").count() == 1


def test_expired_third_attempt_becomes_terminal_and_retains_history(client, db_session, configured):
    item = task(db_session, configured)
    wid = offer(client, configured, item, "dev")
    for attempt in range(3):
        body = claim_body(configured, "dev")
        claim(client, configured, wid, body)
        db_session.expire_all()
        row = db_session.get(WorkerWork, wid)
        assert row.attempts == attempt + 1
        row.lease_until = utc_now() - timedelta(seconds=1)
        db_session.commit()
    terminal = claim(client, configured, wid, claim_body(configured, "dev"))
    assert terminal.json()["state"] == "failed"
    db_session.expire_all()
    assert row.state == "failed" and row.active_slot is None
    assert item.status == "blocked"
    assert len(json.loads(row.attempt_history)) == 2
    claim(client, configured, wid, claim_body(configured, "dev"), 409)
    complete(client, configured, wid, body, {"decision": "submit", "summary": "late"}, 409)


def test_legacy_proposal_recovery_cannot_steal_worker_lease(client, db_session, configured):
    from agentboard.features.proposals import service as proposals
    p = proposals.create_proposal(db_session, project_id=configured[1], title="Keep lease",
        content="Scoped test", author_id=configured[0])
    proposals.set_proposal_status(db_session, p.id, "queued")
    wid = offer(client, configured, p, "proposal", entity_type="proposal")
    body = claim_body(configured, "proposal")
    claim(client, configured, wid, body)
    db_session.expire_all()
    p.claimed_at = utc_now() - timedelta(days=1)
    db_session.commit()
    assert proposals.reclaim_stale_proposals(db_session, lease_seconds=0) == []
    assert proposals.recover_failed_proposals(db_session, window_seconds=0) == []
    db_session.refresh(p)
    assert p.status == "analyzing"


def test_dependency_change_fences_completion(client, db_session, configured):
    item = task(db_session, configured)
    wid = offer(client, configured, item, "dev")
    body = claim_body(configured, "dev")
    claim(client, configured, wid, body)
    upstream = task(db_session, configured, "design")
    db_session.add(TaskDependency(task_id=item.id, depends_on_id=upstream.id, dependency_type="blocks"))
    db_session.commit()
    complete(client, configured, wid, body, {"decision": "submit", "summary": "obsolete execution"}, 409)
    db_session.expire_all()
    assert item.status == "in_progress"
    assert db_session.get(WorkerWork, wid).state == "leased"
    failure = client.post(f"/api/worker-work/{wid}/fail", headers=configured[3],
        json={**body, "result": {"summary": "Completion rejected after requirements changed"}})
    assert failure.status_code == 200, failure.text
    db_session.expire_all()
    assert db_session.get(WorkerWork, wid).state == "failed"
    assert item.status == "in_progress", "Do not undo human dependency/state changes"


def test_proposal_grill_answers_are_in_the_fenced_input(client, db_session, configured):
    from agentboard.features.proposals import service as proposals
    from agentboard.features.proposals.models import ProposalQuestion
    p = proposals.create_proposal(db_session, project_id=configured[1], title="Clarify greeting",
        content="Which language?", author_id=configured[0])
    proposals.set_proposal_status(db_session, p.id, "queued")
    wid = offer(client, configured, p, "proposal", entity_type="proposal")
    first = claim_body(configured, "proposal")
    claim(client, configured, wid, first)
    complete(client, configured, wid, first, {"decision": "ask", "summary": "Need language",
        "questions": ["English or Chinese?"]})
    db_session.expire_all()
    assert p.status == "awaiting" and p.current_round == 1
    question = db_session.query(ProposalQuestion).filter_by(proposal_id=p.id).one()
    proposals.answer_proposal_question(db_session, question.id, answer="English", user_id=configured[0])
    again = offer(client, configured, p, "proposal", iteration=1, entity_type="proposal")
    second = claim_body(configured, "proposal")
    accepted = claim(client, configured, again, second)
    assert "English" in json.dumps(accepted.json()["context"]["history"])
    proposals.answer_proposal_question(db_session, question.id, answer="Chinese", user_id=configured[0])
    complete(client, configured, again, second, {"decision": "finalize", "summary": "Old answer",
        "spec": "English greeting", "create_ticket": False}, 409)


def test_retry_supplies_previous_failure_without_lease_secrets(client, db_session, configured):
    item = task(db_session, configured)
    wid = offer(client, configured, item, "dev")
    first = claim_body(configured, "dev")
    claim(client, configured, wid, first)
    failed = client.post(f"/api/worker-work/{wid}/fail", headers=configured[3],
        json={**first, "result": {"summary": "Missing structured test evidence"}})
    assert failed.status_code == 200, failed.text
    again = claim(client, configured, wid, claim_body(configured, "dev"))
    context = again.json()["context"]
    assert context["previous_attempts"][0]["result"]["summary"] == "Missing structured test evidence"
    assert first["token"] not in json.dumps(context)


def test_legacy_review_recovery_does_not_reassign_local_agents(client, db_session, configured):
    from agentboard.features.scheduling.service import scan_review_timeouts, unblock_insufficient_agent_tasks
    item = task(db_session, configured)
    wid = offer(client, configured, item, "dev")
    author = claim_body(configured, "dev")
    claim(client, configured, wid, author)
    complete(client, configured, wid, author, {"decision": "submit", "summary": "implementation"})
    db_session.expire_all()
    rid = offer(client, configured, item, "dev_review")
    claim(client, configured, rid, claim_body(configured, "dev_review", "b"))
    db_session.expire_all()
    original = item.reviewer_agent_id
    item.updated_at = utc_now() - timedelta(days=1)
    db_session.commit()
    scan = scan_review_timeouts(db_session, timeout_minutes=1)
    assert scan["tasks_reassigned"] == scan["blocked"] == 0
    assert unblock_insufficient_agent_tasks(db_session, configured[0]) == 0
    db_session.refresh(item)
    assert item.status == "in_review" and item.reviewer_agent_id == original


@pytest.mark.parametrize("legacy_review_mode", ["single", "majority"])
def test_failed_qa_creates_new_bugs_then_independent_retest_atomically(client, db_session, configured, monkeypatch, legacy_review_mode):
    # Agent c fixes bugs but did not implement the original dev. It must still
    # be excluded from QA through bug dependencies, not just dev dependencies.
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", legacy_review_mode)
    db_session.add(Agent(agent_id="c", name="c", user_id=configured[0], roles="[]"))
    db_session.commit()

    def execute_and_review(item, kind, author="a", reviewer="b"):
        wid = offer(client, configured, item, kind)
        body = claim_body(configured, kind, author)
        claim(client, configured, wid, body)
        complete(client, configured, wid, body, {"decision": "submit", "summary": "implemented"})
        db_session.expire_all()
        rid = offer(client, configured, item, kind + "_review")
        check = claim_body(configured, kind + "_review", reviewer)
        claim(client, configured, rid, check)
        complete(client, configured, rid, check, {"decision": "approve", "summary": "reviewed"})
        db_session.expire_all()

    dev = task(db_session, configured)
    execute_and_review(dev, "dev")
    qa = task(db_session, configured, "qa")
    db_session.add(TaskDependency(task_id=qa.id, depends_on_id=dev.id, dependency_type="blocks"))
    db_session.commit()
    initial_qa = qa
    for cycle in range(2):
        qid = offer(client, configured, qa, "qa")
        tester = claim_body(configured, "qa", "b")
        claim(client, configured, qid, tester)
        defects = [{"title": f"Fix greeting {cycle}", "description": "GET /greet: expected 200, actual 500; log ref /tmp/error"}]
        failed = {"decision": "submit", "summary": "Found reproducible bug", "tests_passed": False,
            "deployment_steps": ["start locally"], "test_steps": ["GET /greet"],
            "test_results": ["HTTP 500"], "defects": defects}
        complete(client, configured, qid, tester, {**failed, "defects": []}, 422)
        complete(client, configured, qid, tester, {**failed, "tests_passed": True}, 422)
        complete(client, configured, qid, tester, failed)
        db_session.expire_all()
        assert qa.status == "in_review"
        rid = offer(client, configured, qa, "qa_review")
        check = claim_body(configured, "qa_review", "a")
        claim(client, configured, rid, check)
        count = db_session.query(Task).count()
        approved = {"decision": "approve", "summary": "Tests are reasonable; product has a bug"}
        complete(client, configured, rid, check, approved, 422)
        complete(client, configured, rid, check, {"decision": "discuss", "subject": "qa_defects", "summary": "Verify reported defect"})
        discussion_turn(client, configured, qa, {"decision": "respond", "position": "agree", "summary": "Reproduced, report is accurate"})
        rid, check = discussion_turn(client, configured, qa)
        approved["decision"] = "confirm"
        followup = {"source_work_id": qid, "bugs": defects,
            "retest": {"title": f"Retest {cycle}", "description": "Deploy and rerun original acceptance + bug repro"}}
        for invalid in ({**followup, "source_work_id": qid + 100}, {**followup, "bugs": []},
                        {**followup, "retest": {"title": "", "description": "bad"}}):
            complete(client, configured, rid, check, {**approved, "qa_followup": invalid}, 422)
        db_session.expire_all()
        assert db_session.query(Task).count() == count and qa.status == "in_review"
        approved["qa_followup"] = followup
        complete(client, configured, rid, check, approved)
        complete(client, configured, rid, check, approved)  # lost HTTP reply: no duplicate Tasks
        db_session.expire_all()
        assert db_session.query(Task).count() == count + 2
        assert qa.status == "done" and dev.status == "done" and dev.review_round == 0
        bug = db_session.query(Task).filter_by(type="bug", title=defects[0]["title"]).one()
        retest = db_session.query(Task).filter_by(type="qa", title=f"Retest {cycle}").one()
        assert bug.story_id == qa.story_id and bug.owner_user_id == qa.owner_user_id
        assert bug.assignee_id is None and bug.status == "todo"
        assert db_session.query(TaskDependency).filter_by(task_id=bug.id, depends_on_id=qa.id).one()
        assert db_session.query(TaskDependency).filter_by(task_id=retest.id, depends_on_id=bug.id).one()
        early = client.post("/api/worker-work/offers", headers=configured[3], json={
            "project_id": configured[1], "entity_type": "task", "entity_id": retest.id, "kind": "qa", "iteration": 0})
        assert early.status_code == 409
        close = client.post(f"/api/worker-work/stories/{configured[2]}/complete", headers=configured[3], json={})
        assert close.status_code != 200
        execute_and_review(bug, "dev", "c", "b")
        next_id = offer(client, configured, retest, "qa")
        claim(client, configured, next_id, claim_body(configured, "qa", "c"), 403)
        claim(client, configured, next_id, claim_body(configured, "qa", "a"), 403)
        qa = retest

    qid = offer(client, configured, qa, "qa")
    body = claim_body(configured, "qa", "b")
    claim(client, configured, qid, body)
    complete(client, configured, qid, body, {"decision": "submit", "summary": "Fixes verified", "tests_passed": True,
        "deployment_steps": ["start fixed app locally"], "test_steps": ["repro and regression"], "test_results": ["all pass"]})
    db_session.expire_all()
    rid = offer(client, configured, qa, "qa_review")
    body = claim_body(configured, "qa_review", "a")
    claim(client, configured, rid, body)
    complete(client, configured, rid, body, {"decision": "approve", "summary": "QA evidence sufficient"})
    close = client.post(f"/api/worker-work/stories/{configured[2]}/complete", headers=configured[3], json={})
    assert close.status_code == 200, close.text
    db_session.expire_all()
    assert db_session.get(Story, configured[2]).status == "done"
    assert initial_qa.status == "done"
    # The live harness must accept historical failed QA only when its concrete
    # linked bug/retest chain is closed, never merely because Tasks say done.
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    from report_worker_owned_e2e import qa_acceptance_closed
    tasks = [{"id": t.id, "type": t.type, "status": t.status, "labels": t.labels}
             for t in db_session.query(Task).filter_by(story_id=configured[2])]
    works = [{"id": w.id, "entity_type": w.entity_type, "entity_id": w.entity_id,
              "kind": w.kind, "iteration": w.iteration, "result": json.loads(w.result)}
             for w in db_session.query(WorkerWork).filter_by(state="completed")]
    edges = {(e.task_id, e.depends_on_id) for e in db_session.query(TaskDependency)}
    assert qa_acceptance_closed(tasks, works, edges)
    assert not qa_acceptance_closed(tasks, works, set())
    last_qa = max((w for w in works if w["kind"] == "qa"), key=lambda w: w["id"])
    last_qa["result"]["tests_passed"] = False
    assert not qa_acceptance_closed(tasks, works, edges)


def test_unreasonable_failed_qa_is_retested_not_sent_to_dev(client, db_session, configured):
    qa = task(db_session, configured, "qa")
    qid = offer(client, configured, qa, "qa")
    body = claim_body(configured, "qa", "b")
    claim(client, configured, qid, body)
    complete(client, configured, qid, body, {"decision": "submit", "summary": "claimed failure", "tests_passed": False,
        "deployment_steps": ["start"], "test_steps": ["test"], "test_results": ["failed"],
        "defects": [{"title": "Unsubstantiated defect", "description": "No usable reproduction evidence"}]})
    db_session.expire_all()
    rid = offer(client, configured, qa, "qa_review")
    body = claim_body(configured, "qa_review", "a")
    claim(client, configured, rid, body)
    complete(client, configured, rid, body, {"decision": "discuss", "subject": "review_findings", "summary": "Missing reproducible test evidence"})
    discussion_turn(client, configured, qa, {"decision": "respond", "position": "agree", "summary": "Need to collect evidence"})
    discussion_turn(client, configured, qa, {"decision": "confirm", "summary": "Correct QA, no product bug established"})
    db_session.expire_all()
    assert qa.status == "todo" and qa.review_round == 1
    assert db_session.query(Task).filter_by(type="bug").count() == 0
    offer(client, configured, qa, "qa", iteration=1)


@pytest.mark.parametrize("subject", ["qa_defects", "review_findings"])
def test_withdrawn_qa_concern_never_creates_unconfirmed_bugs(client, db_session, configured, subject):
    qa = task(db_session, configured, "qa")
    qid = offer(client, configured, qa, "qa")
    body = claim_body(configured, "qa", "b")
    claim(client, configured, qid, body)
    complete(client, configured, qid, body, {"decision": "submit", "summary": "failed QA", "tests_passed": False,
        "deployment_steps": ["start locally"], "test_steps": ["GET /test"], "test_results": ["HTTP 500"],
        "defects": [{"title": "500", "description": "Reproduction under discussion"}]})
    db_session.expire_all()
    rid = offer(client, configured, qa, "qa_review")
    reviewer = claim_body(configured, "qa_review", "a")
    claim(client, configured, rid, reviewer)
    complete(client, configured, rid, reviewer, {"decision": "discuss", "subject": subject, "summary": "Verify report"})
    discussion_turn(client, configured, qa, {"decision": "respond", "position": "disagree", "summary": "Counter-evidence"})
    discussion_turn(client, configured, qa, {"decision": "withdraw", "summary": "Verified counter-evidence"})
    db_session.expire_all()
    assert db_session.query(Task).filter_by(type="bug").count() == 0
    if subject == "qa_defects":
        assert qa.status == "todo" and qa.review_round == 1
    else:
        assert qa.status == "in_review"
        next_review = offer(client, configured, qa, "qa_review")
        assert next_review != rid  # No reuse of completed review; defects still need their own confirmation.
