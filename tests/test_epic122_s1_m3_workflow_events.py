"""Epic 122 S1 M3：Workflow 事件源接入回归护栏（api.py 四端点发布断言）。

覆盖：
1. create_story → 广播 story.created（broadcast 队列）；
2. assign-reviewer（reviewer 绑定 Agent）→ 定向 review.requested（agent 队列）；
3. review approve → 广播 story.ready；reject → 广播 review.rejected（ref_id=轮次）；
4. story 评论 → 定向 comment.replied（agent 队列）。

实现方式：向进程级 WorkflowPublisher 注入 InMemoryWorkflowBroker，
经 TestClient 直调 REST 端点，断言各队列深度与消息载荷。
"""
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from fastapi.testclient import TestClient  # noqa: E402

from agentboard import api, auth, mq, service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402

init_db()

_NS = "ab.m3.events.test"


@pytest.fixture(scope="module")
def bus():
    """注入 InMemory broker 的发布器，返回 broker + 清理钩子。"""
    broker = mq.InMemoryWorkflowBroker(namespace=_NS)
    broker.declare_topology()
    pub = mq.WorkflowPublisher(config=mq.MQConfig(),  # url 空 → enabled=False
                               broker=broker, namespace=_NS)
    mq.set_workflow_publisher(pub)
    yield broker
    mq.set_workflow_publisher(None)


@pytest.fixture(scope="module")
def seeded():
    """项目 + author/reviewer 用户 + 在线 reviewer Agent（绑定 reviewer 用户）。"""
    with SessionLocal() as s:
        p = service.create_project(s, name="M3 Events P")
        author = service.register_user(s, username="m3-author", password="password123")
        reviewer = service.register_user(s, username="m3-reviewer", password="password123")
        service.add_project_member(s, project_id=p.id, user_id=author.id, role="member")
        service.add_project_member(s, project_id=p.id, user_id=reviewer.id, role="member")
        service.register_agent(
            s, agent_id="wb-m3-reviewer", name="M3ReviewerBot",
            roles='["reviewer"]', capabilities='["backend"]', user_id=reviewer.id,
        )
        service.agent_heartbeat(s, "wb-m3-reviewer", user_id=reviewer.id)
        s.commit()
        return {"project_id": p.id, "author_id": author.id, "reviewer_id": reviewer.id}


def _hdr(user_id: int) -> dict:
    return {"Authorization": f"Bearer {auth.make_token(user_id)}"}


def _wait_depth(broker, queue: str, want: int, timeout: float = 3.0) -> int:
    """轮询等待队列深度达到 want（发布可能异步/延迟）。"""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if broker.queue_depth(queue) >= want:
            return broker.queue_depth(queue)
        time.sleep(0.05)
    return broker.queue_depth(queue)


def _last_msg(broker, queue: str) -> mq.WorkflowMessage | None:
    raw = broker._queues.get(queue) or []
    if not raw:
        return None
    return mq.WorkflowMessage.from_bytes(raw[-1])


# ---------- 1. create_story → story.created 广播 ----------
def test_create_story_publishes_story_created_broadcast(bus, seeded):
    broker = bus
    broker.purge()
    client = TestClient(api.app)
    r = client.post(f"/api/projects/{seeded['project_id']}/epics",
                    json={"title": "M3 Epic"}, headers=_hdr(seeded["author_id"]))
    assert r.status_code in (200, 201)
    eid = r.json()["id"]
    r = client.post(f"/api/epics/{eid}/stories",
                    json={"title": "M3 Story A"}, headers=_hdr(seeded["author_id"]))
    assert r.status_code == 201
    sid = r.json()["id"]
    assert _wait_depth(broker, mq.WorkflowTopology(_NS).broadcast_queue, 1) >= 1
    msg = _last_msg(broker, mq.WorkflowTopology(_NS).broadcast_queue)
    assert msg is not None and msg.event == mq.EVENT_STORY_CREATED
    assert msg.entity_type == "story" and msg.entity_id == sid
    assert msg.ref_id == eid


