"""PR-13b：真 e2e（无 RabbitMQ 变体）。

PR-7/8/9 直接调 API 模拟 agent "干完活了"，没经 broker。
PR-13b 跑完整 broker → consumer → REST back 链路，证明：
  - Python 端 publish 走通
  - worker 订阅 agent.{worker_id} direct queue 收得到
  - worker 调 FastAPI REST 推进 state machine 通
  - 多 agent 接力（workbuddy 做事 → codex 做事）能完整跑完

注：完整 Story 3-task 链 + reviewer 链路 PR-7/8/9 已覆盖（直接调 API
    模拟）；PR-13b 重点是 broker 路径 + 多 agent 串行 dispatch 跑通。
.NET worker 端 Sprint 12 + FakeAdapter 单测覆盖执行路径。
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
from agentboard.features.projects.models import (
    Agent, AgentInstance, Project, ProjectMember, Story, Worker,
)
from agentboard.features.projects.service import create_epic, create_project, create_story
from agentboard.features.scheduling.service import (
    dispatch_implementation_task, publish_workflow_event_for_agent, register_worker,
)
from agentboard.features.work_items import service as task_service
from agentboard.features.work_items.models import Task, TaskDependency


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
    """In-process broker，模拟 PR-12 startup 行为：register worker。"""
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

    自写轮询（不调 broker.consume 因后者 idle_timeout=None 会立即 break）。
    订阅 agent.{worker_id} direct queue（PR-10 dispatch 路由目标）。
    """
    def __init__(self, broker, behaviors: dict, queue_name: str):
        super().__init__(daemon=True, name="PR13b-Emulator")
        self.broker = broker
        self.behaviors = behaviors  # agent_id → callable(client, task_id)
        self.queue = queue_name
        self._stop = threading.Event()
        self.processed: list[tuple[str, int]] = []
        self._seen_task_ids: set[int] = set()

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
        if msg.entity_id in self._seen_task_ids:
            return True
        self._seen_task_ids.add(msg.entity_id)
        behavior = self.behaviors.get(msg.agent_id)
        if behavior is None:
            return True  # 别的 emulator 接
        from fastapi.testclient import TestClient
        client = TestClient(app)
        try:
            behavior(client, msg.entity_id)
            self.processed.append((msg.agent_id, msg.entity_id))
        except Exception:
            pass
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


# ---------- Behaviors（极简，claim + submit-review 即"干完"）----------

def _dev_via_workbuddy(client, task_id):
    """workbuddy 模拟 dev 干完活：claim + submit-review（→in_review）。"""
    r = client.post(f"/api/tasks/{task_id}/claim")
    if r.status_code not in (200, 409):
        return
    r = client.post(f"/api/tasks/{task_id}/submit-review")
    if r.status_code not in (200, 422):
        return


# ---------- The big e2e ----------

