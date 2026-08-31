"""PR-9 Happy Path E2E: Reviewer approve → done → final unlock。

链路：
  1. 建 dev task，in_review 状态，reviewer_id 已设（模拟 PR-8 收尾）
  2. 模拟 reviewer（WorkBuddy）"审完了"：调 review 端点 approve
  3. 验：
     - dev task 状态=done, status_reason=completed
     - EVENT_TASK_REVIEWED 已发（broadcast 审计）
     - 如果有 dependent task → EVENT_TASK_AVAILABLE 已发（dependency unlock）
"""
from __future__ import annotations

import time

import pytest

from agentboard.core.common.enums import ItemType, Status, StatusReason
from agentboard.core.common.models import utc_now
from agentboard.core.infrastructure.messaging import rabbitmq as mq_mod
from agentboard.features.projects.models import Agent
from agentboard.features.scheduling import service as scheduling_service
from agentboard.features.work_items import service as task_service
from agentboard.features.work_items.models import TaskDependency

from conftest import (
    auth_headers,
    clear_broker_queues,
    drain_broker_events,
    login_token,
    setup_story,
    setup_user_project,
)


@pytest.mark.usefixtures("app_engine", "workflow_worker_thread")
def test_pr9_happy_path_reviewer_approve_to_done(
    db_session, client, broker,
):
    """PR-9：Reviewer approve → done + dependency unlock。"""
    # 1. setup: 1 owner user + 1 reviewer user
    owner_id, project_id = setup_user_project(db_session, role="owner")
    story_id = setup_story(db_session, project_id)
    owner_token = login_token(client, db_session, owner_id)
    owner_H = auth_headers(owner_token)

    # 建 reviewer user
    import uuid
    reviewer_username = f"r-{uuid.uuid4().hex[:8]}"
    from agentboard.features.identity.service import register_user
    reviewer_user = register_user(
        db_session, username=reviewer_username, password="test1234",
    )
    from agentboard.features.projects.models import ProjectMember
    db_session.add(ProjectMember(
        project_id=project_id, user_id=reviewer_user.id, role="member",
    ))
    # roles do not authorize workloads. A live AgentInstance with an explicit
    # executor_type makes this user runnable for review and implementation.
    worker_id = f"reviewer-worker-{uuid.uuid4().hex[:6]}"
    reviewer_agent = Agent(
        agent_id=f"reviewer-{uuid.uuid4().hex[:6]}",
        name="workbuddy reviewer",
        roles="[]",
        capabilities="[]",
        user_id=reviewer_user.id,
        online=True,
        enabled=True,
        last_heartbeat=utc_now(),
    )
    db_session.add(reviewer_agent)
    db_session.flush()
    scheduling_service.register_worker(
        db_session, worker_id=worker_id, hostname="test",
    )
    instance = scheduling_service.upsert_agent_instance(
        db_session, worker_id=worker_id, agent_id=reviewer_agent.agent_id,
        executor_type="fake",
    )
    scheduling_service.instance_heartbeat(
        db_session, instance.id, caller_worker_id=worker_id, probe_ok=True,
    )
    db_session.commit()
    reviewer_token = login_token(client, db_session, reviewer_user.id)
    reviewer_H = auth_headers(reviewer_token)
    broker.declare_agent_queue(worker_id)

    # 建 dev task + successor task
    dev_id = task_service.create_task(
        db_session, project_id=project_id, story_id=story_id,
        title=f"实现：{story_id}", type=ItemType.DEV.value,
        assignee_id=owner_id,
        needs_human_confirmation=False,
    ).id
    successor_id = task_service.create_task(
        db_session, project_id=project_id, story_id=story_id,
        title=f"QA：{story_id}", type=ItemType.QA.value,
        assignee_id=owner_id,
        needs_human_confirmation=False,
    ).id
    db_session.add(TaskDependency(task_id=successor_id, depends_on_id=dev_id))
    db_session.commit()
    db_session.expire_all()

    # 模拟 PR-8 收尾：dev 直接推到 in_review + 分配 reviewer
    r = client.post(f"/api/tasks/{dev_id}/claim", headers=owner_H)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/tasks/{dev_id}/submit-review", headers=owner_H)
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/tasks/{dev_id}/assign-reviewer",
        json={"reviewer_user_id": reviewer_user.id},
        headers=owner_H,
    )
    assert r.status_code in (200, 201), r.text
    db_session.expire_all()
    t = db_session.get(task_service.Task, dev_id)
    assert t.reviewer_id == reviewer_user.id, \
        f"assign-reviewer 应设 reviewer_id={reviewer_user.id}，实际 {t.reviewer_id}"

    # 清 broker
    clear_broker_queues(broker)

    # 2. reviewer 调 review 端点 approve
    r = client.post(
        f"/api/tasks/{dev_id}/review",
        json={"verdict": "approve", "comment": "代码 OK"},
        headers=reviewer_H,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == Status.DONE.value
    assert body["status_reason"] == StatusReason.COMPLETED.value

    # 3. The server immediately dispatches the newly-unlocked successor.
    db_session.expire_all()
    successor = db_session.get(task_service.Task, successor_id)
    assert successor.status == Status.IN_PROGRESS.value
    assert successor.assignee_id == reviewer_user.id

    # 4. Audit remains broadcast; executable successor work is targeted.
    time.sleep(0.3)
    broadcast_msgs = drain_broker_events(
        broker, "agentboard.workflow.broadcast",
    )
    broadcast_events = [mq_mod.WorkflowMessage.from_bytes(b).event for b in broadcast_msgs]
    assert "task.reviewed" in broadcast_events, \
        f"reviewer approve 应发 task.reviewed（broadcast 审计），实际 {broadcast_events}"
    targeted_msgs = drain_broker_events(
        broker, mq_mod.WorkflowTopology().agent_queue(worker_id),
    )
    targeted_events = [mq_mod.WorkflowMessage.from_bytes(b).event for b in targeted_msgs]
    assert "task.assigned" in targeted_events

    # 5. 验：internal_queue 没新增（PR-9 不走 Python worker 编排）
    time.sleep(0.2)
    final_internal = drain_broker_events(
        broker, "agentboard.workflow.internal",
    )
    assert len(final_internal) == 0, \
        f"reviewer approve 路径不应发 internal 事件，实际 {len(final_internal)}"
