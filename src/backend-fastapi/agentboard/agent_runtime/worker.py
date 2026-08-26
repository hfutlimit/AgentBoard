"""Proposal Worker 主循环（Epic 123 Step 2 · 拆分自原 worker.py）。

拆分目标（Story 238）：原 1808 行「三合一」Worker 拆为「主循环 + 3 个 Handler」。
本模块只保留：发现路由、维护编排、MQ 消费拓扑、Agent 心跳、CLI 入口。
业务逻辑（prompt 构建 / context 加载 / decision 落库）下沉到
``handlers/{clarify,ticket,story}.py``；崩溃恢复 / 失败重投在 ``maintenance.py``。

兼容性：``ProposalWorker(config, invoker=..., client=...)`` 调用契约不变；
``agentboard.worker`` 模块符号（ProposalWorker / WorkerConfig / AgentDecision /
SubprocessAgentInvoker / CallableAgentInvoker / split_command / extract_decision_json）
全部 re-export（见 __init__.py）。

Review 2026-08-26 P1：每个 handle* 入口必须把 ExecutionCommand 塞进 context._command，
激活 invokers.build_prompt 的 PreparedExecution 路径，让 BehaviorResolver /
ContextBuilder / PromptBuilder 在 production 真正生效。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from agentboard.core.infrastructure import messaging as mq
from . import heartbeat, maintenance
from .config import AgentDecision, WorkerConfig, WorkerError
from .contract import ExecutionCommand, WorkType
from .handlers import build_handlers
from .invokers import (
    CallableAgentInvoker,
    SubprocessAgentInvoker,
    build_prompt,
    set_prompt_builder,
)

log = logging.getLogger("agentboard.worker")

__all__ = [
    "ProposalWorker", "WorkerConfig", "AgentDecision",
    "SubprocessAgentInvoker", "CallableAgentInvoker",
    "split_command", "build_prompt",
]


def _parse_dt(value: Any) -> datetime | None:
    """解析后端返回的时间串；naive 一律按 UTC 处理（服务端用 utc_now 落库）。"""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _stamp_command(
    work_type: WorkType,
    entity_type: str,
    entity_id: int,
    context: dict,
    *,
    execution_id: str | None = None,
) -> ExecutionCommand:
    """构造 ExecutionCommand 并塞进 context["_command"]，激活 PreparedExecution 路径。

    Review 2026-08-26 P1：原 Worker 主流程完全没有调用 BehaviorResolver /
    ContextBuilder / PromptBuilder；通过这个 helper 在每个 handle* 入口
    把 ExecutionCommand 注入 context，下游 invoker.invoke() 自动走
    ``invokers._prepared_build_prompt``，最终 prompt 由 Behavior pipeline
    真正渲染（不再仅靠 handler hardcode）。

    Args:
        work_type: 已归一化的 WorkType（调用方负责 legacy alias 转换）
        entity_type: 业务实体类型（proposal | story | task | epic）
        entity_id: 业务实体 ID
        context: handler 用的 context dict（会被原地写入 _command）
        execution_id: 可选；不传则用 (kind, entity_id) 派生稳定 ID

    Returns:
        构造的 ExecutionCommand
    """
    if "_command" in context and isinstance(context["_command"], ExecutionCommand):
        return context["_command"]
    eid = execution_id or f"{work_type.value}_{entity_type}_{entity_id}"
    cmd = ExecutionCommand(
        execution_id=eid,
        work_type=work_type,
        entity_type=entity_type,
        entity_id=entity_id,
        context=context,
    )
    context["_command"] = cmd
    return cmd


class ProposalWorker:
    """澄清回路消费者：发现 → 认领 → 全量重放 → 调 Agent → 落决策。

    三个业务域（澄清 / Ticket 转化 / Story 编排）由对应 Handler 承担，
    本类只做路由 + 维护 + MQ/心跳编排。
    """

    def __init__(self, config: WorkerConfig, invoker: AgentInvoker | None = None,
                 client: httpx.Client | None = None):
        from .config import AgentInvoker  # 协议，延迟导入避免循环
        self.config = config
        self.invoker = invoker or self._default_invoker(config)
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=config.api_url, timeout=config.http_timeout,
            headers=({"Authorization": f"Bearer {config.token}"} if config.token else {}),
        )
        # prompt 构建：注入各 Handler 的实现（invokers.build_prompt 委托）
        from .handlers import build_handlers as _build_handlers
        self._handlers = _build_handlers(self.client, self.config)
        set_prompt_builder(self.build_prompt_for)
        # 心跳节流
        self._last_heartbeat_ts: float = 0.0
        # 后台工作项执行器（2026-08-26 根治长任务阻塞 main loop）。
        # 默认不启用；config.async_story_executor=True 时 init。
        # Stage 0 泛化：覆盖 clarify / ticket / story 三域（配置名沿用旧值兼容）。
        self._work_executor: "AsyncWorkExecutor | None" = None
        if getattr(config, "async_story_executor", False):
            from .async_story import AsyncWorkExecutor  # 延迟导入避免循环
            max_c = max(1, int(getattr(config, "async_story_max_concurrent", 1)))
            join_to = float(getattr(config, "async_story_join_timeout", 30.0))
            self._work_executor = AsyncWorkExecutor(
                invoker=self.invoker, handlers=self._handlers,
                max_concurrent=max_c, join_timeout=join_to,
            )
        # MQ 消费瞬时错误重试计数（proposal_id → 已重试次数），成功消费后清除
        self._msg_retries: dict[int, int] = {}
        self._validate_lease_vs_timeout()

    def _validate_lease_vs_timeout(self) -> None:
        """租约必须显著大于单次 agent 超时，否则长任务会在执行中被回收。

        仅告警不拒绝启动 —— 运维可能临时调小 lease 观察回收行为；
        默认 1800 vs 900 是 2× 安全边界，低于它就危险了。
        """
        lease = int(self.config.lease_seconds)
        timeout = int(self.config.agent_timeout)
        if lease <= timeout:
            log.warning(
                "lease_seconds(%s) ≤ agent_timeout(%s)：正在执行的 agent 任务会"
                "在超时前被租约回收判定为崩溃。建议 lease ≥ 2×agent_timeout"
                "（环境变量 AGENTBOARD_WORKER_LEASE / AGENTBOARD_WORKER_AGENT_TIMEOUT）",
                lease, timeout)

    # ---------- 构造辅助 ----------

    @staticmethod
    def _default_invoker(config: WorkerConfig) -> "AgentInvoker":
        # 优先级（2026-08-25 多通道路由）：
        #   1. AGENTBOARD_WORKER_AGENT_COMMANDS（多 alias 路由表）→ RoutedSubprocessInvoker
        #   2. AGENTBOARD_WORKER_AGENT_CMD（旧单字符串）→ SubprocessAgentInvoker
        # 都没配 → fail-fast
        from .invokers import (
            RoutedSubprocessInvoker, parse_agent_command_map,
        )
        cmds = parse_agent_command_map()
        if cmds:
            try:
                return RoutedSubprocessInvoker(commands=cmds, timeout=config.agent_timeout)
            except ValueError as e:
                log.warning("RoutedSubprocessInvoker 初始化失败：%s", e)
        if not config.agent_cmd.strip():
            raise ValueError(
                "未配置 AGENTBOARD_WORKER_AGENT_COMMANDS / AGENTBOARD_WORKER_AGENT_CMD，"
                "且未显式传入 invoker —— Worker 不知道该拉起哪个无头 Agent"
            )
        return SubprocessAgentInvoker(config.agent_cmd, timeout=config.agent_timeout)

    def dispatch(self, work_item: dict) -> Any:
        """按域路由 work_item → Handler（依据 can_handle 判定）。"""
        for h in self._handlers.values():
            if h.can_handle(work_item):
                return h
        log.warning("无法路由工作项（无匹配 Handler）：%s", work_item)
        return None

    def close(self) -> None:
        # 后台工作项执行器（2026-08-26）：先停接新任务，再 join in-flight。
        # 超时由 config.async_story_join_timeout 控制（默认 30s），
        # 超过则强制退场，子进程随主进程一起被 OS 回收。
        if self._work_executor is not None:
            try:
                self._work_executor.shutdown()
            except Exception:
                log.exception("AsyncWorkExecutor 收尾异常")
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "ProposalWorker":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- HTTP ----------

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        return self.client.request(method, path, **kw)

    def _get_json(self, path: str, **kw) -> Any:
        r = self._request("GET", path, **kw)
        r.raise_for_status()
        return r.json()

    # ---------- prompt 路由（供 invokers.build_prompt 委托） ----------

    def build_prompt_for(self, context: dict) -> str:
        """按 context 的 action 字段路由到对应 Handler 的 prompt 构建器。"""
        action = str(context.get("action") or "")
        if action == "create_ticket":
            return self._handlers["ticket"].build_prompt(context)
        if action == "process_story":
            return self._handlers["story"].build_prompt(context)
        if action == "process_task":
            return self._handlers["story"].build_task_prompt(context)
        if action == "review_task":
            return self._handlers["review"].build_prompt(context)
        if action == "owner_response":
            return self._handlers["owner_response"].build_prompt(context)
        return self._handlers["clarify"].build_prompt(context)

    # ---------- 域便捷转发（向后兼容） ----------

    def fetch_work(self) -> list[dict]:
        return self._handlers["clarify"].fetch()

    def claim(self, proposal: dict) -> bool:
        return self._handlers["clarify"].claim(proposal)

    def build_context(self, proposal_id: int) -> dict:
        return self._handlers["clarify"].load_context({"id": proposal_id})

    def handle(self, proposal: dict) -> str:
        return self._handlers["clarify"].handle(proposal, self.invoker)

    def mark_failed(self, proposal_id: int, error: str) -> str:
        return self._handlers["clarify"]._mark_failed(proposal_id, error)

    def fetch_ticket_requests(self) -> list[dict]:
        return self._handlers["ticket"].fetch()

    def build_ticket_context(self, request: dict) -> dict:
        return self._handlers["ticket"].load_context(request)

    def handle_ticket_request(self, request: dict) -> str:
        return self._handlers["ticket"].handle(request, self.invoker)

    def fetch_confirmed_stories(self) -> list[dict]:
        return self._handlers["story"].fetch()

    def build_story_context(self, story: dict) -> dict:
        return self._handlers["story"].load_context(story)

    def handle_story(self, story: dict) -> str:
        return self._handlers["story"].handle(story, self.invoker)

    def build_task_context(self, task: dict) -> dict:
        return self._handlers["story"].build_task_context(task)

    def handle_task_available(self, msg: "mq.WorkflowMessage") -> bool:
        return self._handlers["story"].handle_task_available(msg, self.invoker)

    def handle_direct_task(self, msg: "mq.WorkflowMessage") -> bool:
        return self._handlers["story"].handle_direct_task(msg, self.invoker)

    def handle_workflow_message(self, msg: "mq.WorkflowMessage") -> bool:
        if msg.event == mq.EVENT_TASK_REVIEW_REQUESTED:
            return self._handlers["review"].handle_requested(msg, self.invoker)
        if msg.event in (mq.EVENT_TASK_REVIEWED, mq.EVENT_TASK_REJECTED):
            return self._handlers["owner_response"].handle_result(msg, self.invoker)
        return self._handlers["story"].handle_workflow_message(msg, self.invoker)

    # ---------- 私有成员兼容转发（旧 worker.py 单文件时期的内部 API） ----------

    def _apply_ask(self, proposal_id: int, decision: AgentDecision) -> str:
        return self._handlers["clarify"]._apply_ask(proposal_id, decision)

    def _apply_finalize(self, proposal_id: int, decision: AgentDecision) -> str:
        return self._handlers["clarify"]._apply_finalize(proposal_id, decision)

    @property
    def _story_min_interval(self) -> float:
        return self._handlers["story"]._story_min_interval

    @_story_min_interval.setter
    def _story_min_interval(self, value: float) -> None:
        self._handlers["story"]._story_min_interval = value

    @property
    def _story_attempts(self) -> dict[int, float]:
        return self._handlers["story"]._story_attempts

    @_story_attempts.setter
    def _story_attempts(self, value: dict[int, float]) -> None:
        self._handlers["story"]._story_attempts = value

    def _story_all_tasks_done(self, story: dict) -> bool:
        return self._handlers["story"]._story_all_tasks_done(story)

    def _story_fail(self, sid: int, error: str) -> str:
        return self._handlers["story"]._story_fail(sid, error)

    def _story_comment(self, story_id: int, content: str) -> None:
        return self._handlers["story"]._story_comment(story_id, content)

    # ---------- 崩溃恢复（委托 maintenance） ----------

    def reclaim_stale(self) -> list[int]:
        return maintenance.reclaim_stale(self.client, self.config)

    def reclaim_stale_ticket_requests(self) -> list[int]:
        return maintenance.reclaim_stale_ticket_requests(self.client, self.config)

    def reclaim_stale_stories(self) -> list[int]:
        return maintenance.reclaim_stale_stories(self.client, self.config)

    def reclaim_stale_tasks(self) -> list[int]:
        return maintenance.reclaim_stale_tasks(self.client, self.config)

    def recover_failed(self) -> list[int]:
        return maintenance.recover_failed(self.client, self.config)

    def sweep(self, publisher: "mq.ProposalPublisher") -> int:
        return maintenance.sweep(self.client, self.config, self.fetch_work, publisher)

    # ---------- Agent 心跳探测（2026-08-09，实现下沉 heartbeat.py） ----------

    def _probe_cli(self, cmd: str, model: str = "") -> tuple[bool, str]:
        """CLI 可用性探测：``<cmd> --version``（委托 heartbeat.probe_cli）。"""
        return heartbeat.probe_cli(self.config, cmd, model=model)

    def agent_heartbeat_once(self) -> dict:
        """执行一轮 Agent 心跳探测（委托 heartbeat.agent_heartbeat_once）。"""
        return heartbeat.agent_heartbeat_once(self.client, self.config)

    def _agent_heartbeat_loop(self, stop: threading.Event) -> None:
        """后台心跳探测线程（周期 heartbeat_interval，默认 60s）。"""
        while not stop.wait(self.config.heartbeat_interval):
            try:
                self.agent_heartbeat_once()
            except Exception:
                log.exception("Agent 心跳探测周期异常，将在下个周期重试")

    # ---------- 轮询 ----------

    def poll_once(self) -> dict:
        """执行一轮：先做崩溃恢复 + agent 失败自动重投，再消费三类工作项。

        2026-08-26 起：config.async_story_executor=True 时三类工作项都提交
        后台线程池（per-kind 串行化 + (kind,id) 去重），main loop 不再被
        900s 长任务阻塞；close() 时等待后台收尾。
        """
        now_ts = time.time()
        if now_ts - self._last_heartbeat_ts >= self.config.heartbeat_interval:
            self._last_heartbeat_ts = now_ts
            try:
                self.agent_heartbeat_once()
            except Exception:
                log.exception("Agent 心跳探测异常（不阻断本轮）")
        reclaimed = self.reclaim_stale()
        ticket_reclaimed = self.reclaim_stale_ticket_requests()
        story_reclaimed = self.reclaim_stale_stories()
        task_reclaimed = self.reclaim_stale_tasks()
        recovered = self.recover_failed()
        results: dict[str, int] = {}
        handled: list[dict] = []
        for proposal in self.fetch_work():
            if self._work_executor is not None:
                outcome = self._work_executor.submit(
                    "clarify", proposal, on_decision=self._on_work_decision)
            else:
                outcome = self.handle(proposal)
            results[outcome] = results.get(outcome, 0) + 1
            handled.append({"proposal_id": proposal.get("id"), "outcome": outcome})
        ticket_results: dict[str, int] = {}
        for req in self.fetch_ticket_requests():
            if self._work_executor is not None:
                outcome = self._work_executor.submit(
                    "ticket", req, on_decision=self._on_work_decision)
            else:
                outcome = self.handle_ticket_request(req)
            ticket_results[outcome] = ticket_results.get(outcome, 0) + 1
            handled.append({"ticket_request_id": req.get("id"), "outcome": outcome})
        story_results: dict[str, int] = {}
        for story in self.fetch_confirmed_stories():
            if self._work_executor is not None:
                outcome = self._work_executor.submit(
                    "story", story, on_decision=self._on_work_decision)
            else:
                outcome = self.handle_story(story)
            story_results[outcome] = story_results.get(outcome, 0) + 1
            handled.append({"story_id": story.get("id"), "outcome": outcome})
        # 回收本轮中已完成的后台任务（metrics 接入点：outcome 已在回调里记日志）
        if self._work_executor is not None:
            for kind, wid, outcome in self._work_executor.drain_finished():
                log.debug("后台任务完成：%s #%s → %s", kind, wid, outcome)
        return {
            "reclaimed": reclaimed,
            "ticket_reclaimed": ticket_reclaimed,
            "story_reclaimed": story_reclaimed,
            "task_reclaimed": task_reclaimed,
            "recovered": recovered,
            "handled": handled,
            "counts": results,
            "ticket_counts": ticket_results,
            "story_counts": story_results,
        }

    def _on_work_decision(self, kind: str, work_id: int, outcome: str,
                          exc: BaseException | None) -> None:
        """后台工作项完成回调：仅记日志，metrics 由 main loop 在下一轮 drain。"""
        if exc is not None:
            log.warning("%s #%s 后台执行异常：%s", kind, work_id, exc)
        else:
            log.info("%s #%s 后台执行完成：%s", kind, work_id, outcome)

    def run_forever(self, stop: threading.Event | None = None,
                    max_cycles: int | None = None) -> int:
        """常驻轮询。``stop`` 用于优雅退出，``max_cycles`` 便于测试收敛。"""
        stop = stop or threading.Event()
        cycles = 0
        log.info("Worker 启动：api=%s agent=%s interval=%ss lease=%ss max_rounds=%s",
                 self.config.api_url, self.config.agent, self.config.poll_interval,
                 self.config.lease_seconds, self.config.max_rounds)
        while not stop.is_set():
            try:
                summary = self.poll_once()
                if summary["handled"] or summary["reclaimed"]:
                    log.info("本轮处理：%s（回收 %s）", summary["counts"], summary["reclaimed"])
            except Exception:
                log.exception("轮询周期异常，将在下个周期重试")
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            stop.wait(self.config.poll_interval)
        log.info("Worker 退出，共执行 %s 轮", cycles)
        return cycles

    # ---------- MQ 消费（P2） ----------

    #: 瞬时错误（网络抖动 / server 5xx）的退避序列；耗尽后仍失败才进死信
    MSG_RETRY_BACKOFF: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)

    def _transient_failure(self, pid: int, what: str,
                           err: Exception | str) -> None:
        """回查瞬时失败：按退避序列等待后抛 MessageRetry 让 broker requeue。

        连续超过 ``len(MSG_RETRY_BACKOFF)`` 次（约 1 分钟累计）仍失败才放弃，
        由调用方 return False 转入死信 —— server 长期宕机时不该无限空转。
        """
        n = self._msg_retries.get(pid, 0)
        if n >= len(self.MSG_RETRY_BACKOFF):
            self._msg_retries.pop(pid, None)
            log.error("提案 #%s %s 连续 %s 次瞬时失败，放弃重试转入死信：%s",
                      pid, what, n, err)
            return
        delay = self.MSG_RETRY_BACKOFF[n]
        self._msg_retries[pid] = n + 1
        log.warning("提案 #%s %s 瞬时失败（第 %s/%s 次）：%.0fs 后 requeue 重投（%s）",
                    pid, what, n + 1, len(self.MSG_RETRY_BACKOFF), delay, err)
        time.sleep(delay)
        raise mq.MessageRetry(f"proposal #{pid} {what} 瞬时失败") from None

    def handle_message(self, message: "mq.ProposalMessage") -> bool:
        """处理一条派发消息。返回 False 表示拒收，消息转入死信队列。

        消息只是提示，数据库才是事实源：一律先回查提案再决策。
        网络异常 / 5xx 属瞬时错误 → 抛 MessageRetry 触发 requeue（带退避）；
        仅消息体损坏或连续重试耗尽才落死信。
        """
        pid = message.proposal_id
        try:
            r = self._request("GET", f"/api/proposals/{pid}")
        except Exception as e:
            self._transient_failure(pid, "回查", e)
            return False
        self._msg_retries.pop(pid, None)  # 成功回查即清计数
        if r.status_code == 404:
            log.info("提案 #%s 已不存在，丢弃消息", pid)
            return True
        if r.status_code >= 500:
            self._transient_failure(pid, f"回查 HTTP {r.status_code}",
                                    r.text[:200])
            return False
        if r.status_code != 200:
            log.warning("提案 #%s 回查异常：%s %s，消息转入死信",
                        pid, r.status_code, r.text[:200])
            return False
        proposal = r.json()
        status = str(proposal.get("status") or "")
        if status not in ("queued", "answered"):
            log.info("提案 #%s 当前状态 %s 不可认领（已被处理或尚未就绪），丢弃消息",
                     pid, status)
            return True
        outcome = self.handle(proposal)
        log.info("提案 #%s 消费完成：%s", pid, outcome)
        return True

    def _maintenance_loop(self, publisher: "mq.ProposalPublisher",
                          stop: threading.Event) -> None:
        """后台维护：回收超租约（提案 + 转换请求 + Story/Task）+ 自愈重投。"""
        while not stop.wait(self.config.maintenance_interval):
            try:
                self.reclaim_stale()
                self.reclaim_stale_ticket_requests()
                self.reclaim_stale_stories()
                self.reclaim_stale_tasks()
                self.sweep(publisher)
            except Exception:
                log.exception("维护周期异常，将在下个周期重试")

    def _ticket_scan_loop(self, stop: threading.Event) -> None:
        """MQ 模式兜底：周期扫描 pending 转换请求（workflow 总线由 Workflow
        Worker ack，本 Worker 收不到 ticket_requested 事件）。"""
        while not stop.wait(self.config.poll_interval):
            try:
                for req in self.fetch_ticket_requests():
                    try:
                        self.handle_ticket_request(req)
                    except Exception:
                        log.exception("ticket 请求 #%s 处理异常", req.get("id"))
            except Exception:
                log.exception("ticket 扫描周期异常，将在下个周期重试")

    def _story_scan_loop(self, stop: threading.Event) -> None:
        """Story 编排扫描兜底（MQ 模式下周期扫描 confirmed Story）。"""
        while not stop.wait(self.config.poll_interval):
            try:
                for story in self.fetch_confirmed_stories():
                    try:
                        self.handle_story(story)
                    except Exception:
                        log.exception("Story #%s 处理异常", story.get("id"))
            except Exception:
                log.exception("Story 编排扫描周期异常，将在下个周期重试")

    def _start_keepers(self, stop: threading.Event, publisher) -> list[threading.Thread]:
        """启动维护 / ticket 扫描 / story 扫描 / 心跳 4 个后台线程。"""
        keeper = threading.Thread(
            target=self._maintenance_loop, args=(publisher, stop),
            name="proposal-worker-maintenance", daemon=True,
        )
        ticket_keeper = threading.Thread(
            target=self._ticket_scan_loop, args=(stop,),
            name="proposal-worker-ticket-scan", daemon=True,
        )
        story_keeper = threading.Thread(
            target=self._story_scan_loop, args=(stop,),
            name="proposal-worker-story-scan", daemon=True,
        )
        heartbeat_keeper = threading.Thread(
            target=self._agent_heartbeat_loop, args=(stop,),
            name="proposal-worker-agent-heartbeat", daemon=True,
        )
        for t in (keeper, ticket_keeper, story_keeper, heartbeat_keeper):
            t.start()
        return [keeper, ticket_keeper, story_keeper, heartbeat_keeper]

    def run_mq_forever(self, stop: threading.Event | None = None,
                       max_messages: int | None = None,
                       idle_timeout: float | None = None,
                       broker: Any | None = None,
                       publisher: "mq.ProposalPublisher | None" = None) -> dict:
        """MQ 竞争消费模式（P2，替换 P1 的 DB 轮询）。未配置 MQ 自动回退轮询。"""
        broker = broker if broker is not None else mq.build_broker(self.config.mq)
        if broker is None:
            log.warning("未配置 AGENTBOARD_MQ_URL（或 pika 不可用），回退 P1 轮询模式")
            cycles = self.run_forever(stop=stop)
            return {"mode": "poll", "cycles": cycles}

        stop = stop or threading.Event()
        publisher = publisher or mq.ProposalPublisher(self.config.mq)
        broker.declare_topology()
        log.info("Worker 以 MQ 模式启动：ns=%s prefetch=%s api=%s agent=%s",
                 self.config.mq.namespace, self.config.mq.prefetch,
                 self.config.api_url, self.config.agent)
        try:
            self.reclaim_stale()
            self.reclaim_stale_ticket_requests()
            self.reclaim_stale_stories()
            self.reclaim_stale_tasks()
        except Exception:
            log.exception("启动期回收超租约提案失败，继续消费")
        keepers = self._start_keepers(stop, publisher)
        try:
            stats = broker.consume(
                self.handle_message, max_messages=max_messages,
                idle_timeout=idle_timeout, stop=stop,
            )
        finally:
            stop.set()
            for t in keepers:
                t.join(timeout=2)
        stats["mode"] = "mq"
        log.info("Worker MQ 模式退出：%s", stats)
        return stats

    def _wf_broadcast_loop(self, broker: Any, stop: threading.Event) -> None:
        """竞争消费广播队列（task.available）。"""
        queue = mq.WorkflowTopology().broadcast_queue
        log.info("Agent 广播竞争线程启动：%s", queue)
        try:
            broker.consume(queue, self.handle_workflow_message, stop=stop)
        except Exception:
            log.exception("广播竞争消费异常退出")
        finally:
            try:
                broker.close()
            except Exception:
                pass

    def _agent_direct_loop(self, broker: Any, agent_id: str, stop: threading.Event) -> None:
        """消费本 agent 的定向队列（direct queue）。"""
        queue = mq.WorkflowTopology().agent_queue(agent_id)
        log.info("Agent 定向消费线程启动：%s", queue)
        try:
            broker.consume(queue, self.handle_workflow_message, stop=stop)
        except Exception:
            log.exception("定向队列消费异常退出")
        finally:
            try:
                broker.close()
            except Exception:
                pass

    def run_agent_mq_forever(self, agent_id: str,
                              stop: threading.Event | None = None,
                              max_messages: int | None = None,
                              idle_timeout: float | None = None,
                              broker: Any | None = None,
                              wf_broker: Any | None = None,
                              direct_broker: Any | None = None,
                              publisher: "mq.ProposalPublisher | None" = None) -> dict:
        """Agent MQ 消费模式：澄清竞争 + 任务广播竞争 + 定向 direct。"""
        if broker is None:
            broker = mq.build_broker(self.config.mq)
        if broker is None:
            log.warning("未配置 AGENTBOARD_MQ_URL，回退 P1 轮询模式")
            return {"mode": "poll", "cycles": self.run_forever(stop=stop)}
        stop = stop or threading.Event()
        publisher = publisher or mq.ProposalPublisher(self.config.mq)
        broker.declare_topology()
        self.config.agent_id = agent_id
        wf_topology = mq.WorkflowTopology()
        broadcast_broker = wf_broker or mq.PikaWorkflowBroker(self.config.mq)
        broadcast_broker.declare_topology()
        direct_b = direct_broker or mq.PikaWorkflowBroker(self.config.mq)
        direct_b.declare_topology()
        direct_b.declare_agent_queue(agent_id)
        log.info("Agent Worker(%s) MQ 模式启动：澄清竞争 + 广播竞争 + direct=%s",
                 agent_id, wf_topology.agent_queue(agent_id))
        try:
            self.reclaim_stale()
            self.reclaim_stale_ticket_requests()
            self.reclaim_stale_stories()
            self.reclaim_stale_tasks()
        except Exception:
            log.exception("启动期回收超租约提案失败，继续消费")
        keepers = self._start_keepers(stop, publisher)
        wf_threads = [
            threading.Thread(target=self._wf_broadcast_loop,
                             args=(broadcast_broker, stop), daemon=True),
            threading.Thread(target=self._agent_direct_loop,
                             args=(direct_b, agent_id, stop), daemon=True),
        ]
        for t in wf_threads:
            t.start()
        try:
            stats = broker.consume(
                self.handle_message, max_messages=max_messages,
                idle_timeout=idle_timeout, stop=stop,
            )
        finally:
            stop.set()
            for t in (keepers + wf_threads):
                t.join(timeout=2)
        stats["mode"] = "agent-mq"
        log.info("Agent Worker(%s) MQ 模式退出：%s", agent_id, stats)
        return stats
