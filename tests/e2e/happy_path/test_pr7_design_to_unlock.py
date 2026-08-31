"""PR-7 Happy Path E2E: Proposal → Design → User Confirm → Dev Unlock。

链路：
  1. 建 story + design task (needs_human_confirmation=True) + dev task
  2. 设计者 submit-review design → in_review
     验：PR-6 行为 —— 没发 internal event，Python workflow_worker 看不到
  3. 用户 POST /api/tasks/{design_id}/user_confirm
     → 设计 done, comment 留 trail, dev task 被 unlock
  4. 验：design 状态=done + status_reason=completed
     dev 在 get_unlocked_dependent_tasks 返回里
  5. 验：EVENT_TASK_REVIEWED + EVENT_TASK_AVAILABLE 已 publish

bug 链（修前）：
  design done → auto review → reviewer approve → unlock dev → user 没看设计
bug 修后（PR-6）：
  design done → 等 user 显式 confirm 才进 done → user 看过设计才进开发
"""
from __future__ import annotations

import time
import uuid

import pytest

from agentboard.core.common.enums import ItemType, Status, StatusReason
from agentboard.core.infrastructure.messaging import rabbitmq as mq_mod
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

    # 2. 建 design task（默认 needs_human_confirmation=True）+ dev task
    design_id = task_service.create_task(
        db_session, project_id=project_id, story_id=story_id,
        title=f"设计：{story_id}", type=ItemType.DESIGN.value,
        assignee_id=user_id,
    ).id
    dev_id = task_service.create_task(
        db_session, project_id=project_id, story_id=story_id,
        title=f"实现：{story_id}", type=ItemType.DEV.value,
        assignee_id=user_id,
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
    db_session.expire_all()
    unlocked = task_service.get_unlocked_dependent_tasks(db_session, design_id)
    assert any(t.id == dev_id for t in unlocked), \
        f"dev task {dev_id} 没在 unlocked 列表 {[t.id for t in unlocked]}"

    # 6. comment 留 trail
    from agentboard.features.work_items.models import Comment
    db_session.expire_all()
    comments = db_session.query(Comment).filter(
        Comment.task_id == design_id,
    ).all()
    assert any("设计看着 OK" in (c.content or "") for c in comments), \
        f"comment 没留 trail: {[c.content for c in comments]}"

    # 7. 验事件：EVENT_TASK_REVIEWED broadcast + EVENT_TASK_AVAILABLE 至少
    # （EVENT_TASK_REVIEWED 是 PR-4 保留的 broadcast 审计事件，PR-7 user_confirm 也发）
    time.sleep(0.3)
    broadcast_msgs = drain_broker_events(
        broker, "agentboard.workflow.broadcast",
    )
    # broadcast 至少包含 task.reviewed（reviewer 或 user 路径都发）
    # EVENT_TASK_AVAILABLE 走 broadcast 也算上
    assert len(broadcast_msgs) >= 1, \
        f"应至少有 EVENT_TASK_REVIEWED broadcast，实际 {len(broadcast_msgs)}"
    # 至少有一条 EVENT_TASK_REVIEWED
    reviewed_found = False
    available_found = False
    for body in broadcast_msgs:
        msg = mq_mod.WorkflowMessage.from_bytes(body)
        if msg.event == "task.reviewed":
            reviewed_found = True
        if msg.event == "task.available":
            available_found = True
    assert reviewed_found, "user_confirm 应发 task.reviewed"
    # task.available 给 dev 发（dependency unlock 触发）
    assert available_found, "dev 被 unlock 应发 task.available"

    # 8. 验 internal_queue 仍然空（PR-6 + PR-4 综合：user_confirm 不触发
    # Python workflow_worker 任何动作）
    time.sleep(0.2)
    final_internal = drain_broker_events(
        broker, "agentboard.workflow.internal",
    )
    assert len(final_internal) == 0, \
        f"happy path 完成，internal_queue 仍应空，实际 {len(final_internal)}"