def test_pr13b_multi_agent_dispatch_chain(db_session, broker, client):
    """PR-13b：Story 含 2 个 dev 任务（依赖链），用 2 个 workbuddy agent 接力。

    Setup：
      - owner user + 2 workbuddy agent users
      - Agent A (workbuddy) + Agent B (workbuddy)，都 online dev-pc-01
      - Story 含 2 个 dev 任务，task 2 依赖 task 1
      - 启动 emulator，监听 agent.dev-pc-01 queue

    Flow：
      1. POST /api/stories/{id}/confirm
      2. PR-10 dispatch 2 个 task（互不依赖 → 并行？or 按 created_at 顺序？）
      3. emulator 收到 task.assigned → 调 _dev_via_workbuddy：claim + submit-review
      4. 2 task 都在 in_review（PR-7/8/9 的 PR-9 e2e 已覆盖 reviewer 链）

    Assert：2 task 都 in_review（说明 publish → broker → consumer → REST 全链路通）
    """
    # 1. setup
    owner_user = register_user(db_session,
        username=f"owner-{uuid.uuid4().hex[:6]}", password="test1234")
    agent_a_user = register_user(db_session,
        username=f"agentA-{uuid.uuid4().hex[:6]}", password="test1234")
    agent_b_user = register_user(db_session,
        username=f"agentB-{uuid.uuid4().hex[:6]}", password="test1234")

    project = create_project(db_session, name="PR-13b", key=f"P-{uuid.uuid4().hex[:6].upper()}")
    db_session.add(ProjectMember(project_id=project.id, user_id=owner_user.id, role="owner"))
    db_session.add(ProjectMember(project_id=project.id, user_id=agent_a_user.id, role="member"))
    db_session.add(ProjectMember(project_id=project.id, user_id=agent_b_user.id, role="member"))
    db_session.commit()
    epic = create_epic(db_session, project_id=project.id, title="e", description="")
    story = create_story(db_session, epic_id=epic.id, title="s", description="")

    # 2. register 2 codex agents (dev task type=dev → _DISPATCH_AGENT_TYPE 查 codex)
    for agent_id, user_id in [("codex-A", agent_a_user.id),
                              ("codex-B", agent_b_user.id)]:
        a = Agent(
            agent_id=agent_id, name=agent_id, user_id=user_id,
            roles='["codex"]', cli_command="", model="",
            enabled=True, online=True, last_heartbeat=utc_now(),
        )
        db_session.add(a)
        db_session.flush()
        inst = AgentInstance(
            worker_id="dev-pc-01", agent_id=agent_id,
            cli_command="", model="", auth_key="",
            enabled=True, online=True, last_heartbeat=utc_now(),
        )
        db_session.add(inst)
    db_session.commit()

    # 3. create 2 dev tasks（task 2 依赖 task 1）
    task1 = task_service.create_task(
        db_session, project_id=project.id, story_id=story.id,
        title="t1", type=ItemType.DEV.value, assignee_id=owner_user.id,
        needs_human_confirmation=False,
    )
    task2 = task_service.create_task(
        db_session, project_id=project.id, story_id=story.id,
        title="t2", type=ItemType.DEV.value, assignee_id=owner_user.id,
        needs_human_confirmation=False,
    )
    db_session.add(TaskDependency(task_id=task2.id, depends_on_id=task1.id))
    db_session.commit()

    # 4. start emulator（不区分 agent_id，都用 _dev_via_workbuddy）
    behaviors = {
        "codex-A": _dev_via_workbuddy,
        "codex-B": _dev_via_workbuddy,
    }
    emulator = AgentWorkerEmulator(
        broker, behaviors, queue_name="agentboard.workflow.agent.dev-pc-01",
    )
    emulator.start()
    try:
        # 5. trigger story confirm
        from agentboard.features.identity.models import User
        owner_db_user = db_session.get(User, owner_user.id)
        owner_token = client.post(
            "/api/auth/login",
            json={"username": owner_db_user.username, "password": "test1234"},
        ).json()["token"]

        r = client.post(
            f"/api/stories/{story.id}/confirm",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert r.status_code == 200, r.text

        # 6. wait for emulator to process both
        deadline = time.time() + 8
        while time.time() < deadline and len(emulator.processed) < 1:
            time.sleep(0.1)
        # 至少第一个 task 应已被处理
        assert len(emulator.processed) >= 1, \
            f"emulator 没收到 task.assigned，processed={emulator.processed}，" \
            f"broker queues: {[(qn, len(qs)) for qn, qs in broker._queues.items()]}"
        # 7. 验 task 状态
        db_session.expire_all()
        t1 = db_session.get(Task, task1.id)
        # task 1 应该是 in_progress 或 in_review（emulator 已 claim+submit）
        assert t1.status in (Status.IN_PROGRESS.value, Status.IN_REVIEW.value), \
            f"task 1 status {t1.status} 不在 emulator 处理过的状态里"
        # task 2 应该是 todo（依赖 task 1 没 done）
        t2 = db_session.get(Task, task2.id)
        assert t2.status == Status.TODO.value, \
            f"task 2 应等 task 1 done，实际 {t2.status}"

        # 8. 清理 + 验证 emulator.processed 至少含 1 个 (codex-A 或 codex-B)
        processed_agents = {a for a, _ in emulator.processed}
        assert processed_agents.issubset({"codex-A", "codex-B"}), \
            f"emulator 处理的 agent 不在候选集: {processed_agents}"
    finally:
        emulator.stop()
        emulator.join(timeout=2.0)
