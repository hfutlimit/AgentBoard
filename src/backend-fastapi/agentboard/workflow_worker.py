"""AgentBoard Workflow 分配器 Worker（Epic 122 S1 M3）。

消费 M2 泛化的 Workflow 事件总线（``agentboard.workflow`` 命名空间）：

- ``story.confirmed``（广播）→ 仅确认 ack：Story 确认后的 agent 自动处理编排
  由 Proposal Worker（``agentboard.worker``）轮询兜底执行（Ticket 全流程，
  2026-08-09，Story 级评审已下线）；
- ``task.ready_for_review``（广播）→ 自动指派 Task reviewer（``POST /api/tasks/{tid}/assign-reviewer``，
  随机选择 + CAS 幂等 + 排除 assignee；切片 2 M2 评审闭环入口）；
- ``review.rejected`` / ``comment.replied`` → 日志记录（评审往返收敛主要由
  Reviewer/作者 Agent 各自订阅**定向队列**感知，本 Worker 不介入业务决策）。

设计原则（与 Proposal Worker 一致）：**消息只做通知、状态一律回查数据库。**
本 Worker 收到事件后不携带任何状态，而是回查 REST 再触发分配，
因此消息重投 / 丢失都不会产生重复轮次或漏单。

MQ 未配置（``AGENTBOARD_MQ_URL`` 为空）时回退 **DB 轮询**：定期扫描
``in_review`` 未指派 reviewer 的 Task 触发指派，正确性不变。

运行：
    python -m agentboard.workflow_worker --mq     # MQ 消费模式（未配置自动回退轮询）
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
    EVENT_COMMENT_REPLIED,
    EVENT_REVIEW_REJECTED,
    EVENT_REVIEW_VOTE_CAST,
    EVENT_STORY_CONFIRMED,
    EVENT_STORY_CREATED,
    # Step 4 P1-1（2026-08-10 review）：按 entity 分流的 review/comment 事件
    EVENT_STORY_REVIEW_REQUESTED, EVENT_STORY_REVIEW_REJECTED,
    EVENT_STORY_REVIEW_VOTE_CAST, EVENT_STORY_COMMENT_REPLIED,
    EVENT_TASK_REVIEW_REQUESTED, EVENT_TASK_REVIEW_REJECTED,
    EVENT_TASK_REVIEW_VOTE_CAST, EVENT_TASK_COMMENT_REPLIED,
    EVENT_TASK_AVAILABLE,
    EVENT_TASK_READY_FOR_REVIEW,
    EVENT_TASK_REVIEWED,
    EVENT_TASK_REJECTED,
    EVENT_TICKET_CREATED,
    EVENT_TICKET_REQUESTED,
    WORKFLOW_DEFAULT_NAMESPACE,
    WorkflowMessage,
    WorkflowTopology,
)

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
    """Workflow 事件消费者：分配评审 + 预留开发任务分配入口。"""

    #: 本 Worker 关心的广播事件 → 处理函数（未列出的事件直接 ack 忽略）
    _HANDLERS = {
        EVENT_STORY_CONFIRMED: "story.confirmed",
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

        成功/已指派 → True；无在线 reviewer → warn + True（开发完成后开发者
        轮询 list_tasks?reviewer_id=me 兜底）；网络异常 → False（重投语义）。
        """
        try:
            r = self._request("POST", f"/api/tasks/{task_id}/assign-reviewer")
        except Exception as e:
            log.warning("task %s 指派评审请求失败（网络异常）：%s", task_id, e)
            return False
        if r.status_code in (200, 201):
            t = r.json()
            log.info("task %s 已指派 reviewer=%s（status=%s）",
                     task_id, t.get("reviewer_id"), t.get("status"))
            return True
        if r.status_code == 404:
            log.info("task %s 不存在（可能已删除），忽略", task_id)
            return True
        # 422（无在线 reviewer / 非 in_review）—— 暂时性条件，轮询兜底
        log.warning("task %s 指派评审未成功（HTTP %s）：%s",
                    task_id, r.status_code, r.text[:200])
        return True

    def _broadcast_available_tasks(self, story_id: int) -> bool:
        """Story confirmed → 回查 Story 下 backlog/todo 任务 → 逐个广播 ``task.available``。

        消息只带定位信息（task_id + story_id），开发者收到后经 ``claim_development_task``
        竞争认领（CAS，恰一赢家）。MQ 未配置时 ``publish_workflow_event`` 为 no-op，
        开发者靠轮询（list_tasks?status=backlog）兜底，正确性不变。

        注意（2026-08-09）：Story confirmed 的 agent 自动编排由 Proposal Worker
        轮询执行，本方法仅作通知辅助，Worker 主流程不再依赖它。
        """
        try:
            r = self._request("GET", f"/api/stories/{story_id}/tasks",
                              params={"limit": 200})
            r.raise_for_status()
            items = (r.json() or {}).get("items", []) or []
        except Exception as e:
            log.warning("story %s 拉取任务列表失败：%s", story_id, e)
            return False
        claimed = 0
        for t in items:
            if t.get("status") in ("backlog", "todo"):
                mq.publish_workflow_event(EVENT_TASK_AVAILABLE, "task", t["id"],
                                          ref_id=story_id)
                claimed += 1
        log.info("story %s 已确认（confirmed），广播 %s 个可认领任务",
                 story_id, claimed)
        return True

    def handle_message(self, msg: WorkflowMessage) -> bool:
        """处理一条 Workflow 消息。返回 False → broker 转死信（重投语义留给轮询兜底）。"""
        event = msg.event
        if event == EVENT_STORY_CONFIRMED:
            # Ticket 全流程：用户已确认 Story（backlog→confirmed），agent 自动处理
            # 编排由 Proposal Worker（agentboard.worker）轮询兜底执行（fetch confirmed
            # stories → 拉起 agent），本 Worker 仅确认 ack 避免死信（2026-08-09）。
            log.info("事件 story.confirmed（story=%s epic=%s）：Agent 自动处理由 Proposal Worker 轮询兜底",
                     msg.entity_id, msg.ref_id)
            return True
        if event == EVENT_STORY_CREATED:
            # Story 创建不再自动指派 reviewer（Story 级评审已下线，2026-08-09）：
            # 设计评审由 design task 的 in_design 流承担，实现评审由 Task in_review 承担。
            log.info("事件 story.created（story=%s）：Story 级评审已下线，跳过指派", msg.entity_id)
            return True
        if event == EVENT_TASK_AVAILABLE:
            log.info("事件 task.available（task=%s story=%s）：由在线 developer 竞争认领", event, msg.entity_id)
            return True
        if event in (EVENT_REVIEW_REJECTED, EVENT_COMMENT_REPLIED,
                     EVENT_STORY_REVIEW_REJECTED, EVENT_STORY_COMMENT_REPLIED,
                     EVENT_TASK_REVIEW_REJECTED, EVENT_TASK_COMMENT_REPLIED):
            # Step 4 P1-1：旧 review.* 仍兼容（log 走"story"默认标注），
            # 新 story/task.* 分别标注 entity，让运维能从日志一眼区分。
            entity = "task" if event.startswith("task.") else "story"
            log.info("事件 %s（%s=%s）：评审往返收敛，由 Agent 定向订阅处理",
                     event, entity, msg.entity_id)
            return True
        if event == EVENT_TASK_READY_FOR_REVIEW:
            # Task 提交评审 → 自动指派 Task reviewer（切片 2 M2 闭环）
            log.info("事件 %s（task=%s assignee=%s）：自动指派 Task reviewer",
                     event, msg.entity_id, msg.ref_id)
            return self._assign_task_reviewer(msg.entity_id)
        if event in (EVENT_TASK_REVIEWED, EVENT_TASK_REJECTED):
            log.info("事件 %s（task=%s）：Task 评审完成，assignee/reviewer 经定向队列感知",
                     event, msg.entity_id)
            return True
        if event in (EVENT_REVIEW_VOTE_CAST, EVENT_STORY_REVIEW_VOTE_CAST,
                     EVENT_TASK_REVIEW_VOTE_CAST):
            # 切片 3 M3：多数决投票已记录（未达法定票数），等待更多评审人投票/超时兜底
            entity = "task" if event.startswith("task.") else "story"
            log.info("事件 %s（%s=%s 投票人=%s）：多数决进行中，达法定票数自动结算",
                     event, entity, msg.entity_id, msg.ref_id)
            return True
        if event in (EVENT_TICKET_REQUESTED, EVENT_TICKET_CREATED):
            # Proposal → Ticket 转化事件：由 Proposal Worker 轮询兜底消费
            # （fetch_ticket_requests + handle_ticket_request），本 Worker 仅确认
            # ack 避免死信；事件留作审计/通知总线（2026-08-09 review 备注）。
            log.info("事件 %s（proposal=%s req=%s）：Proposal Worker 轮询兜底处理",
                     event, msg.entity_id, msg.ref_id)
            return True
        log.warning("收到未识别事件 %s（entity=%s#%s），直接 ack 忽略",
                    event, msg.entity_type, msg.entity_id)
        return True

    # ---------- 轮询模式（无 MQ 兜底） ----------

    def run_poll_once(self) -> int:
        """扫描一轮并触发分配：in_review 未指派 Task 指派 + 评审超时重派。返回处理条数。

        Story 级评审已下线（2026-08-09）：不再扫描 backlog Story 指派 reviewer。
        """
        assigned = 0
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
        """MQ 消费模式：订阅广播队列（story.created 等）。

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
        log.info("Workflow 分配器以 MQ 模式启动：ns=%s api=%s",
                 self.config.namespace, self.config.api_url)
        try:
            stats = broker.consume(
                topology.broadcast_queue, self.handle_message,
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