# ---------- 2. Story 确认（2026-08-09 起取代 assign-reviewer 闸门） ----------
def test_assign_reviewer_deprecated_and_confirm_publishes(bus, seeded):
    """Story 评审已下线：assign-reviewer 返回 422；confirm 发 story.confirmed 广播。"""
    broker = bus
    broker.purge()
    client = TestClient(api.app)
    epic = client.post(f"/api/projects/{seeded['project_id']}/epics",
                       json={"title": "M3 Epic B"}, headers=_hdr(seeded["author_id"])).json()
    st = client.post(f"/api/epics/{epic['id']}/stories",
                     json={"title": "M3 Story B"}, headers=_hdr(seeded["author_id"])).json()
    broker.purge()  # 清掉 create_story 的广播，专注本次断言
    r = client.post(f"/api/stories/{st['id']}/assign-reviewer",
                    headers=_hdr(seeded["author_id"]))
    assert r.status_code == 422, r.text
    assert "评审已下线" in r.json().get("detail", "")
    # 新人工闸门：confirm → story.confirmed（广播）
    r = client.post(f"/api/stories/{st['id']}/confirm",
                    headers=_hdr(seeded["author_id"]))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
    q = mq.WorkflowTopology(_NS).broadcast_queue
    # confirm 后广播：1 × story.confirmed + N × task.available（可认领任务）
    assert _wait_depth(broker, q, 2) >= 2
    raw = broker._queues.get(q) or []
    msgs = [mq.WorkflowMessage.from_bytes(b) for b in raw]
    events = [m.event for m in msgs]
    assert mq.EVENT_STORY_CONFIRMED in events
    assert mq.EVENT_TASK_AVAILABLE in events
    assert events[0] == mq.EVENT_STORY_CONFIRMED  # 先发确认事件，再广播任务
    confirmed = next(m for m in msgs if m.event == mq.EVENT_STORY_CONFIRMED)
    assert confirmed.entity_id == st["id"]


# ---------- 3. review approve/reject 已下线 → 422 ----------
def test_review_approve_deprecated_returns_422(bus, seeded):
    broker = bus
    broker.purge()
    client = TestClient(api.app)
    epic = client.post(f"/api/projects/{seeded['project_id']}/epics",
                       json={"title": "M3 Epic C"}, headers=_hdr(seeded["author_id"])).json()
    st = client.post(f"/api/epics/{epic['id']}/stories",
                     json={"title": "M3 Story C"}, headers=_hdr(seeded["author_id"])).json()
    r = client.post(f"/api/stories/{st['id']}/review",
                    json={"verdict": "approve", "comment": "LGTM"},
                    headers=_hdr(seeded["reviewer_id"]))
    assert r.status_code == 422, r.text
    assert "评审已下线" in r.json().get("detail", "")


def test_review_reject_deprecated_returns_422(bus, seeded):
    broker = bus
    broker.purge()
    client = TestClient(api.app)
    epic = client.post(f"/api/projects/{seeded['project_id']}/epics",
                       json={"title": "M3 Epic D"}, headers=_hdr(seeded["author_id"])).json()
    st = client.post(f"/api/epics/{epic['id']}/stories",
                     json={"title": "M3 Story D"}, headers=_hdr(seeded["author_id"])).json()
    r = client.post(f"/api/stories/{st['id']}/review",
                    json={"verdict": "reject", "comment": "补个验收标准"},
                    headers=_hdr(seeded["reviewer_id"]))
    assert r.status_code == 422, r.text
    assert "评审已下线" in r.json().get("detail", "")


# ---------- 4. story 评论 → comment.replied 广播（无 reviewer 定向） ----------
def test_story_comment_publishes_broadcast_comment_replied(bus, seeded):
    """Story 评审下线后无 reviewer 定向：评论事件退化为广播（agent_id=None）。"""
    broker = bus
    broker.purge()
    client = TestClient(api.app)
    epic = client.post(f"/api/projects/{seeded['project_id']}/epics",
                       json={"title": "M3 Epic E"}, headers=_hdr(seeded["author_id"])).json()
    st = client.post(f"/api/epics/{epic['id']}/stories",
                     json={"title": "M3 Story E"}, headers=_hdr(seeded["author_id"])).json()
    broker.purge()
    r = client.post(f"/api/stories/{st['id']}/comments",
                    json={"author": "m3-author", "content": "已补验收标准，请再看"},
                    headers=_hdr(seeded["author_id"]))
    assert r.status_code == 201, r.text
    q = mq.WorkflowTopology(_NS).broadcast_queue
    assert _wait_depth(broker, q, 1) >= 1
    msg = _last_msg(broker, q)
    assert msg is not None and msg.event == mq.EVENT_COMMENT_REPLIED
    assert msg.entity_id == st["id"]
    assert msg.ref_id == r.json()["id"]


# ---------- 5. 事件发布 best-effort：publisher 未注入时 REST 零影响 ----------
def test_events_are_best_effort_when_bus_disabled(seeded):
    mq.set_workflow_publisher(None)  # 模拟 MQ 未配置
    client = TestClient(api.app)
    r = client.post(f"/api/projects/{seeded['project_id']}/epics",
                    json={"title": "M3 Epic F"}, headers=_hdr(seeded["author_id"]))
    assert r.status_code in (200, 201)
    eid = r.json()["id"]
    # 无 publisher：create_story 仍应成功（发布 no-op）
    r = client.post(f"/api/epics/{eid}/stories",
                    json={"title": "M3 Story F"}, headers=_hdr(seeded["author_id"]))
    assert r.status_code == 201
