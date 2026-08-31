"""Happy Path E2E 共享 fixture（PR-7/8/9 共用）。

不调 .NET 也不调真实 CLI（codex / workbuddy / minimax 都不在测试机），
全链路在 Python 进程内：
- FastAPI TestClient 触发 REST 端点
- InMemoryWorkflowBroker 替代 RabbitMQ
- 内嵌一个 workflow_worker 线程在同进程跑（消费 internal_queue）
- Agent 执行用 fake replace：直接调 submit_for_review / review 端点
  模拟 CLI "做完活了"
- 断言：state transitions + 事件流 + dependency unlock + comment

fixture 互不污染（per-test in-memory SQLite + 清干净 broker）。
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Alembic multiple heads → init_db() 挂；不依赖 alembic，用 create_all
import pytest

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# 测试环境常量
os.environ.setdefault("AGENTBOARD_REQUIRE_AUTH", "0")
os.environ.setdefault("AGENTBOARD_ALLOW_REGISTRATION", "1")
os.environ.setdefault("AGENTBOARD_SECRET", "x" * 64)  # 32+ bytes 避免 dev 告警

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard.api import app  # noqa: E402
from agentboard.core.common.models import Base  # noqa: E402
from agentboard.core.infrastructure.messaging import rabbitmq as mq_mod  # noqa: E402
from agentboard.features.identity.service import register_user  # noqa: E402
from agentboard.features.projects.service import (  # noqa: E402
    create_project,
    create_story,
    create_epic,
)


# ---------- module-level：替换 process 级 WorkflowPublisher ----------

@pytest.fixture
def broker():
    """单测 InMemoryWorkflowBroker + bind 给 WorkflowPublisher 单例。

    Pytest 跨 case 不共享 broker（每个 case 新建）—— 清空状态。
    """
    b = mq_mod.InMemoryWorkflowBroker()
    b.declare_topology()
    b.declare_agent_queue("workbuddy")
    b.declare_agent_queue("codex")
    publisher = mq_mod.WorkflowPublisher(broker=b)
    mq_mod.set_workflow_publisher(publisher)
    # 同时把 set_workflow_publisher 注入到主模块（publish_workflow_event 一行式入口）
    yield b
    mq_mod.set_workflow_publisher(None)


# ---------- per-test：FastAPI app + in-memory DB ----------

@pytest.fixture
def app_engine(broker):
    """建内存 SQLite + create_all + 注入到 module-level SessionLocal。
    broker fixture 先于 engine 触发，保证 publish 走我们的 broker。
    """
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
def client(app_engine):
    return TestClient(app)


# ---------- workflow_worker 后台线程（PR-4 internal_queue 消费） ----------

@pytest.fixture
def workflow_worker_thread(broker):
    """起一个 workflow_worker 线程，订阅 internal_queue。

    模拟生产部署的 Python workflow_worker。
    """
    from agentboard.workflow_worker import WorkflowConsumer, WorkflowConsumerConfig
    cfg = WorkflowConsumerConfig(
        api_url="http://test-internal",  # 不会真用，测试直接调端点
        token=None,
        poll_interval=0.1,
    )
    consumer = WorkflowConsumer(cfg, client=_NoopClient())
    stop = threading.Event()
    # 直接消费 internal_queue（PR-4 改完后的路径）
    topology = mq_mod.WorkflowTopology()
    t = threading.Thread(
        target=lambda: broker.consume(
            topology.internal_queue, consumer.handle_message,
            idle_timeout=0.1, stop=stop,
        ),
        daemon=True,
        name="workflow-worker-test",
    )
    t.start()
    # 给 worker 时间起来
    time.sleep(0.05)
    yield consumer
    stop.set()
    t.join(timeout=2.0)


class _NoopClient:
    """workflow_worker 调 REST API 的 stub。E2E 不走 REST 调 assignee，
    而是测试代码直接调端点驱动。"""
    def request(self, *a, **kw):
        class _R:
            status_code = 200
            text = "{}"
            def json(self): return {}
        return _R()


# ---------- 实用工具 ----------

def login_token(client: TestClient, db_session, user_id: int) -> str:
    """登入拿 token。"""
    from agentboard.features.identity.models import User
    u = db_session.query(User).filter(User.id == user_id).one()
    r = client.post("/api/auth/login",
                    json={"username": u.username, "password": "test1234"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def setup_user_project(db_session, role: str = "owner") -> tuple[int, int]:
    """建 user + project（user 是 owner member）。"""
    username = f"u-{uuid.uuid4().hex[:8]}"
    u = register_user(db_session, username=username, password="test1234")
    p = create_project(
        db_session,
        name="happy path project",
        key=f"P-{uuid.uuid4().hex[:6].upper()}",
    )
    from agentboard.features.projects.models import ProjectMember
    db_session.add(ProjectMember(project_id=p.id, user_id=u.id, role=role))
    db_session.commit()
    return u.id, p.id


def setup_story(db_session, project_id: int) -> int:
    e = create_epic(db_session, project_id=project_id, title="e", description="")
    s = create_story(db_session, epic_id=e.id, title="happy path story", description="")
    return s.id


def drain_broker_events(broker, queue_name: str) -> list:
    """从 broker 拿出全部事件 body。"""
    out = []
    for body in broker._queues.get(queue_name, []):  # type: ignore[attr-defined]
        out.append(body)
    return out


def clear_broker_queues(broker):
    """每个 step 间清空 broker queue（保留下一步要 observe 的新事件）。"""
    for q in list(broker._queues.keys()):  # type: ignore[attr-defined]
        broker._queues[q].clear()  # type: ignore[attr-defined]
    broker._dead.clear()  # type: ignore[attr-defined]
