"""PR-7 Happy Path E2E: Proposal → Design → User Confirm → Dev Dispatch。

链路（PR-10 dispatch 模型）:
  1. 建 story + design task (needs_human_confirmation=True) + dev task
  2. 设计者 submit-review design → in_review
     验：PR-6 行为 —— 没发 internal event，Python workflow_worker 看不到
  3. 用户 POST /api/tasks/{design_id}/user_confirm
     → 设计 done + comment 留 trail
     → dev task 依赖解锁
     → **PR-10 dispatch** 立即把 dev 推到 in_progress
     → task.assigned 路由到 owner 名下 dev-agent
  4. 验：design 状态=done + status_reason=completed
     dev 状态=in_progress（不再用 get_unlocked_dependent_tasks / TODO
     列表判断 —— dispatch 后 dev 不在 TODO 状态）
     dev.assignment_deferred_reason 为空（dispatch 成功）
  5. 验：broadcast 至少 1 条 task.reviewed
     PR-10 不发 task.available 给 dev（走 task.assigned 到 agent 队列）

bug 链（修前）:
  design done → auto review → reviewer approve → unlock dev → user 没看设计
bug 修后（PR-6）:
  design done → 等 user 显式 confirm 才进 done → user 看过设计才进开发
"""
from __future__ import annotations

import time
import uuid

import pytest

