"""AgentBoard Workflow 分配器 Worker（Epic 122 S1 M3 + PR-4）。

PR-4 之前本 Worker 与 .NET ``WorkflowMqConsumerService`` 抢同一个
``agentboard.workflow.broadcast`` 队列：同一个 event 谁拿到谁处理，导致
``task.ready_for_review`` 既被 Python 拿去做 reviewer 分配、又被 .NET 拿
去执行 review，两个不同 action 在并发下互踩。happy path 偶发性失败 / 流程
错乱都源于此。

PR-4 拆分事件 ownership：

-  ``task.ready_for_review``（broadcast）→ 改由 .NET 独占，但 **.NET 也不再
    对它执行**（这是 pre-assignment 事件，没 reviewer）；FastAPI 在同一
    状态转换里 **额外** publish ``task.review_assignment_needed`` 到 internal
    路由（PR-4 新增），Python 独占 internal_queue 听这个事件。
-  Python 选完 reviewer 后 publish ``task.review_requested`` 到 agent 定向
    队列（route="agent"），.NET 拿这条去真正执行 review。
-  老的 ``EVENT_TASK_REVIEW_REQUESTED`` 等定向/agent 事件 Python 不再关心
    （不订阅 internal_queue）。review 闭环主体逻辑在 .NET + FastAPI REST
    endpoint，Python 只做"分配"这一步。

设计原则不变：**消息只做通知、状态一律回查数据库。**
本 Worker 收到 internal event 后不携带任何状态，而是回查 REST 再触发
分配，因此消息重投 / 丢失都不会产生重复轮次或漏单。

MQ 未配置（``AGENTBOARD_MQ_URL`` 为空）时回退 **DB 轮询**：定期扫描
``in_review`` 未指派 reviewer 的 Task 触发指派，正确性不变。

运行：
    python -m agentboard.workflow_worker --mq     # MQ 消费 internal 模式
    python -m agentboard.workflow_worker --loop   # 轮询常驻
    python -m agentboard.workflow_worker --once   # 只跑一轮
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from .core.infrastructure import messaging as mq
from .mq import (
    # PR-4：internal 路由事件白名单（Python 唯一关心的）
    EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED,
    WORKFLOW_DEFAULT_NAMESPACE,
    WorkflowMessage,
    WorkflowTopology,
)

EVENT_TICKET_REQUESTED = mq.EVENT_TICKET_REQUESTED

log = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        log.warning("环境变量 %s=%r 不是整数，回退默认值 %s", name, raw, default)
        return default


@dataclass
class WorkflowConsumerConfig:
    """分配器 Worker 运行参数，全部可由环境变量覆盖。"""

    api_url: str = "http://127.0.0.1:58124"
    # 服务账号 abk_ key（或登录 token）；REST 调用身份
    token: str | None = None
    # 轮询间隔（秒）—— MQ 未配置时的兜底扫描节奏
    poll_interval: float = 10.0
    # 单轮最多处理多少个 Story，避免一个 Worker 长时间独占
    batch_size: int = 20
    http_timeout: float = 30.0
    # 消息总线（M2 泛化 Workflow 拓扑）
    mq: "mq.MQConfig" = field(default_factory=lambda: mq.MQConfig())
    namespace: str = WORKFLOW_DEFAULT_NAMESPACE

    @classmethod
    def from_env(cls) -> "WorkflowConsumerConfig":
        return cls(
            mq=mq.MQConfig.from_env(),
            namespace=os.getenv("AGENTBOARD_WORKFLOW_NAMESPACE",
                                WORKFLOW_DEFAULT_NAMESPACE),
            api_url=os.getenv("AGENTBOARD_API_URL", cls.api_url).rstrip("/"),
            token=os.getenv("AGENTBOARD_WORKER_TOKEN")
            or os.getenv("AGENTBOARD_MCP_TOKEN"),
            poll_interval=float(_env_int("AGENTBOARD_WORKFLOW_WORKER_INTERVAL", 10)),
            batch_size=_env_int("AGENTBOARD_WORKFLOW_WORKER_BATCH", 20),
        )


class WorkflowConsumer:
    """Workflow 事件消费者：分配评审（PR-4 拆 internal_queue）。"""

    #: PR-4：本 Worker 关心的 internal 事件白名单
    #: 任何不在这里的 internal event 直接 ack 忽略（不视为未识别）
    _INTERNAL_HANDLERS = {
        EVENT_TASK_REVIEW_ASSIGNMENT_NEEDED: "_handle_task_review_assignment_needed",
        EVENT_TICKET_REQUESTED: "_handle_auto_story_materialization",
    }

    def __init__(self, config: WorkflowConsumerConfig,
                 client: httpx.Client | None = None):
        self.config = config
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=config.api_url, timeout=config.http_timeout,
            headers=({"Authorization": f"Bearer {config.token}"} if config.token else {}),
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "WorkflowConsumer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- HTTP ----------

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        return self.client.request(method, path, **kw)

    # ---------- 分配动作 ----------

    def _assign_task_reviewer(self, task_id: int) -> bool:
        """Task ready_for_review → 自动指派 Task reviewer（幂等，切片 2 M2）。

        Sprint 12 多数决 fan-out：当 ``AGENTBOARD_REVIEW_MODE=majority`` 时
        用 ``?count=AGENTBOARD_REVIEW_QUORUM`` 一次挑 N 个 reviewer，每人
        收到一条 ``task.review_requested``（端点内部 fan-out 事件）。
        ``count`` 上限由后端卡死 (1..9)，无须在此重检。

        成功/已指派 → True；无在线 reviewer → warn + True（开发完成后开发者
        轮询 list_tasks?reviewer_id=me 兜底）；网络异常 → 抛 MessageRetry
        触发 broker requeue（Stage 0 修正：此前 return False 实际进死信，
        与「重投语义」不符）。
        """
        # 多数决模式才 fan-out；单 review 模式（默认 / 兼容旧部署）保持
        # 一次一个，行为不变。
        count = 1
        try:
            from .core.application.service import get_review_mode, get_review_quorum
            if get_review_mode() == "majority":
                count = get_review_quorum()
        except Exception as e:  # pragma: no cover - 防御性 import 失败
            log.debug("review_mode 探测失败，回退 count=1：%s", e)
        try:
            r = self._request("POST", f"/api/tasks/{task_id}/assign-reviewer",
                              params={"count": count})
        except Exception as e:
            log.warning("task %s 指派评审请求失败（网络异常），requeue 重投：%s",
                        task_id, e)
            raise mq.MessageRetry(
                f"task #{task_id} assign-reviewer 网络异常") from None
        if r.status_code in (200, 201):
            t = r.json()
            log.info("task %s 已指派 reviewer=%s（status=%s, count=%s）",
                     task_id, t.get("reviewer_id"), t.get("status"), count)
            return True
        if r.status_code == 404:
            log.info("task %s 不存在（可能已删除），忽略", task_id)
            return True
        # 422（无在线 reviewer / 非 in_review）—— 暂时性条件，轮询兜底
        log.warning("task %s 指派评审未成功（HTTP %s）：%s",
                    task_id, r.status_code, r.text[:200])
        return True

    def _broadcast_available_tasks(self, story_id: int) -> bool:
        """PR-4：此方法已废弃（之前负责 story.confirmed → task.available 广播）。

        保留以兼容旧 reference（defense），但 no-op 行为：当前 internal_queue
        收不到 story.confirmed 事件，story 自动编排由 Proposal Worker
        轮询兜底执行（fetch confirmed stories → 拉起 agent），不再依赖
        本 Worker 协助通知。Story 级评审已下线（2026-08-09）。
        """
        log.debug("_broadcast_available_tasks(story_id=%s) 已废弃（PR-4）", story_id)
        return True

    def handle_message(self, msg: WorkflowMessage) -> bool:
        """PR-4：处理一条 internal 编排消息。

        只关心 internal 事件白名单内的事件；不在白名单的 internal event
        直接 ack 忽略。返回 False → broker 转死信（重投语义留给轮询兜底）。
        """
        event = msg.event
        handler = self._INTERNAL_HANDLERS.get(event)
        if handler is None:
            log.info("事件 %s（entity=%s#%s）不在 internal 白名单，直接 ack 忽略",
                     event, msg.entity_type, msg.entity_id)
            return True
        log.info("事件 %s（entity=%s#%s ref_id=%s correlation_id=%s）：路由到 %s",
                 event, msg.entity_type, msg.entity_id, msg.ref_id, msg.correlation_id,
                 handler)
        method = getattr(self, handler, None)
        if method is None:
            log.error("internal handler %s 未找到对应方法（白名单错配）", handler)
            return False
        return method(msg)

    def _handle_task_review_assignment_needed(self, msg: WorkflowMessage) -> bool:
        """Task 进入 in_review（PR-4 内部事件）→ 选 reviewer。

        流程：
          1. 调 ``POST /api/tasks/{tid}/assign-reviewer``（CAS 并发安全，幂等）
          2. 服务端 assign-reviewer API 选完 reviewer 后会 publish
             ``task.review_requested`` 到 agent 定向队列（route=agent），
             .NET 真正执行 review。

        之前 ``task.ready_for_review`` broadcast 的"指派 reviewer"职责
        完全迁到这里，避免与 .NET 抢同一个 queue 重复处理。
        """
        task_id = msg.entity_id
        log.info("事件 task.review_assignment_needed（task=%s assignee=%s）：自动指派 Task reviewer",
                 task_id, msg.ref_id)
        return self._assign_task_reviewer(task_id)

    def _execute_auto_story_request(self, request_id: int) -> bool:
        """执行确定性的 AUTO Story request；不拉起 CLI Agent 决策实体类型。"""
        try:
            r = self._request(
                "POST", f"/api/ticket-requests/{request_id}/execute",
            )
        except Exception as e:
            raise mq.MessageRetry(
                f"auto_story request #{request_id} 网络异常: {e}",
            ) from None
        if r.status_code in (200, 201):
            log.info("auto_story request #%s materialized", request_id)
            return True
        if r.status_code in (404, 409):
            # 404=请求已删除；409=其它消费者已 claim，均可安全 ack。
            log.info("auto_story request #%s 已被处理/不存在（HTTP %s）",
                     request_id, r.status_code)
            return True
        log.warning("auto_story request #%s 执行失败 HTTP %s: %s",
                    request_id, r.status_code, r.text[:300])
        return True

    def _handle_auto_story_materialization(self, msg: WorkflowMessage) -> bool:
        if not msg.ref_id:
            log.error("proposal.ticket_requested 缺 ref_id，无法定位 request")
            return False
        return self._execute_auto_story_request(int(msg.ref_id))

    # ---------- 轮询模式（无 MQ 兜底） ----------

    def run_poll_once(self) -> int:
        """扫描一轮并触发分配：in_review 未指派 Task 指派 + 评审超时重派。返回处理条数。

        Story 级评审已下线（2026-08-09）：不再扫描 backlog Story 指派 reviewer。
        """
        assigned = 0
        # 无 MQ 兜底：仅接管新的确定性 auto_story；手工四类 ticket 仍由原
        # Proposal Agent worker 处理。
        try:
            r = self.client.get(
                "/api/admin/ticket-requests/pending",
                params={"limit": max(1, self.config.batch_size)},
            )
            r.raise_for_status()
            rows = r.json() or []
            for req in rows:
                if req.get("type") != "auto_story":
                    continue
                if self._execute_auto_story_request(int(req["id"])):
                    assigned += 1
        except Exception as e:
            log.warning("轮询执行 auto_story request 失败：%s", e)
        # 切片 2 M2 兜底：扫描 in_review 未指派 reviewer 的 Task → 自动指派
        try:
            r = self.client.get("/api/tasks", params={
                "status": "in_review", "limit": max(1, self.config.batch_size),
            })
            r.raise_for_status()
            data = r.json()
            task_items = data.get("items", []) if isinstance(data, dict) else (data or [])
            for t in task_items:
                if t.get("reviewer_id") is not None:
                    continue  # 已指派（幂等跳过）
                if self._assign_task_reviewer(t["id"]):
                    assigned += 1
        except Exception as e:
            log.warning("轮询拉取 in_review Task 失败：%s", e)
        # 切片 3 M2：超时重派扫描（best-effort；幂等，服务端 CAS 仲裁）
        try:
            r = self._request("POST", "/api/review-stats/reassign-timeout",
                              json={"timeout_minutes": 30, "max_per_run": 20})
            if r.status_code in (200, 201):
                log.info("超时重派扫描：%s", r.json())
            else:
                log.warning("超时重派扫描未成功（HTTP %s）：%s",
                            r.status_code, r.text[:200])
        except Exception as e:
            log.warning("超时重派扫描请求失败（网络异常，下轮重试）：%s", e)
        if assigned:
            log.info("轮询本轮指派 %s 个评审任务", assigned)
        return assigned

    def run_forever(self, stop: threading.Event | None = None,
                    interval: float | None = None) -> int:
        """轮询常驻循环（MQ 未配置时使用）。"""
        stop = stop or threading.Event()
        interval = interval if interval is not None else self.config.poll_interval
        cycles = 0
        while not stop.wait(interval):
            cycles += 1
            try:
                self.run_poll_once()
            except Exception:
                log.exception("轮询周期异常，将在下个周期重试")
        return cycles

    # ---------- MQ 模式 ----------

    def run_mq_forever(self, stop: threading.Event | None = None,
                       max_messages: int | None = None,
                       idle_timeout: float | None = None,
                       broker: Any | None = None) -> dict:
        """PR-4：MQ 消费 internal_queue（不抢 .NET 的 broadcast_queue）。

        之前版本订阅 ``topology.broadcast_queue``，与 .NET
        ``WorkflowMqConsumerService`` 抢同一个 queue。P0-1 的根因。

        修法：本 Worker 只听 ``internal_queue``（PR-4 新增的编排事件路由），
        .NET 继续听 ``broadcast_queue`` + 每 Agent 定向队列。两者完全
        解耦，event ownership 单一。

        未配置 MQ 时自动回退轮询模式，部署未就绪不影响功能。
        """
        if not self.config.mq.enabled:
            log.warning("未配置 AGENTBOARD_MQ_URL（或 pika 不可用），回退轮询模式")
            cycles = self.run_forever(stop=stop)
            return {"mode": "poll", "cycles": cycles}

        stop = stop or threading.Event()
        broker = broker or mq.PikaWorkflowBroker(self.config.mq, self.config.namespace)
        topology = WorkflowTopology(self.config.namespace)
        broker.declare_topology()
        log.info("Workflow 分配器以 MQ 模式启动：ns=%s queue=%s api=%s",
                 self.config.namespace, topology.internal_queue, self.config.api_url)
        try:
            stats = broker.consume(
                topology.internal_queue, self.handle_message,
                max_messages=max_messages, idle_timeout=idle_timeout, stop=stop,
            )
        finally:
            try:
                broker.close()
            except Exception:  # pragma: no cover
                pass
        stats["mode"] = "mq"
        log.info("Workflow 分配器 MQ 模式退出：%s", stats)
        return stats


# ===================== CLI =====================

def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentboard.workflow_worker",
        description="AgentBoard Workflow 分配器 Worker（Epic 122 S1 M3）",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="只跑一轮轮询后退出")
    group.add_argument("--loop", action="store_true", help="常驻轮询（默认）")
    group.add_argument("--mq", action="store_true",
                       help="MQ 消费模式（未配置 AGENTBOARD_MQ_URL 时自动回退轮询）")
    parser.add_argument("--mq-url", default=None, help="覆盖 AGENTBOARD_MQ_URL")
    parser.add_argument("--api-url", default=None, help="覆盖 AGENTBOARD_API_URL")
    parser.add_argument("--interval", type=float, default=None, help="轮询间隔（秒）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = WorkflowConsumerConfig.from_env()
    if args.api_url:
        cfg.api_url = args.api_url.rstrip("/")
    if args.interval is not None:
        cfg.poll_interval = args.interval
    if args.mq_url:
        cfg.mq = mq.MQConfig(url=args.mq_url, enabled=True)

    with WorkflowConsumer(cfg) as worker:
        if args.mq:
            stats = worker.run_mq_forever()
            log.info("Workflow 分配器退出统计：%s", stats)
        elif args.once:
            n = worker.run_poll_once()
            log.info("单轮完成，处理 %s 个 Story", n)
        else:  # --loop（默认）
            cycles = worker.run_forever()
            log.info("轮询退出，共 %s 个周期", cycles)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
