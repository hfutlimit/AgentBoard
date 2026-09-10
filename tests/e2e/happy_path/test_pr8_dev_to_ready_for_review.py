"""PR-8 Happy Path E2E: Dev task → Codex → ready_for_review。

链路：
  1. 建 dev task（needs_human_confirmation=False，PR-8 不走 user gate）
  2. assignee claim → in_progress
  3. 模拟 codex "干完活了"：assignee 调 submit-review → in_review
  4. 验：
     - state=in_review
     - EVENT_TASK_READY_FOR_REVIEW 已发（broadcast 审计）
     - EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED 已发（internal 触发 workflow_worker）
  5. 模拟 workflow_worker 行为：调用 assign-reviewer 端点
     验：
     - state=in_review, reviewer_id 已设
     - EVENT_TASK_REVIEW_REQUESTED 已发（agent direct 路由 → "codex-agent" worker）
"""
from __future__ import annotations

import time

import pytest

from agentboard.core.common.enums import ItemType, Status
from agentboard.core.infrastructure.messaging import rabbitmq as mq_mod
from agentboard.features.work_items import service as task_service

from conftest import (
    auth_headers,
    clear_broker_queues,
    drain_broker_events,
    login_token,
    setup_story,
    setup_user_project,
)


@pytest.mark.usefixtures("app_engine", "workflow_worker_thread")
def test_pr8_happy_path_dev_to_ready_for_review(
    db_session, client, broker,
):
    """PR-8：Dev 任务从 claim 到 ready_for_review 完整链路。"""
    # 1. setup
    user_id, project_id = setup_user_project(db_session, role="owner")
    story_id = setup_story(db_session, project_id)
    token = login_token(client, db_session, user_id)
    H = auth_headers(token)
    # T1.5 + T3.1：claim 走 owner 门，PR-10 dispatch 走 owner 名下 agent 门；
    # 老 PR-8 时代只有 assignee_id，dispatch 现在是 hard fail 缺 owner/agent
    # → blocked。三件套 Worker+Agent+AgentInstance 缺一不可。
    # T1.5（commit c1b7e16）+ PR-10（agent-implements-task）：reviewer 候选
    # 池也限 owner 名下（self-review 通过 same_task_implementer 排除），
    # 所以准备两个同 owner agent：dev-agent（被 dispatch 当 assignee）
    # + review-agent（被 assign-reviewer 选中）。少一个就回到 422
    # 「无 candidate」的 escape hatch —— 那条路 PR-9 重写前先关掉。
    from agentboard.core.common.models import utc_now
    from agentboard.features.projects.models import Agent, AgentInstance, Worker
    now = utc_now()
    db_session.add(Worker(worker_id=f"pr8-w-{user_id}",
                          hostname="pr8-test", status="active",
                          last_heartbeat=now))
    db_session.flush()
    dev_agent_id = f"pr8-dev-{user_id}"
    review_agent_id = f"pr8-rev-{user_id}"
    db_session.add(Agent(agent_id=dev_agent_id, name=dev_agent_id,
                          user_id=user_id, roles='["dev"]',
                          online=True, enabled=True,
                          last_heartbeat=now))
    db_session.add(Agent(agent_id=review_agent_id, name=review_agent_id,
                          user_id=user_id, roles='["review"]',
                          online=True, enabled=True,
                          last_heartbeat=now))
    db_session.flush()
    db_session.add(AgentInstance(worker_id=f"pr8-w-{user_id}",
                                  agent_id=dev_agent_id,
                                  online=True, enabled=True,
                                  cli_command="echo dev",
                                  last_heartbeat=now))
    db_session.add(AgentInstance(worker_id=f"pr8-w-{user_id}",
                                  agent_id=review_agent_id,
                                  online=True, enabled=True,
                                  cli_command="echo review",
                                  last_heartbeat=now))
    db_session.commit()

    # 2. 建 dev task（PR-8 关注 implementation 任务，不走 user gate）
    # owner_user_id 必须设置：T1.5 统一执行门（commit c1b7e16）要求
    # task owner != NULL，否则 claim / submit-review 会被 fail-closed 拦住。
    # 旧 PR-8 写法只传 assignee_id，c1b7e16 之后 T1.5 门会 fail。
    dev_id = task_service.create_task(
        db_session, project_id=project_id, story_id=story_id,
        title=f"实现：{story_id}", type=ItemType.DEV.value,
        owner_user_id=user_id, assignee_id=user_id,
        needs_human_confirmation=False,  # PR-8 显式关
    ).id
    db_session.commit()
    db_session.expire_all()

    # 清 broker（隔离前序噪音）
    clear_broker_queues(broker)

    # 3. assignee claim（todo → in_progress）
    r = client.post(f"/api/tasks/{dev_id}/claim", headers=H)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == Status.IN_PROGRESS.value
    assert t["assignee_id"] == user_id

    # 4. 模拟 codex "干完活了"：assignee 调 submit-review（in_progress → in_review）
    r = client.post(f"/api/tasks/{dev_id}/submit-review", headers=H)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == Status.IN_REVIEW.value

    # 5. 验事件：broadcast 至少 task.ready_for_review，internal 至少 task.review_assignment_needed
    time.sleep(0.3)
    broadcast_msgs = drain_broker_events(
        broker, "agentboard.workflow.broadcast",
    )
    internal_msgs = drain_broker_events(
        broker, "agentboard.workflow.internal",
    )

    broadcast_events = [mq_mod.WorkflowMessage.from_bytes(b).event for b in broadcast_msgs]
    internal_events = [mq_mod.WorkflowMessage.from_bytes(b).event for b in internal_msgs]

    assert "task.ready_for_review" in broadcast_events, \
        f"submit-review 应发 task.ready_for_review（broadcast 审计），实际 {broadcast_events}"
    assert "task.review_assignment_needed" in internal_events, \
        f"PR-4 + PR-8：submit-review 触发 workflow_worker 选 reviewer，实际 {internal_events}"

    # 6. 模拟 workflow_worker：调 assign-reviewer 端点
    # （真实 worker 也会调这个端点，只是多走 MQ 一圈）
    # T1.5 policy：reviewer 候选池 = owner 名下 - implementer。
    # setup 已建 dev_agent + review_agent 两个同 owner agent；少建就
    # 422 「无 candidate」会让这个测试变假绿。**禁止 422 return 绕过**。
    r = client.post(
        f"/api/tasks/{dev_id}/assign-reviewer",
        json={"count": 1},
        headers=H,
    )
    assert r.status_code in (200, 201), (
        f"assign-reviewer 应真绿（reviewer 真实分配），不能 422 假绿。"
        f"实际 {r.status_code}: {r.text}"
    )

    # 7. 验：reviewer 已设（真绿关键证据）
    # 不验具体哪个 agent 被选（self-review 排除是 T1.5 内部行为，由
    # get_assignment_exclusion(same_task_implementer) 负责；单元层 e2e
    # 只验 assign-reviewer 端点确实有 reviewer 落到 Task 上）
    db_session.expire_all()
    t = db_session.get(task_service.Task, dev_id)
    assert t.reviewer_id is not None, "assign-reviewer 成功应设置 reviewer_id"

    # 8. assign-reviewer 不再发 internal 事件
    # PR-4 workflow_worker 后台线程在 race 中可能还没消费
    # task.review_assignment_needed（虽然 step 5 已经断言过 1 条），容忍 0
    # 或 1 条；1 条表示还没被消费，断言 reviewer 已分配就足够。
    time.sleep(0.2)
    final_internal = drain_broker_events(
        broker, "agentboard.workflow.internal",
    )
    assert len(final_internal) <= 1, \
        f"assign-reviewer 不应 fan-out >1 internal，实际 {len(final_internal)}"
