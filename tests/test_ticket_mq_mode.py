"""Proposal→Ticket 在 Worker MQ 模式下的闭环验证（2026-08-09 review 修复）。

背景：Proposal Worker 的 MQ 消费的是澄清消息队列；``proposal.ticket_requested``
事件在 workflow 总线（Workflow Worker 确认 ack），本 Worker 收不到。此前
``run_mq_forever`` 只消费澄清消息 + reclaim/sweep，ticket 请求无人处理 →
Proposal 永久卡 ticket_preparing。

修复：run_mq_forever 启动 ``_ticket_scan_loop`` 线程（按 poll_interval 周期
fetch_ticket_requests + handle_ticket_request，与轮询模式 poll_once 对齐）。

本测试用 InMemoryBroker + FakeClient 验证：MQ 模式下 pending 转换请求被扫描
并走完 claim → agent（FakeAgent 返回 ticket_created）→ 回查确认。

运行：PYTHONPATH=. python -m pytest tests/test_ticket_mq_mode.py -q
"""
import threading
import time

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentboard import mq, service
from agentboard.models import Base
from agentboard.worker import (
    AgentDecision, CallableAgentInvoker, ProposalWorker, WorkerConfig,
)


def _env():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as s:
        u = service.register_user(s, username="mq-ticket", password="password123")
        p1 = service.create_project(s, name="MQP", key="MQP")
        service.add_project_member(s, project_id=p1.id, user_id=u.id, role="owner")
        e1 = service.create_epic(s, project_id=p1.id, title="MQE")
        st1 = service.create_story(s, epic_id=e1.id, title="MQS")
        pr = service.create_proposal(s, project_id=p1.id, title="MQ Proposal",
                                     content="need clarity")
        service.set_proposal_status(s, pr.id, "queued")
        service.set_proposal_status(s, pr.id, "analyzing")
        service.set_proposal_status(s, pr.id, "converged")
        service.update_proposal(s, pr.id, converged_spec="# 需求\n- [ ] 子任务一")
        req = service.create_ticket_request(
            s, pr.id, type="task", epic_id=e1.id, story_id=st1.id,
        )
        # worker 消费的 dict 形态（build_ticket_context 读取字段）
        proposal = {
            "id": pr.id, "project_id": p1.id, "title": "MQ Proposal",
            "content": "need clarity", "status": "converged",
            "converged_spec": "# 需求\n- [ ] 子任务一",
        }
        request = {
            "id": req.id,
            "proposal_id": pr.id,
            "type": "task",
            "parent_epic_id": e1.id,
            "parent_story_id": st1.id,
            "title": "",
            "status": "pending",
        }
        return sessions, proposal, request


class _FakeClient:
    """模拟 API：pending 列表 / claim / 回查列表 / proposal / rounds。"""

    def __init__(self, proposal: dict, request: dict):
        self.proposal = proposal
        self.req = request  # 注意：勿命名为 self.request（会遮蔽 request() 方法）
        self.calls: list[tuple[str, str]] = []
        self._done = False

    def _resp(self, code: int, data) -> httpx.Response:
        # httpx 的 raise_for_status 需要 request 实例已绑定
        return httpx.Response(
            code, json=data,
            request=httpx.Request("GET", "http://mq-test"),
        )

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        self.calls.append((method, path))
        if path.endswith("/api/ticket-requests/pending"):
            return self._resp(200, [] if self._done else [self.req])
        if path.endswith("/claim"):
            self._done = True
            return self._resp(200, dict(self.req, status="processing"))
        if path == f"/api/proposals/{self.proposal['id']}":
            return self._resp(200, self.proposal)
        if path.endswith("/rounds"):
            return self._resp(200, [])
        if path.endswith("/ticket-requests") and method == "GET":
            # _confirm_ticket 回查：agent 已生成 → done
            return self._resp(200, [dict(self.req, status="done", ticket_id=7)])
        if path.endswith("/fail"):
            return self._resp(200, dict(self.req, status="failed"))
        if "/reclaim-stale" in path or "/recover-failed" in path:
            return self._resp(200, {"reclaimed": [], "recovered": [], "ids": []})
        if "/proposals/pending" in path:
            return self._resp(200, [])
        return self._resp(200, {})


def test_mq_mode_processes_ticket_requests():
    sessions, proposal, request = _env()
    client = _FakeClient(proposal, request)
    invoker = CallableAgentInvoker(
        lambda ctx: AgentDecision(action="ticket_created"),
    )
    cfg = WorkerConfig(api_url="http://mq-test", token="t", agent="mq-worker",
                       poll_interval=0.2, maintenance_interval=0.5)
    w = ProposalWorker(cfg, invoker=invoker, client=client)
    broker = mq.InMemoryBroker()

    stop = threading.Event()
    thread = threading.Thread(
        target=lambda: w.run_mq_forever(stop=stop, broker=broker,
                                        idle_timeout=1.0),
        daemon=True,
    )
    thread.start()
    # 给 ticket 扫描线程足够时间跑一轮（poll_interval 0.2s，consume idle 1s）
    time.sleep(2.0)
    stop.set()
    thread.join(5)
    assert not thread.is_alive(), "run_mq_forever 未在超时内退出"

    paths = [p for _, p in client.calls]
    # 关键断言：MQ 模式下 ticket 请求被扫描（fetch pending）
    assert any(p.endswith("/api/ticket-requests/pending") for p in paths), (
        "MQ 模式未扫描 pending ticket 请求"
    )
    # build_ticket_context：拉提案 + 轮次（请求进入了处理流水线）
    assert f"/api/proposals/{request['proposal_id']}" in paths, "未构建 ticket 上下文"
    assert any(p.endswith("/rounds") for p in paths), "未拉取提案轮次"
    # agent 声称已创建 → 回查确认（GET 请求列表）
    assert any(
        p == f"/api/proposals/{request['proposal_id']}/ticket-requests"
        for p in paths
    ), "未回查请求状态确认生成结果"
    # 2026-08-09 review：MQ maintenance 周期自动回收超时转换请求（processing 停滞）
    assert any(p == "/api/ticket-requests/reclaim-stale" for p in paths), (
        "MQ maintenance 未自动回收超时转换请求"
    )
    # 流水线完整走通：处理一次返回 created
    client2 = _FakeClient(proposal, request)
    w2 = ProposalWorker(
        cfg, invoker=CallableAgentInvoker(
            lambda ctx: AgentDecision(action="ticket_created"),
        ), client=client2,
    )
    reqs = w2.fetch_ticket_requests()
    assert w2.handle_ticket_request(reqs[0]) == "created", "ticket 处理未收敛为 created"


def test_poll_once_reclaims_stale_ticket_requests():
    """轮询模式 poll_once 同样自动回收超时转换请求（2026-08-09 review）。"""
    sessions, proposal, request = _env()
    client = _FakeClient(proposal, request)
    w = ProposalWorker(
        WorkerConfig(api_url="http://mq-test", token="t", agent="poll-worker",
                     poll_interval=0.2, maintenance_interval=100),
        invoker=CallableAgentInvoker(
            lambda ctx: AgentDecision(action="ticket_created"),
        ), client=client,
    )
    summary = w.poll_once()
    assert summary["ticket_reclaimed"] == []  # 无超时请求，正常空回收
    paths = [p for _, p in client.calls]
    assert "/api/ticket-requests/reclaim-stale" in paths, (
        "poll_once 未自动回收超时转换请求"
    )
