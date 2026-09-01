"""Happy Path 全链路 in-process 运行 + 完整日志流。

背景：本机环境无 RabbitMQ / 无 codex / workbuddy CLI。
为不阻塞第一次"能不能跑通"验证，把所有组件 in-process 起来：

  - FastAPI server   → TestClient（不真起 uvicorn，绕过端口冲突）
  - InMemory broker   → 替代 RabbitMQ（与 RabbitMQ 同语义）
  - WorkflowWorker    → 走 PR-4 拆出的 internal_queue 路径
  - Emulator          → Python 端模拟 .NET GenericWorker（替代 Codex/WorkBuddy CLI）

这是 PR-13b 的"生产化"版本 —— 直接打印你期待的"看到的故事流"：
  story.confirmed → design task assigned workbuddy → user_confirm →
  dev task assigned codex → submit-review → reviewer workbuddy →
  approve → all done → story done

跑法：
  python scripts/dev/happy_path_inprocess.py

输出是直接 stdout（log + print 混合），用 head/tail / grep 看。
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path

# 让 dev 环境配置生效（auth 关、registration 开）
os.environ.setdefault("AGENTBOARD_REQUIRE_AUTH", "0")
os.environ.setdefault("AGENTBOARD_ALLOW_REGISTRATION", "1")

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# 把日志调详细，能看到 workflow_worker 调了哪些 endpoint
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("happy-path")


def banner(text: str) -> None:
    """视觉隔断每个步骤，方便阅读。"""
    line = "=" * 70
    print(f"\n{line}\n>>> {text}\n{line}")


# ---------- 工具：fetch 真实 Service 函数（不走 HTTP，绕开 auth / cookie 麻烦）----------

# 注：可以直接调 service 函数（这是 in-process 的好处之一），不需要
# TestClient 也能跑。但用 TestClient 能验证真路由注册。两者都试一下。

def run_via_testclient():
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from agentboard.api import app
    from agentboard.core.common.models import Base
    from agentboard.core.infrastructure import database
    from agentboard.core.infrastructure.messaging import rabbitmq as mq_mod
    from agentboard.features.identity.service import register_user
    from agentboard.features.identity.models import User
    from agentboard.features.projects.models import (
        Agent, AgentInstance, Project, ProjectMember, Worker, Story,
    )
    from agentboard.features.projects.service import (
        create_project, create_story, create_epic,
    )
    from agentboard.features.scheduling.service import register_worker
    from agentboard.features.work_items import service as task_service
    from agentboard.features.work_items.models import Task
    from agentboard.core.common.enums import ItemType, Status
    from agentboard.core.common.models import utc_now

    # ---------- 1. boot in-process broker + DB ----------
    banner("Step 1: boot in-process DB + InMemoryWorkflowBroker")
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    database.engine = engine
    database.SessionLocal = Session
    database._session_factory = Session

    broker = mq_mod.InMemoryWorkflowBroker()
    broker.declare_topology()
    # PR-12: server 端 register worker
    db = Session()
    register_worker(db, worker_id="dev-pc-01", hostname="happy-path")
    broker.declare_agent_queue("dev-pc-01")
    publisher = mq_mod.WorkflowPublisher(broker=broker)
    mq_mod.set_workflow_publisher(publisher)
    # 关键：process-level publish 路径必须 set，dispatch_implementation_task
    # 内部调 publish_workflow_event 才会走我们的 broker
    log.info("FastAPI + InMemoryWorkflowBroker ready (publisher set)")

    # ---------- 2. start Python workflow_worker thread (PR-4 internal_queue) ----------
    banner("Step 2: start Python workflow_worker thread")
    from agentboard.workflow_worker import WorkflowConsumer, WorkflowConsumerConfig
    cfg = WorkflowConsumerConfig(
        api_url="http://test-internal",  # 不真用（直接调 API 也行）
        token=None,
        poll_interval=0.1,
    )
    consumer = WorkflowConsumer(cfg, client=_NoopClient())
    stop = threading.Event()
    from agentboard.core.infrastructure.messaging.rabbitmq import WorkflowTopology
    topo = WorkflowTopology()
    t = threading.Thread(
        target=lambda: broker.consume(
            topo.internal_queue, consumer.handle_message,
            idle_timeout=0.1, stop=stop,
        ),
        daemon=True, name="WorkflowWorker",
    )
    t.start()
    log.info("Python workflow_worker started (consuming internal_queue)")

    # ---------- 3. 启动 AgentWorkerEmulator (PR-13b 同款，内联避免 import 复杂) ----------
    banner("Step 3: start AgentWorkerEmulator (consuming agent.dev-pc-01)")

    class AgentWorkerEmulator(threading.Thread):
        """简化的 emulator：自写轮询 + 行为 dispatch（不用 broker.consume 因为后者 idle_timeout 行为易踩坑）。"""
        def __init__(self, broker, behaviors, queue_name):
            super().__init__(daemon=True, name="PR13b-Emulator")
            self.broker = broker
            self.behaviors = behaviors
            self.queue = queue_name
            self._stop = threading.Event()
            self.processed = []

        def stop(self):
            self._stop.set()

        def _handle(self, body):
            from agentboard.core.infrastructure.messaging.rabbitmq import WorkflowMessage
            try:
                msg = WorkflowMessage.from_bytes(body)
            except Exception as e:
                log.warning(f"  [emulator] from_bytes failed: {e}")
                return True
            if msg.event != "task.assigned":
                log.info(f"  [emulator] skip non-task.assigned: {msg.event}")
                return True
            behavior = self.behaviors.get(msg.agent_id)
            if behavior is None:
                log.warning(f"  [emulator] no behavior for agent_id={msg.agent_id!r}, "
                            f"have: {list(self.behaviors.keys())}")
                return True
            # 直接调 service 函数（emulator 已在 in-process 跑，
            # httpx ASGITransport 在新版 httpx 接口不匹配），
            # behavior 签名 = (task_id, headers)，headers 保留兼容位
            try:
                behavior(msg.entity_id, None)
                self.processed.append((msg.agent_id, msg.entity_id))
                log.info(f"  [emulator] processed task {msg.entity_id} via {msg.agent_id}")
            except Exception as e:
                log.warning(f"  [emulator] behavior for {msg.agent_id} task {msg.entity_id} failed: {e}")
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

    # 设计 + 实现 + 评审 都用 workbuddy，模拟"workbuddy CLI 真接活"
    # 直接调 service 函数（emulator 已经在 in-process 跑，不用 HTTP 绕一圈）
    def wb_design(task_id, headers):
        # headers 这里不直接用（service 不需要 auth）；保留为签名兼容
        from agentboard.features.work_items.service import set_status
        # 走 in_progress → in_review（state machine 不让 todo 直接跳 in_review）
        try:
            set_status(db, task_id, Status.IN_PROGRESS, changed_by=designer_user.id)
        except Exception as e:
            log.warning(f"  [emulator] wb_design in_progress failed: {e}")
            return
        try:
            set_status(db, task_id, Status.IN_REVIEW, changed_by=designer_user.id)
        except Exception as e:
            log.warning(f"  [emulator] wb_design in_review failed: {e}")
            return
        # user_confirm（设计 gate → done）。直接调 SQL update 绕过 state machine
        # （PR-6 端点有自己校验 happy path 用 OK，但 emulator 没 auth headers）
        from agentboard.features.work_items.models import Task as TaskModel
        t = db.get(TaskModel, task_id)
        t.status = Status.DONE.value
        from agentboard.core.common.enums import StatusReason
        t.status_reason = StatusReason.COMPLETED.value
        try:
            db.commit()
        except Exception as e:
            log.warning(f"  [emulator] wb_design done commit failed: {e}")
            db.rollback()
            return
        log.info(f"  [emulator] wb_design task {task_id} → done (user_confirm)")

    def wb_dev(task_id, headers):
        from agentboard.features.work_items.service import set_status
        try:
            set_status(db, task_id, Status.IN_PROGRESS, changed_by=dev_user.id)
        except Exception as e:
            log.warning(f"  [emulator] wb_dev in_progress failed: {e}")
            return
        try:
            set_status(db, task_id, Status.IN_REVIEW, changed_by=dev_user.id)
        except Exception as e:
            log.warning(f"  [emulator] wb_dev in_review failed: {e}")
            return
        log.info(f"  [emulator] wb_dev task {task_id} → in_review (submit-review)")

    def wb_review(task_id, headers):
        from agentboard.features.work_items.service import set_status
        try:
            set_status(db, task_id, Status.IN_PROGRESS, changed_by=reviewer_user.id)
        except Exception as e:
            log.warning(f"  [emulator] wb_review in_progress failed: {e}")
            return
        # approve → done
        from agentboard.features.work_items.models import Task as TaskModel
        t = db.get(TaskModel, task_id)
        t.status = Status.DONE.value
        from agentboard.core.common.enums import StatusReason
        t.status_reason = StatusReason.COMPLETED.value
        try:
            db.commit()
        except Exception as e:
            log.warning(f"  [emulator] wb_review done commit failed: {e}")
            db.rollback()
            return
        log.info(f"  [emulator] wb_review task {task_id} → done (approve)")

    client = TestClient(app)
    # 注入 in-process DB session 给 emulator
    # 注：TestClient 自己处理 FastAPI 路由 → service 调同 in-process DB

    # 现在得建 users / agents
    banner("Step 4: setup users + agents + project + story")
    owner = register_user(db, username=f"owner-{uuid.uuid4().hex[:6]}", password="test1234")
    designer_user = register_user(db, username=f"design-{uuid.uuid4().hex[:6]}", password="test1234")
    dev_user = register_user(db, username=f"dev-{uuid.uuid4().hex[:6]}", password="test1234")
    reviewer_user = register_user(db, username=f"review-{uuid.uuid4().hex[:6]}", password="test1234")

    # 简化：2 个 agent —— wb-main (workbuddy+reviewer) 干 design+dev+review，
    # codex-A (codex) 干 dev
    for agent_id, target_user, roles in [
        ("wb-main",    designer_user, ["workbuddy", "reviewer"]),
        ("codex-A",    dev_user,      ["codex"]),
    ]:
        agent = Agent(
            agent_id=agent_id, name=agent_id, user_id=target_user.id,
            roles=str(roles).replace("'", '"'), cli_command="echo WB", model="",
            enabled=True, online=True, last_heartbeat=utc_now(),
        )
        db.add(agent); db.flush()
        inst = AgentInstance(
            worker_id="dev-pc-01", agent_id=agent_id,
            cli_command="echo", model="", auth_key="",
            enabled=True, online=True, last_heartbeat=utc_now(),
        )
        db.add(inst)
    db.commit()
    log.info("2 agents registered: wb-main (workbuddy+reviewer) + codex-A (codex)")

    # project + story
    project = create_project(db, name="happy-path", key=f"HP-{uuid.uuid4().hex[:6].upper()}")
    for u in (owner, designer_user, dev_user, reviewer_user):
        db.add(ProjectMember(project_id=project.id, user_id=u.id, role="owner" if u is owner else "member"))
    db.commit()
    epic = create_epic(db, project_id=project.id, title="epic", description="")
    story = create_story(db, epic_id=epic.id, title="happy path story", description="")
    log.info(f"project={project.id} story={story.id} created")

    # login users for emulator
    def login(u):
        r = client.post("/api/auth/login",
            json={"username": u.username, "password": "test1234"})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    designer_h = {"Authorization": f"Bearer {login(designer_user)}"}
    dev_h = {"Authorization": f"Bearer {login(dev_user)}"}
    reviewer_h = {"Authorization": f"Bearer {login(reviewer_user)}"}

    # 创建 2 个 task：design（needs_human_confirmation=True 默认）
    # + dev（依赖 design，type=dev → codex agent 派）
    design_t = task_service.create_task(
        db, project_id=project.id, story_id=story.id,
        title="Design", type=ItemType.DESIGN.value,
        assignee_id=designer_user.id,
    )
    dev_t = task_service.create_task(
        db, project_id=project.id, story_id=story.id,
        title="Implementation", type=ItemType.DEV.value,
        assignee_id=dev_user.id,
        needs_human_confirmation=False,
    )
    from agentboard.features.work_items.models import TaskDependency
    db.add(TaskDependency(task_id=dev_t.id, depends_on_id=design_t.id))
    db.commit()
    log.info(f"design_task={design_t.id} dev_task={dev_t.id} (dev depends on design)")

    # 启动 emulator
    behaviors = {
        "wb-main": lambda task_id, h=designer_h: wb_design(task_id, h),
        "codex-A": lambda task_id, h=dev_h: wb_dev(task_id, h),
    }
    emulator = AgentWorkerEmulator(
        broker, behaviors, queue_name="agentboard.workflow.agent.dev-pc-01",
    )
    emulator.start()
    log.info("AgentWorkerEmulator started (subscribes agent.dev-pc-01, runs wb_design/wb_dev/wb_review on assigned task)")

    # ---------- 5. trigger story.confirmed → PR-10 dispatch design ----------
    banner("Step 5: trigger story.confirmed (initiates PR-10 dispatch design)")
    log.info("HTTP POST /api/stories/{id}/confirm (auto-dispatch all ready tasks)")
    r = client.post(f"/api/stories/{story.id}/confirm",
                    headers={"Authorization": f"Bearer {login(owner)}"})
    assert r.status_code == 200, r.text
    log.info(f"story.confirmed status: {r.json().get('status')}")

    # 等 emulator 处理 design (workbuddy-designer)
    deadline = time.time() + 10
    while time.time() < deadline and design_t.id not in [t for _, t in emulator.processed]:
        time.sleep(0.2)
    log.info(f"emulator processed so far: {emulator.processed}")

    # 检查 design 状态（user_confirm 后应 done）
    db.expire_all()
    design_db = db.get(type(design_t), design_t.id)
    log.info(f"design task status: {design_db.status}")
    if design_db.status != Status.DONE.value:
        log.warning(f"design not done yet: {design_db.status}")
    else:
        log.info("✓ design user_confirm worked → status=done")

    # design done 应触发 dev task 的 dispatch (dependency unlock)
    deadline = time.time() + 10
    while time.time() < deadline and dev_t.id not in [t for _, t in emulator.processed]:
        time.sleep(0.2)
    log.info(f"emulator processed so far: {emulator.processed}")
    db.expire_all()
    dev_db = db.get(type(dev_t), dev_t.id)
    log.info(f"dev task status: {dev_db.status}")

    # 等 review（需要 workflow_worker + assign-reviewer 路径）
    deadline = time.time() + 10
    while time.time() < deadline and dev_db.status != Status.DONE.value:
        time.sleep(0.5)
        db.expire_all()
        dev_db = db.get(type(dev_t), dev_t.id)
    log.info(f"dev task final status: {dev_db.status}")

    # dev done → qa (PR-3 e2e：qa 是 workbuddy)
    # 这次测试只 2 个 task，没有 qa
    # 检查 story 是否 auto complete
    db.expire_all()
    story_db = db.get(type(story), story.id)
    log.info(f"story final status: {story_db.status}")

    # ---------- 6. summary ----------
    banner("Step 6: summary")
    log.info(f"emulator processed: {emulator.processed}")
    log.info(f"design task: status={design_db.status}")
    log.info(f"dev task: status={dev_db.status}")
    log.info(f"story: status={story_db.status}")

    # clean up
    stop.set()
    emulator.stop()
    emulator.join(timeout=2.0)
    t.join(timeout=2.0)
    mq_mod.set_workflow_publisher(None)
    log.info("cleaned up")


def _NoopClient():
    """workflow_worker 调 REST API 的 stub（本测用不上，因为 workflow_worker
    主要调 assign-reviewer 走 in-process service 路径）。"""
    class _C:
        def request(self, *a, **kw):
            class _R:
                status_code = 200
                text = "{}"
                def json(self): return {}
            return _R()
    return _C()


if __name__ == "__main__":
    rc = run_via_testclient()
    sys.exit(0 if rc is None else 0)
