"""WorkerCoordinator：统一工作项协调器（Unified Execution Model）。

单进程统一执行中枢：
1. 统一管理所有 WorkType 对应的 BaseWorkHandler 策略类；
2. 统一调度入口：dispatch(ExecutionCommand) -> ExecutionResult；
3. 通道隔离与 (work_type, entity_id) in-flight 去重；
4. 统一驱动源：支持轮询拉取（poll_once）与 MQ 事件流（handle_workflow_message）；
5. 自动化租约回收与健康检查维护。
6. （Phase 2 P1 修复）瞬时错误重试计数持久化到 DB（message_attempts 表），
   多 Worker / Worker restart / RabbitMQ requeue 不会让 attempt 归零。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from agentboard.core.infrastructure import messaging as mq
from . import heartbeat, maintenance
from .config import AgentDecision, AgentInvoker, WorkerConfig, WorkerError
from .contract import (
    ExecutionCommand,
    ExecutionResult,
    ExecutionStatus,
    UnknownWorkTypeError,
    WorkType,
)
from .handlers import BaseWorkHandler, build_handlers, build_work_type_registry
from .invokers import (
    CallableAgentInvoker,
    ComplianceEnforcingInvoker,
    RoutedSubprocessInvoker,
    SubprocessAgentInvoker,
    parse_agent_command_map,
    set_prompt_builder,
)

log = logging.getLogger("agentboard.worker.coordinator")

__all__ = ["WorkerCoordinator"]

WORKFLOW_RETRY_BACKOFF_SECONDS = (1, 2, 4, 8, 15, 30)


def _execution_id_from_retry_key(retry_key: tuple[str, str, int, int]) -> str:
    """从 retry_key 元组派生出稳定的 execution_id 字符串。"""
    return f"{retry_key[0]}:{retry_key[1]}:{retry_key[2]}:{retry_key[3]}"


class WorkerCoordinator:
    """统一 Worker 协调器：单进程统管全生命周期与异构工作项。"""

    _session_factory: Callable | None = None
    _msg_retries: dict[tuple[str, str, int, int], int] = {}

    def __init__(
        self,
        config: WorkerConfig,
        invoker: AgentInvoker | None = None,
        client: httpx.Client | None = None,
        registry: dict[WorkType, BaseWorkHandler] | None = None,
        session_factory: Callable | None = None,
    ):
        """
        Args:
            session_factory: 持久化 retry 计数用的 SQLAlchemy session factory。
                传 None 时回退到 in-memory dict（dev 模式 / 单 Worker）；
                生产（多 Worker / 跨重启）应传 ``agentboard.core.infrastructure.database.SessionLocal``
                以保证 attempt 跨进程一致。
        """
        self.config = config
        raw_invoker = invoker or self._default_invoker(config, prompt_builder=self.build_prompt_for)
        # Production CLI decisions are always guarded. CallableAgentInvoker is
        # an in-process test/embedding seam and does not represent a real CLI.
        if isinstance(raw_invoker, (SubprocessAgentInvoker, RoutedSubprocessInvoker)):
            self.invoker = ComplianceEnforcingInvoker(raw_invoker)
        else:
            self.invoker = raw_invoker
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=config.api_url,
            timeout=config.http_timeout,
            headers=({"Authorization": f"Bearer {config.token}"} if config.token else {}),
        )
        self._handlers_by_name = build_handlers(self.client, self.config)
        self.registry = registry or build_work_type_registry(
            self.client, self.config, handlers=self._handlers_by_name,
        )

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
            # P1 收口（2026-08-26）：AsyncWorkExecutor 接 coordinator 而非 handlers，
            # async 路径走统一执行内核（统一 error taxonomy + dispatch 去重）。
            self._work_executor = AsyncWorkExecutor(
                coordinator=self,
                max_concurrent=max_c,
                join_timeout=join_to,
            )

        # P2 P1 修复（2026-08-26）：retry 计数持久化。session_factory 为 None 时
        # 退化到 in-memory dict（dev 模式）；生产必须传 SessionLocal。
        self._session_factory = session_factory
        self._msg_retries = {}
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

    @classmethod
    def _default_invoker(
        cls, config: WorkerConfig, prompt_builder: Callable[[dict], str] | None = None,
    ) -> AgentInvoker:
        cmds = parse_agent_command_map()
        if cmds:
            try:
                return RoutedSubprocessInvoker(
                    commands=cmds, timeout=config.agent_timeout, prompt_builder=prompt_builder,
                )
            except ValueError as e:
                log.warning("RoutedSubprocessInvoker 初始化失败：%s", e)
        if not config.agent_cmd.strip():
            raise ValueError(
                "未配置 AGENTBOARD_WORKER_AGENT_COMMANDS / AGENTBOARD_WORKER_AGENT_CMD，"
                "且未显式传入 invoker"
            )
        return SubprocessAgentInvoker(
            config.agent_cmd, timeout=config.agent_timeout, prompt_builder=prompt_builder,
        )

    def close(self) -> None:
        if self._work_executor is not None:
            self._work_executor.shutdown()
        if self._owns_client:
            self.client.close()

    # ---------- Phase 2 P1：持久化 retry 计数 ----------

    def _get_attempt(self, execution_id: str) -> int:
        """读当前 attempt。session_factory=None → in-memory dict；否则 DB。"""
        if self._session_factory is None:
            return self._msg_retries.get(
                self._retry_key_from_execution_id(execution_id), 0,
            )
        try:
            from agentboard.features.scheduling.models import MessageAttempt
            s = self._session_factory()
            try:
                row = s.query(MessageAttempt).filter(
                    MessageAttempt.execution_id == execution_id,
                ).first()
                return int(row.attempt) if row else 0
            finally:
                s.close()
        except Exception as e:
            log.warning("读 message_attempt 失败（回退 in-memory）：%s", e)
            return 0

    def _set_attempt(
        self, execution_id: str, attempt: int, *,
        last_error: str = "",
        status: str = "pending",
        retry_key: tuple[str, str, int, int] | None = None,
    ) -> None:
        """写当前 attempt。session_factory=None → in-memory dict；否则 DB upsert。"""
        if self._session_factory is None:
            if retry_key is None:
                retry_key = self._retry_key_from_execution_id(execution_id)
            if attempt <= 0:
                self._msg_retries.pop(retry_key, None)
            else:
                self._msg_retries[retry_key] = attempt
            return
        try:
            from agentboard.features.scheduling.models import MessageAttempt
            from sqlalchemy import select
            s = self._session_factory()
            try:
                row = s.execute(
                    select(MessageAttempt).where(
                        MessageAttempt.execution_id == execution_id,
                    )
                ).scalar_one_or_none()
                if row is None:
                    row = MessageAttempt(
                        execution_id=execution_id,
                        attempt=attempt,
                        last_error=last_error[:1000],
                        status=status,
                    )
                    if retry_key is not None:
                        row.last_event = str(retry_key[0])
                        row.last_entity_type = str(retry_key[1])
                        row.last_entity_id = int(retry_key[2])
                        row.last_ref_id = int(retry_key[3])
                    s.add(row)
                else:
                    row.attempt = attempt
                    row.last_error = last_error[:1000]
                    row.status = status
                s.commit()
            finally:
                s.close()
        except Exception as e:
            log.warning("写 message_attempt 失败：%s", e)

    def _delete_attempt(self, retry_key: tuple[str, str, int, int]) -> None:
        """非 transient / dead-lettered / completed 时清掉 attempt。"""
        if self._session_factory is None:
            self._msg_retries.pop(retry_key, None)
            return
        try:
            from agentboard.features.scheduling.models import MessageAttempt
            from sqlalchemy import delete
            execution_id = _execution_id_from_retry_key(retry_key)
            s = self._session_factory()
            try:
                s.execute(
                    delete(MessageAttempt).where(
                        MessageAttempt.execution_id == execution_id,
                    )
                )
                s.commit()
            finally:
                s.close()
        except Exception as e:
            log.warning("删 message_attempt 失败：%s", e)

    @staticmethod
    def _retry_key_from_execution_id(execution_id: str) -> tuple[str, str, int, int]:
        """从 ``execution_id`` 字符串反解 retry_key 元组（仅 in-memory fallback 用）。"""
        parts = execution_id.split(":", 3)
        if len(parts) != 4:
            return ("", "", 0, 0)
        return (parts[0], parts[1], int(parts[2] or 0), int(parts[3] or 0))

    def _message_consumed(
        self,
        result: ExecutionResult,
        retry_key: tuple[str, str, int, int],
    ) -> bool:
        """Ack terminal outcomes and boundedly requeue transient failures.

        Phase 2 P1 修复（2026-08-26）：attempt 计数从进程内 dict 改 DB 持久化。
        session_factory=None → 旧 in-memory dict（dev 模式）；
        否则 → message_attempts 表，跨进程 / 跨重启一致。
        """
        execution_id = _execution_id_from_retry_key(retry_key)
        if result.status is not ExecutionStatus.FAILED_TRANSIENT:
            self._delete_attempt(retry_key)
            return True

        attempt = self._get_attempt(execution_id)
        if attempt >= len(WORKFLOW_RETRY_BACKOFF_SECONDS):
            self._set_attempt(
                execution_id, attempt, last_error=result.summary or "dead-lettered",
                status="dead_lettered", retry_key=retry_key,
            )
            log.error(
                "Workflow message exceeded transient retry limit; dead-lettering "
                "event=%s entity=%s#%s attempt=%s",
                retry_key[0], retry_key[1], retry_key[2], attempt,
            )
            return False

        delay = WORKFLOW_RETRY_BACKOFF_SECONDS[attempt]
        new_attempt = attempt + 1
        self._set_attempt(
            execution_id, new_attempt,
            last_error=result.summary or "transient",
            status="pending", retry_key=retry_key,
        )
        if delay:
            time.sleep(delay)
        raise mq.MessageRetry(
            f"transient execution failure; retry {new_attempt}/"
            f"{len(WORKFLOW_RETRY_BACKOFF_SECONDS)}"
        )

    @staticmethod
    def _workflow_retry_key(msg: mq.WorkflowMessage) -> tuple[str, str, int, int]:
        return (
            str(getattr(msg, "event", "")),
            str(getattr(msg, "entity_type", "")),
            int(getattr(msg, "entity_id", 0) or 0),
            int(getattr(msg, "ref_id", 0) or 0),
        )

    def __enter__(self) -> WorkerCoordinator:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- 统一分发与执行 ----------

    def dispatch(self, command: ExecutionCommand) -> ExecutionResult:
        """核心统一调度入口：根据 command.work_type 分发到对应的 Handler 执行。

        Review 2026-08-26 P1：dispatch 入口把 ExecutionCommand 注入 context["_command"]，
        激活 invokers.build_prompt 的 PreparedExecution 路径——
        BehaviorResolver + ContextBuilder + PromptBuilder 在 production 真正生效。
        """
        try:
            canonical_type = WorkType.canonical_for(command.work_type)
        except UnknownWorkTypeError as exc:
            log.error("Rejecting execution with unknown work type: %s", exc)
            return ExecutionResult.permanent_failure(
                command.execution_id, str(exc), action="fail",
            )
        handler = self.registry.get(canonical_type) or self.registry.get(command.work_type)
        if not handler:
            return ExecutionResult.permanent_failure(
                command.execution_id,
                f"No handler registered for work_type: {command.work_type}",
                action="fail",
            )

        inflight_key = (canonical_type.value, command.entity_id)
        compatibility_key = (str(command.work_type), command.entity_id)
        with self._inflight_lock:
            if inflight_key in self._inflight or compatibility_key in self._inflight:
                log.info("工作项 %s 正在执行中，跳过重复 dispatch", inflight_key)
                return ExecutionResult.skipped(
                    command.execution_id, "In-flight duplicate skipped",
                )
            self._inflight.add(inflight_key)

        # P1 关键：让下游 invoker.invoke() / invoke_with_prompt() 走 PreparedExecution 路径
        # （即真正调用 BehaviorResolver / ContextBuilder / PromptBuilder）
        context = dict(command.context)
        if "_command" not in context or command.work_type != canonical_type:
            context["_command"] = command
        command = command.model_copy(
            update={"work_type": canonical_type, "context": context},
        )

        try:
            log.info("开始执行命令 [exec_id=%s, work_type=%s, entity_id=%s]",
                     command.execution_id, command.work_type, command.entity_id)
            result = handler.execute_command(command, self.invoker)
            log.info("命令执行完成 [exec_id=%s, status=%s, action=%s]",
                     command.execution_id, result.status, result.action)
            return result
        except Exception as e:
            log.exception("命令执行抛出异常 [exec_id=%s]: %s", command.execution_id, e)
            return ExecutionResult.from_exception(command.execution_id, e, action="fail")
        finally:
            with self._inflight_lock:
                self._inflight.discard(inflight_key)

    def build_prompt_for(self, context: dict) -> str:
        """根据 WorkType 路由到对应 Handler 的 Prompt 渲染器。

        Review 2026-08-26 修正：原先按 ``action`` 字符串反查 WorkType，导致
        ``DESIGN_REVIEW`` / ``IMPLEMENTATION_REVIEW`` / ``QA_REVIEW`` 全被
        ``"review_task"`` 抹平。现在优先按 ``context["work_type"]`` 字符串
        直接查 registry；缺失时再 fallback 到 action 字符串（保持向后兼容，
        旧 invoker / 旧 message 仍能 work）。

        长期目标：所有 caller 都传 work_type，action 字符串将在下一轮彻底删除。
        """
        # 1. 优先按 WorkType 字符串精确路由
        wt_str = context.get("work_type")
        if wt_str is not None:
            wt = WorkType.canonical_for(wt_str)
            h = self.registry.get(wt)
            if h:
                return h.build_prompt(context)
            raise UnknownWorkTypeError(f"No handler registered for work_type: {wt_str}")

        # 2. 兼容路径：按 action 字符串反查（仅用于历史 invoker / 旧 MQ 消息）
        action = context.get("action")
        if action == "review_task":
            h = self.registry.get(WorkType.IMPLEMENTATION_REVIEW) or self.registry.get(WorkType.TASK_REVIEW)
            if h:
                return h.build_prompt(context)
        elif action == "owner_response":
            h = self.registry.get(WorkType.TASK_RESPOND)
            if h:
                return h.build_prompt(context)
        elif action in ("process_story", "process_task") or context.get("story_id"):
            h = self.registry.get(WorkType.IMPLEMENTATION) or self.registry.get(WorkType.TASK_IMPLEMENT)
            if h:
                return h.build_prompt(context)
        elif context.get("ticket_request_id") or (
            context.get("type") in ("auto", "epic", "story", "task", "bug") and "ticket_type" in context
        ):
            h = self.registry.get(WorkType.PROPOSAL_CONVERT)
            if h:
                return h.build_prompt(context)
        # 3. 终极 fallback：clarify
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
        story_h = self.registry.get(WorkType.IMPLEMENTATION)
        if story_h and hasattr(story_h, "fetch"):
            for st in story_h.fetch():
                cmd = ExecutionCommand(
                    execution_id=f"story_{st.get('id')}",
                    work_type=WorkType.IMPLEMENTATION,
                    entity_type="story",
                    entity_id=st.get("id", 0),
                    context=st,
                )
                res = self.dispatch(cmd)
                if res.status == "success":
                    stats["stories"] += 1

        # 4. 租约超期回收
        # In deployments without RabbitMQ, ``task.available`` is not
        # delivered to workers.  Opt-in polling closes that gap while being
        # restricted to locally mapped projects.
        if os.getenv("AGENTBOARD_WORKER_TASK_POLL", "0").strip().lower() in {
            "1", "true", "yes",
        }:
            stats["tasks"] = self._poll_available_tasks()

        stale_stories = maintenance.reclaim_stale_stories(self.client, self.config)
        stale_tasks = maintenance.reclaim_stale_tasks(self.client, self.config)
        stats["stale_stories"] = stale_stories
        stats["stale_tasks"] = stale_tasks

        return stats

    def _mapped_project_ids(self) -> list[int]:
        raw = os.getenv("AGENTBOARD_WORKER_PROJECT_IDS", "").strip()
        if raw:
            return [int(item) for item in raw.split(",") if item.strip().isdigit()]
        mapping = os.getenv("AGENTBOARD_LOCAL_MAPPINGS", "").strip()
        path = Path(mapping) if mapping else Path.cwd() / "tmp" / "project-mappings.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [int(pid) for pid in (data.get("projects") or {}).keys()]
        except (OSError, ValueError, TypeError):
            return []

    def _poll_available_tasks(self) -> int:
        project_ids = self._mapped_project_ids()
        if not project_ids:
            log.warning(
                "AGENTBOARD_WORKER_TASK_POLL 已启用，但没有项目映射；跳过 todo Task 扫描"
            )
            return 0
        handled = 0
        for project_id in project_ids:
            try:
                response = self.client.get(
                    "/api/tasks",
                    params={
                        "project_id": project_id,
                        "status": "todo",
                        "limit": max(1, self.config.batch_size),
                    },
                )
                response.raise_for_status()
                tasks = response.json() or []
            except Exception as exc:
                log.warning("扫描 project#%s todo Task 失败：%s", project_id, exc)
                continue
            items = tasks if isinstance(tasks, list) else tasks.get("items", [])
            for task in items:
                task_id = task.get("id")
                if not task_id:
                    continue
                try:
                    claimed = self.client.post(
                        f"/api/tasks/{task_id}/claim",
                        json={"agent": self.config.agent},
                    )
                except Exception as exc:
                    log.warning("认领 Task #%s 失败：%s", task_id, exc)
                    continue
                if claimed.status_code != 200:
                    continue
                task_payload = claimed.json() or task
                work_type = WorkType.from_task(task_payload.get("type"), is_review=False)
                result = self.dispatch(ExecutionCommand(
                    execution_id=f"task_{task_id}_{int(time.time())}",
                    work_type=work_type,
                    entity_type="task",
                    entity_id=int(task_id),
                    context={
                        "event": "task.available",
                        "work_type": work_type.value,
                        "task": task_payload,
                    },
                ))
                if result.status is ExecutionStatus.SUCCESS:
                    handled += 1
        return handled

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
            return self._message_consumed(res, self._workflow_retry_key(msg))

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
            return self._message_consumed(res, self._workflow_retry_key(msg))

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
            return self._message_consumed(res, self._workflow_retry_key(msg))

        return True
