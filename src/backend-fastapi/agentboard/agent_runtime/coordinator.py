"""WorkerCoordinator：统一工作项协调器（Unified Execution Model）。

单进程统一执行中枢：
1. 统一管理所有 WorkType 对应的 BaseWorkHandler 策略类；
2. 统一调度入口：dispatch(ExecutionCommand) -> ExecutionResult；
3. 通道隔离与 (work_type, entity_id) in-flight 去重；
4. 统一驱动源：支持轮询拉取（poll_once）与 MQ 事件流（handle_workflow_message）；
5. 自动化租约回收与健康检查维护。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import httpx

from agentboard.core.infrastructure import messaging as mq
from . import heartbeat, maintenance
from .config import AgentDecision, AgentInvoker, WorkerConfig, WorkerError
from .contract import ExecutionCommand, ExecutionResult, WorkType
from .handlers import BaseWorkHandler, build_handlers, build_work_type_registry
from .invokers import (
    CallableAgentInvoker,
    RoutedSubprocessInvoker,
    SubprocessAgentInvoker,
    parse_agent_command_map,
    set_prompt_builder,
)

log = logging.getLogger("agentboard.worker.coordinator")

__all__ = ["WorkerCoordinator"]


class WorkerCoordinator:
    """统一 Worker 协调器：单进程统管全生命周期与异构工作项。"""

    def __init__(
        self,
        config: WorkerConfig,
        invoker: AgentInvoker | None = None,
        client: httpx.Client | None = None,
        registry: dict[WorkType, BaseWorkHandler] | None = None,
    ):
        self.config = config
        self.invoker = invoker or self._default_invoker(config)
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=config.api_url,
            timeout=config.http_timeout,
            headers=({"Authorization": f"Bearer {config.token}"} if config.token else {}),
        )
        self._handlers_by_name = build_handlers(self.client, self.config)
        self.registry = registry or build_work_type_registry(self.client, self.config)

        # 提示词委托注入
        set_prompt_builder(self.build_prompt_for)

        # 心跳与并发控制
        self._last_heartbeat_ts: float = 0.0
        self._inflight: set[tuple[str, int]] = set()
        self._inflight_lock = threading.Lock()

        # 后台工作项执行器
        self._work_executor = None
        if getattr(config, "async_story_executor", False):
            from .async_story import AsyncWorkExecutor
            max_c = max(1, int(getattr(config, "async_story_max_concurrent", 1)))
            join_to = float(getattr(config, "async_story_join_timeout", 30.0))
            self._work_executor = AsyncWorkExecutor(
                invoker=self.invoker,
                handlers=self._handlers_by_name,
                max_concurrent=max_c,
                join_timeout=join_to,
            )

        # 瞬时错误重试记录
        self._msg_retries: dict[int, int] = {}
        self._validate_lease_vs_timeout()

    def _validate_lease_vs_timeout(self) -> None:
        lease = int(self.config.lease_seconds)
        timeout = int(self.config.agent_timeout)
        if lease <= timeout:
            log.warning(
                "lease_seconds(%s) <= agent_timeout(%s)：执行中的任务可能在完成前被租约回收。"
                "建议 lease >= 2*agent_timeout",
                lease, timeout,
            )

    @staticmethod
    def _default_invoker(config: WorkerConfig) -> AgentInvoker:
        cmds = parse_agent_command_map()
        if cmds:
            try:
                return RoutedSubprocessInvoker(commands=cmds, timeout=config.agent_timeout)
            except ValueError as e:
                log.warning("RoutedSubprocessInvoker 初始化失败：%s", e)
        if not config.agent_cmd.strip():
            raise ValueError(
                "未配置 AGENTBOARD_WORKER_AGENT_COMMANDS / AGENTBOARD_WORKER_AGENT_CMD，"
                "且未显式传入 invoker"
            )
        return SubprocessAgentInvoker(config.agent_cmd, timeout=config.agent_timeout)

    def close(self) -> None:
        if self._work_executor is not None:
            self._work_executor.shutdown()
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> WorkerCoordinator:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- 统一分发与执行 ----------

    def dispatch(self, command: ExecutionCommand) -> ExecutionResult:
        """核心统一调度入口：根据 command.work_type 分发到对应的 Handler 执行。"""
        handler = self.registry.get(command.work_type)
        if not handler:
            return ExecutionResult.failure(
                command.execution_id,
                f"No handler registered for work_type: {command.work_type}",
                action="fail",
            )

        inflight_key = (str(command.work_type), command.entity_id)
        with self._inflight_lock:
            if inflight_key in self._inflight:
                log.info("工作项 %s 正在执行中，跳过重复 dispatch", inflight_key)
                return ExecutionResult.failure(
                    command.execution_id,
                    "In-flight duplicate skipped",
                    action="skipped",
                )
            self._inflight.add(inflight_key)

        try:
            log.info("开始执行命令 [exec_id=%s, work_type=%s, entity_id=%s]",
                     command.execution_id, command.work_type, command.entity_id)
            result = handler.execute_command(command, self.invoker)
            log.info("命令执行完成 [exec_id=%s, status=%s, action=%s]",
                     command.execution_id, result.status, result.action)
            return result
        except Exception as e:
            log.exception("命令执行抛出异常 [exec_id=%s]: %s", command.execution_id, e)
            return ExecutionResult.failure(command.execution_id, str(e), action="fail")
        finally:
            with self._inflight_lock:
                self._inflight.discard(inflight_key)

    def build_prompt_for(self, context: dict) -> str:
        """根据上下文动作反查对应 Handler 的 Prompt 渲染器。"""
        action = context.get("action")
        if action == "review_task":
            h = self.registry.get(WorkType.TASK_REVIEW)
            if h:
                return h.build_prompt(context)
        elif action == "owner_response":
            h = self.registry.get(WorkType.TASK_RESPOND)
            if h:
                return h.build_prompt(context)
        elif action in ("process_story", "process_task") or context.get("story_id"):
            h = self.registry.get(WorkType.TASK_IMPLEMENT)
            if h:
                return h.build_prompt(context)
        elif context.get("ticket_request_id") or (context.get("type") in ("epic", "story", "task", "bug") and "ticket_type" in context):
            h = self.registry.get(WorkType.PROPOSAL_CONVERT)
            if h:
                return h.build_prompt(context)
        # 默认回退澄清 Prompt
        clarify_h = self.registry.get(WorkType.PROPOSAL_CLARIFY)
        return clarify_h.build_prompt(context) if clarify_h else ""

    # ---------- 轮询拉取与生命周期维护 ----------

    def poll_once(self) -> dict[str, int]:
        """单轮全域扫描：澄清 -> 转化 -> 任务实现 -> 租约回收。"""
        stats = {
            "clarified": 0,
            "converted": 0,
            "stories": 0,
            "tasks": 0,
            "stale_stories": 0,
            "stale_tasks": 0,
        }

        # 1. 澄清提案
        clarify_h = self.registry.get(WorkType.PROPOSAL_CLARIFY)
        if clarify_h and hasattr(clarify_h, "fetch"):
            for p in clarify_h.fetch():
                cmd = ExecutionCommand(
                    execution_id=f"proposal_{p.get('id')}",
                    work_type=WorkType.PROPOSAL_CLARIFY,
                    entity_type="proposal",
                    entity_id=p.get("id", 0),
                    context=p,
                )
                res = self.dispatch(cmd)
                if res.status == "success":
                    stats["clarified"] += 1

        # 2. 工单转化
        ticket_h = self.registry.get(WorkType.PROPOSAL_CONVERT)
        if ticket_h and hasattr(ticket_h, "fetch"):
            for req in ticket_h.fetch():
                cmd = ExecutionCommand(
                    execution_id=f"ticket_{req.get('id')}",
                    work_type=WorkType.PROPOSAL_CONVERT,
                    entity_type="proposal",
                    entity_id=req.get("id", 0),
                    context=req,
                )
                res = self.dispatch(cmd)
                if res.status == "success":
                    stats["converted"] += 1

        # 3. Story 推进
        story_h = self.registry.get(WorkType.TASK_IMPLEMENT)
        if story_h and hasattr(story_h, "fetch"):
            for st in story_h.fetch():
                cmd = ExecutionCommand(
                    execution_id=f"story_{st.get('id')}",
                    work_type=WorkType.TASK_IMPLEMENT,
                    entity_type="story",
                    entity_id=st.get("id", 0),
                    context=st,
                )
                res = self.dispatch(cmd)
                if res.status == "success":
                    stats["stories"] += 1

        # 4. 租约超期回收
        stale_stories = maintenance.reclaim_stale_stories(self.client, self.config)
        stale_tasks = maintenance.reclaim_stale_tasks(self.client, self.config)
        stats["stale_stories"] = stale_stories
        stats["stale_tasks"] = stale_tasks

        return stats

    # ---------- MQ 事件流驱动 ----------

    def handle_workflow_message(self, msg: mq.WorkflowMessage) -> bool:
        """处理来自 MQ 的 workflow 广播与定向消息。"""
        event = getattr(msg, "event", "")
        entity_id = getattr(msg, "entity_id", 0)

        # 1. 评审请求定向事件
        if event in ("task.review_requested", "review.requested"):
            task_type = getattr(msg, "task_type", None) or (getattr(msg, "context", {}) or {}).get("type")
            work_type = WorkType.from_task(task_type, is_review=True)
            cmd = ExecutionCommand(
                execution_id=f"review_{entity_id}_{getattr(msg, 'message_id', 0)}",
                work_type=work_type,
                entity_type="task",
                entity_id=entity_id,
                context={"event": event, "work_type": work_type.value},
            )
            res = self.dispatch(cmd)
            return res.status == "success"

        # 2. 评审驳回 / 重新激活开发事件 (Re-activate implementation attempt)
        if event in ("task.rejected", "comment.replied"):
            task_type = getattr(msg, "task_type", None) or (getattr(msg, "context", {}) or {}).get("type")
            work_type = WorkType.from_task(task_type, is_review=False)
            attempt = int(getattr(msg, "ref_id", 0) or 1)
            cmd = ExecutionCommand(
                execution_id=f"rework_{entity_id}_{attempt}_{getattr(msg, 'message_id', 0)}",
                work_type=work_type,
                entity_type="task",
                entity_id=entity_id,
                attempt=attempt,
                context={"event": event, "work_type": work_type.value},
            )
            res = self.dispatch(cmd)
            return res.status == "success"

        # 3. 任务可认领事件（DAG 解锁后广播）
        if event == "task.available":
            task_type = getattr(msg, "task_type", None) or (getattr(msg, "context", {}) or {}).get("type")
            work_type = WorkType.from_task(task_type, is_review=False)
            cmd = ExecutionCommand(
                execution_id=f"task_{entity_id}_{getattr(msg, 'message_id', 0)}",
                work_type=work_type,
                entity_type="task",
                entity_id=entity_id,
                context={"event": event, "work_type": work_type.value},
            )
            res = self.dispatch(cmd)
            return res.status == "success"

        return True
