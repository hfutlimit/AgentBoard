"""PR-13b：真 e2e（无 RabbitMQ 变体）。

PR-7/8/9 直接调 API 模拟 agent "干完活了"，没经 broker。
PR-13b 跑完整 broker → consumer → REST back 链路，证明：
  - publish 走通
  - worker 订阅 agent.{worker_id} direct queue 收得到
  - worker 调 FastAPI REST 推进 state machine 通（PR-10 P0-1 修复后
    submit-review 必须 200，task 必须 in_review）

注：完整 3-task Story 链 + reviewer 链 PR-7/8/9 已覆盖。
    PR-13b 重点是 broker 路径 + 多 agent 串行 dispatch 跑通。
.NET worker 端 Sprint 12 单测 + FakeAdapter 覆盖执行路径。
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("AGENTBOARD_REQUIRE_AUTH", "0")
os.environ.setdefault("AGENTBOARD_ALLOW_REGISTRATION", "1")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard.api import app
from agentboard.core.common.enums import ItemType, Status
from agentboard.core.common.models import Base, utc_now
from agentboard.core.infrastructure import messaging as mq_mod
from agentboard.features.identity.service import register_user
from agentboard.features.identity.models import User as UserModel
from agentboard.features.projects.models import (
    Agent, AgentInstance, Project, ProjectMember,
)
from agentboard.features.projects.service import create_project, create_story
from agentboard.features.scheduling.service import register_worker
from agentboard.features.work_items import service as task_service
from agentboard.features.work_items.models import Task


# ---------- Fixtures ----------

@pytest.fixture
def app_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    from agentboard.core.infrastructure import database
    database.engine = engine
    database.SessionLocal = Session
    database._session_factory = Session
    return engine


@pytest.fixture
def db_session(app_engine):
    Session = sessionmaker(bind=app_engine)
    s = Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def broker(db_session):
    b = mq_mod.InMemoryWorkflowBroker()
    b.declare_topology()
    register_worker(db_session, worker_id="dev-pc-01", hostname="test-emulator")
    b.declare_agent_queue("dev-pc-01")
    publisher = mq_mod.WorkflowPublisher(broker=b)
    mq_mod.set_workflow_publisher(publisher)
    yield b
    mq_mod.set_workflow_publisher(None)


@pytest.fixture
def client(app_engine):
    return TestClient(app)


# ---------- AgentWorkerEmulator ----------
class AgentWorkerEmulator(threading.Thread):
    """PR-13b：Python 端 agent worker emulator。

    自写轮询（broker.consume idle_timeout=None 会立即 break）。
    """
    def __init__(self, broker, behaviors: dict, queue_name: str):
        super().__init__(daemon=True, name="PR13b-Emulator")
        self.broker = broker
        self.behaviors = behaviors
        self.queue = queue_name
        self._stop = threading.Event()
        self.processed: list[tuple[str, int]] = []

    def stop(self):
        self._stop.set()

    def _handle(self, body: bytes) -> bool:
        from agentboard.core.infrastructure.messaging.rabbitmq import WorkflowMessage
        try:
            msg = WorkflowMessage.from_bytes(body)
        except Exception:
            return True
        if msg.event != "task.assigned":
            return True
        behavior = self.behaviors.get(msg.agent_id)
        if behavior is None:
            return True
        from fastapi.testclient import TestClient
        c = TestClient(app)
        try:
            behavior(c, msg.entity_id)
            self.processed.append((msg.agent_id, msg.entity_id))
        except Exception as e:
            print(f"  [emulator] behavior for {msg.agent_id} task {msg.entity_id} failed: {e}")
        return True

    def run(self):
        while not self._stop.is_set():
            with self.broker._lock:
                body = self.broker._queues.get(self.queue, [])
                if body:
                    msg_body = body.pop(0)
                else:
                    msg_body = None
            if msg_body is None:
                self._stop.wait(0.1)
                continue
            self._handle(msg_body)


# ---------- Behaviors（PR-13b 收紧：必 in_review，不接受 422）----------

def _codex_dev(client, task_id, headers):
    """codex implementer 模拟「干完活了 + submit-review」（→ in_review）。

    PR-10 P0-1 修复（dispatch 设 assignee_id）后 submit-review 应该 200。
    422 = "only the assignee can submit" 报错，P0-1 回归信号。
    headers：emulator 预登录的 auth header（每次 claim 需 auth）。
    """
    r = client.post(f"/api/tasks/{task_id}/claim", headers=headers)
    assert r.status_code in (200, 409), \
        f"task {task_id} claim 失败：{r.status_code} {r.text}"
    r = client.post(f"/api/tasks/{task_id}/submit-review", headers=headers)
    assert r.status_code == 200, \
        f"task {task_id} submit-review 必须 200（PR-10 P0-1 已修），实际 {r.status_code} {r.text}"


# ---------- The e2e ----------

def test_pr13b_broker_chain_with_p010_assignee_fix(db_session, broker, client):
    """PR-13b：dispatch → broker → consumer → REST 全链路。

    单 dev task（无 dep），不依赖 reviewer 链。
    重点验：emulator 收到 task.assigned 后 submit-review 必须 200 +
    task.status=in_review（PR-10 P0-1 修复后信号）。
    """
    # 1. setup
    owner_user = register_user(db_session,
        username=f"owner-{uuid.uuid4().hex[:6]}", password="test1234")
    agent_a_user = register_user(db_session,
        username=f"codexA-{uuid.uuid4().hex[:6]}", password="test1234")

    project = create_project(db_session, name="PR-13b",
        key=f"P-{uuid.uuid4().hex[:6].upper()}")
    db_session.add(ProjectMember(project_id=project.id, user_id=owner_user.id, role="owner"))
    db_session.add(ProjectMember(project_id=project.id, user_id=agent_a_user.id, role="member"))
    db_session.commit()

    # 不走 create_story（会自动建 2 个 default task），直接建 task
    # story 必须有 epic 先 — 我们走 create_story 但不用它自动建 task
    from agentboard.features.projects.service import create_epic
    epic = create_epic(db_session, project_id=project.id, title="e", description="")
    story = create_story(db_session, epic_id=epic.id, title="s", description="")
    # 但 create_story 会自动建 default tasks 吗？看 docstring 说"自动创建 2 个默认
    # Task (design + dev) "—— 是的，会建。PR-13b 这里我们 dispatch 自己建的那个 dev，
    # 不依赖 story 自动 task chain
    # 把自动建的 2 个 task 删掉（先记录当前 ids）
    auto_tasks = db_session.query(Task).filter(Task.story_id == story.id).all()
    auto_task_ids = [t.id for t in auto_tasks]

    # 2. 单独建一个 dev task（PR-13b focus）
    my_task = task_service.create_task(
        db_session, project_id=project.id, story_id=story.id,
        title="PR-13b dev", type=ItemType.DEV.value, assignee_id=owner_user.id,
        needs_human_confirmation=False,
    )
    my_task_id = my_task.id

    # 3. register codex-A agent
    a = Agent(
        agent_id="codex-A", name="codex-A", user_id=agent_a_user.id,
        roles='["codex"]', cli_command="", model="",
        enabled=True, online=True, last_heartbeat=utc_now(),
    )
    db_session.add(a); db_session.flush()
    inst = AgentInstance(
        worker_id="dev-pc-01", agent_id="codex-A",
        cli_command="", model="", auth_key="",
        enabled=True, online=True, last_heartbeat=utc_now(),
    )
    db_session.add(inst); db_session.commit()

    # 4. login as codex-A's user (emulator needs auth to call /claim etc)
    codex_token = client.post(
        "/api/auth/login",
        json={"username": agent_a_user.username, "password": "test1234"},
    ).json()["token"]
    codex_h = {"Authorization": f"Bearer {codex_token}"}

    # 5. start emulator
    behaviors = {"codex-A": lambda c, tid: _codex_dev(c, tid, codex_h)}
    emulator = AgentWorkerEmulator(
        broker, behaviors, queue_name="agentboard.workflow.agent.dev-pc-01",
    )
    emulator.start()
    try:
        # 5. trigger dispatch：调 dispatch_implementation_task 直接
        # （不走 confirm_story 因为它会先 dispatch 自动 task）
        from agentboard.features.scheduling.service import dispatch_implementation_task
        # 先 dispatch 那些自动建的任务（会走 fallback 因为没 workbuddy agent）
        for t in auto_tasks:
            dispatch_implementation_task(db_session, t.id)
        # 再 dispatch 我们的 dev task
        dispatch_implementation_task(db_session, my_task_id)
        # 5b. 现在用 story confirm 触发剩余 dispatch + dependency unlock 路径
        owner_token = client.post(
            "/api/auth/login",
            json={"username": owner_user.username, "password": "test1234"},
        ).json()["token"]
        # 不调 confirm_story 因为已经手动 dispatch 了。只验状态。
        # 6. wait for emulator to process
        deadline = time.time() + 8
        while time.time() < deadline and len(emulator.processed) < 1:
            time.sleep(0.1)
        # 验 emulator 收到
        assert len(emulator.processed) >= 1, \
            f"emulator 没收到 task.assigned，processed={emulator.processed}，" \
            f"broker queues: {[(qn, len(qs)) for qn, qs in broker._queues.items()]}"
        # 7. 验 task 状态：my_task 必须是 in_review（emulator 模拟 codex 干完活 + submit）
        db_session.expire_all()
        t = db_session.get(Task, my_task_id)
        assert t.status == Status.IN_REVIEW.value, \
            f"my_task status 应是 in_review（PR-10 P0-1 已修），实际 {t.status}"
        # 8. 验 task.assignee_id 被设置（PR-10 P0-1 信号）
        assert t.assignee_id == agent_a_user.id, \
            f"task.assignee_id 应是 codex-A 的 user_id={agent_a_user.id}，" \
            f"实际 {t.assignee_id}"
    finally:
        emulator.stop()
        emulator.join(timeout=2.0)