from agentboard.core.common.enums import ItemType, Status, StatusReason
from agentboard.core.infrastructure.messaging import rabbitmq as mq_mod
from agentboard.features.projects.models import Agent, AgentInstance, Worker
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
def test_pr7_happy_path_design_to_user_confirm_to_unlock(
    db_session, client, broker,
):
    """PR-7：Design 任务 end-to-end 走通 user confirm 路径。"""
    # 1. setup
    user_id, project_id = setup_user_project(db_session, role="owner")
    story_id = setup_story(db_session, project_id)
    token = login_token(client, db_session, user_id)
    H = auth_headers(token)
    # T3.1 dispatch 会在 dev unlock 时自动派发，owner 名下没有 agent → blocked
    # （commit 99aaa39 / T3.1 之后）。注册 Agent + active Worker + online
    # AgentInstance 三件套让 dispatch 真正有候选可派，dev 才能保持 todo 进入
    # unlocked 列表。last_heartbeat 必须设最近（不设会被
    # expire_stale_*_heartbeats 置 inactive）。
    from agentboard.core.common.models import utc_now
    now = utc_now()
    db_session.add(Worker(worker_id=f"pr7-w-{user_id}",
                          hostname="pr7-test", status="active",
                          last_heartbeat=now))
    db_session.flush()
    db_session.add(Agent(agent_id=f"pr7-{user_id}", name=f"pr7-{user_id}",
                          user_id=user_id, roles="[]",
                          online=True, enabled=True,
                          last_heartbeat=now))
    db_session.flush()
    db_session.add(AgentInstance(worker_id=f"pr7-w-{user_id}",
                                  agent_id=f"pr7-{user_id}",
                                  online=True, enabled=True,
                                  cli_command="echo test",
                                  last_heartbeat=now))
    db_session.commit()

    # 2. 建 design task（默认 needs_human_confirmation=True）+ dev task
    # owner_user_id 必须设置：T1.5 统一执行门（commit c1b7e16）要求
    # task owner != NULL，否则后续 claim / review 会被 fail-closed 拦住。
    # 本测试 c1b7e16 之前 cbabb29 写的，旧行为下只传 assignee_id 就够。
    design_id = task_service.create_task(
        db_session, project_id=project_id, story_id=story_id,
        title=f"设计：{story_id}", type=ItemType.DESIGN.value,
        owner_user_id=user_id, assignee_id=user_id,
    ).id
    dev_id = task_service.create_task(
        db_session, project_id=project_id, story_id=story_id,
        title=f"实现：{story_id}", type=ItemType.DEV.value,
        owner_user_id=user_id, assignee_id=user_id,
        needs_human_confirmation=False,
    ).id
    # dev 依赖 design（设计完才能实现）
    db_session.add(TaskDependency(task_id=dev_id, depends_on_id=design_id))
    db_session.commit()
    db_session.expire_all()

    # 清空 broker（前面 setup 阶段可能产生 0 个 event，但保险起见）
    clear_broker_queues(broker)

    # 3a. 设计者先 claim design（todo → in_progress；PR-7 模拟 assignee 自己开干）
    r = client.post(f"/api/tasks/{design_id}/claim", headers=H)
    assert r.status_code == 200, r.text

    # 3b. 然后 submit-review（in_progress → in_review + 发事件）
    r = client.post(f"/api/tasks/{design_id}/submit-review", headers=H)
    assert r.status_code == 200, r.text
    design = r.json()
    assert design["status"] == Status.IN_REVIEW.value
    assert design["needs_human_confirmation"] is True
    # PR-6 行为：flag=True 不发 internal 事件
    # 等 0.5s 让 workflow_worker 线程跑一下（不会消费任何东西因为 broker 为空）
    time.sleep(0.3)
    internal_events = drain_broker_events(
        broker, "agentboard.workflow.internal",
    )
    assert len(internal_events) == 0, \
        f"PR-6：needs_human_confirmation=True 不应发 internal 事件，实际 {len(internal_events)}"

    # 4. user_confirm design
    r = client.post(
        f"/api/tasks/{design_id}/user_confirm",
        json={"comment": "设计看着 OK，可以开发"},
        headers=H,
    )
    assert r.status_code == 200, r.text
    confirmed = r.json()
    assert confirmed["status"] == Status.DONE.value
    assert confirmed["status_reason"] == StatusReason.COMPLETED.value

    # 5. dev 应该被 unlock
    # PR-10 dispatch（T3.1 之后）会立即把 dev 推到 in_progress（owner 名下
    # 有可运行 agent 时），所以「unlocked」不再用 TODO 列表判断 —— 改成
    # 验 dev 的依赖闭包已清空（get_task_readiness.ready=True）。
    db_session.expire_all()
    dev_task = db_session.get(task_service.Task, dev_id)
    readiness = task_service.get_task_readiness(db_session, dev_task)
    assert readiness["ready"] is True, \
        f"dev {dev_id} 设计完成后应可开工，实际 readiness={readiness}"

    # 6. comment 留 trail
    from agentboard.features.work_items.models import Comment
    db_session.expire_all()
    comments = db_session.query(Comment).filter(
        Comment.task_id == design_id,
    ).all()
    assert any("设计看着 OK" in (c.content or "") for c in comments), \
        f"comment 没留 trail: {[c.content for c in comments]}"

    # 7. 验事件 + dispatch 结果：
    # - broadcast 至少 1 条 task.reviewed（user_confirm 路径）
    # - dev 被 dispatch 后状态 in_progress（PR-10 直派，无 task.available 广播）
    # PR-10：task.assigned 发到 workflow.agent.{worker_id} 队列；测试用
    # 临时 worker_id 在 happy_path conftest 没声明，所以不在这里验。
    # 真实部署里这个事件由 .NET worker 消费，dispatch 成功的核心证据是
    # dev 已经成功入 in_progress + assignment_deferred_reason 为空。
    time.sleep(0.3)
    broadcast_msgs = drain_broker_events(
        broker, "agentboard.workflow.broadcast",
    )
    assert len(broadcast_msgs) >= 1, \
        f"应至少有 EVENT_TASK_REVIEWED broadcast，实际 {len(broadcast_msgs)}"
    reviewed_found = any(
        mq_mod.WorkflowMessage.from_bytes(body).event == "task.reviewed"
        for body in broadcast_msgs
    )
    assert reviewed_found, "user_confirm 应发 task.reviewed"

    # PR-10：dev 已被 dispatch_implementation_task 推到 in_progress
    db_session.expire_all()
    dev_after = db_session.get(task_service.Task, dev_id)
    assert dev_after.status == Status.IN_PROGRESS.value, (
        f"PR-10 dispatch 后 dev 应 in_progress，实际 {dev_after.status}")
    assert dev_after.assignment_deferred_reason is None, (
        f"PR-10 dispatch 失败 deferred 原因：{dev_after.assignment_deferred_reason}")

    # 8. 验 internal_queue 仍然空（PR-6 + PR-4 综合：user_confirm 不触发
    # Python workflow_worker 任何动作）
    time.sleep(0.2)
    final_internal = drain_broker_events(
        broker, "agentboard.workflow.internal",
    )
    assert len(final_internal) == 0, \
        f"happy path 完成，internal_queue 仍应空，实际 {len(final_internal)}"
