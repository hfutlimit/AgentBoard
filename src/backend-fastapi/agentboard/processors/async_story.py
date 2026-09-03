"""通用工作项后台执行器（2026-08-26 根治 process_story 阻塞 main loop）。

背景：
    原 ``ProposalProcessor.poll_once`` 同步串行调各 Handler.handle，而 ``handle``
    会同步阻塞在 ``invoker.invoke`` 上等子 agent（codebuddy 跑真实 Story 任务
    实测分钟级，AGENTBOARD_WORKER_AGENT_TIMEOUT 默认 900s）。这导致任何 1 个
    慢工作项都会把整个 worker main loop 挂住，期间其余 pending 工作项全部饿死。

本模块：
    把 handler 执行扔到后台线程池，main loop 立即返回；close() 时
    ``shutdown()`` join 所有 in-flight，超时强制收尾。
    2026-08-26 Stage 0 泛化：从「仅 Story」扩展到 clarify / ticket / story
    三域共用同一套 submit / 去重 / 收尾机制。

设计取舍：
- **per-kind 信号量**（默认各 1）：同 invoker 的子进程并发受控；且慢 Story
  不会占住快通道的槽位 —— clarify/ticket 不被长任务饿死（这是当初单信号量
  方案的实测缺陷）。
- **(kind, id) in-flight 去重**：pending 工作项每轮都会被 fetch 出来，不去重
  时同一任务会在信号量后排成长队重复提交（实测堆积缺陷）；去重后同键重复
  submit 直接拒绝，返回 "duplicate_inflight"。
- Handler 自己的 ``_story_attempts`` / ``claim`` / CAS 认领已在服务端与本地
  双重仲裁，多线程安全。
- 失败回退：后台任务抛异常或超时都进对应 handler 的 fail 路径；进程被强杀
  时已 claim 的工作项由服务端租约回收兜底 —— proposal 走
  ``/api/proposals/reclaim-stale``，Story/Task 走
  ``/api/stories|tasks/reclaim-stale``（worker 维护循环每轮调用，
  见 maintenance.py）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger("agentboard.processors.work_async")

# 回调签名：on_decision(kind, work_item_id, outcome, exc_or_None)
OnDecision = Callable[[str, int, str, BaseException | None], None]


class _SerializedInvoker:
    """Protect invokers whose implementations are not guaranteed thread-safe."""

    def __init__(self, invoker: Any):
        self._invoker = invoker
        self._lock = threading.Lock()

    def invoke(self, context: dict) -> Any:
        with self._lock:
            return self._invoker.invoke(context)

    def invoke_with_prompt(self, prompt: str, context: dict) -> Any:
        with self._lock:
            return self._invoker.invoke_with_prompt(prompt, context)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._invoker, name)


class AsyncWorkExecutor:
    """后台线程池执行 ProcessorCoordinator.dispatch(command)，不阻塞 main loop。

    2026-08-26 P1 架构收口：原版直接调 ``handler.handle(work_item, invoker)``，
    绕过统一执行内核 → 临时 timeout 走 async path 不会 retry（被算成 Story 失败
    计数 +1，三次后 blocked）。新版本构造 ``ExecutionCommand`` 调
    ``coordinator.dispatch``，错误分类与 polling / MQ 路径完全一致。
    """

    #: 支持的业务域（必须与 handlers.build_handlers 的 key 一致）
    KINDS: tuple[str, ...] = ("clarify", "ticket", "story")

    # kind → (WorkType, entity_type) 映射（与 coordinator.poll_once 对齐）
    _KIND_WT_MAP: dict[str, tuple[Any, str]] = {}

    def __init__(self, coordinator: Any = None,
                 invoker: Any = None, handlers: dict[str, Any] | None = None,
                 max_concurrent: int = 1,
                 join_timeout: float = 30.0):
        """初始化（两种签名兼容）：

        - **新签名 (P1 推荐)**: ``AsyncWorkExecutor(coordinator, max_concurrent=...)``
          —— 通过 ``coordinator.dispatch()`` 走统一执行内核。
        - **旧签名 (deprecated)**: ``AsyncWorkExecutor(invoker, handlers, ...)``
          —— 仍可工作但路径被分叉，新代码不要用。
        """
        import warnings
        if coordinator is not None and handlers is None:
            # 新签名：coordinator-based
            self._coordinator = coordinator
            self._invoker = coordinator.invoker
        elif handlers is not None and invoker is not None:
            # 旧签名：handler-based（fallback 走旧 handle 路径，仅用于未升级
            # 测试 / 历史兼容；新部署全部走 coordinator）
            warnings.warn(
                "AsyncWorkExecutor(invoker=, handlers=) is deprecated; "
                "use AsyncWorkExecutor(coordinator=) so async path joins the "
                "unified execution kernel (Phase 1 P1, 2026-08-26).",
                DeprecationWarning,
                stacklevel=2,
            )
            self._coordinator = None
            self._invoker = _SerializedInvoker(invoker)
            self._handlers = handlers
        else:
            raise TypeError(
                "AsyncWorkExecutor requires either (coordinator=) or "
                "(invoker=, handlers=)"
            )
        # 延迟初始化 kind→work_type 映射（避免循环 import contract）
        if not AsyncWorkExecutor._KIND_WT_MAP:
            from .contract import WorkType as _WT
            AsyncWorkExecutor._KIND_WT_MAP = {
                "clarify": (_WT.PROPOSAL_CLARIFY, "proposal"),
                "ticket": (_WT.PROPOSAL_CONVERT, "proposal"),
                "story": (_WT.IMPLEMENTATION, "story"),
            }
        self._join_timeout = max(0.0, join_timeout)
        # per-kind 串行化：慢域不占快域槽位（同 invoker 不假定线程安全）
        self._sems = {
            k: threading.BoundedSemaphore(max(1, max_concurrent))
            for k in self.KINDS
        }
        # in-flight 追踪：(kind, id) → thread；主线程注册/查询，回调里清
        self._inflight: dict[tuple[str, int], threading.Thread] = {}
        # 已完成待取：(kind, id, outcome)，drain_finished 消费
        self._finished: list[tuple[str, int, str]] = []
        self._lock = threading.Lock()
        # 关闭标志：submit 后立即拒绝新任务
        self._closed = False

    def submit(self, kind: str, work_item: dict,
               on_decision: OnDecision | None = None) -> str:
        """立即返回；后台线程执行对应命令。in-flight 重复提交被拒绝。"""
        if kind not in self.KINDS:
            return f"rejected_unknown_kind:{kind}"
        wid = work_item.get("id")
        if wid is None:
            return "rejected_invalid_work_item"
        key = (kind, wid)
        with self._lock:
            if self._closed:
                return "rejected_closed"
            if key in self._inflight:
                return "duplicate_inflight"
            if not self._sems[kind].acquire(blocking=False):
                return "deferred_capacity"
            t = threading.Thread(
                target=self._run_one, args=(kind, work_item, on_decision),
                name=f"work-async-{kind}-{wid}", daemon=True,
            )
            self._inflight[key] = t
            try:
                t.start()
            except BaseException:
                self._inflight.pop(key, None)
                self._sems[kind].release()
                raise
        return "submitted"

    def _run_one(self, kind: str, work_item: dict,
                 on_decision: OnDecision | None) -> None:
        wid = work_item.get("id")
        outcome = ""
        exc: BaseException | None = None
        try:
            if self._coordinator is not None:
                # 新路径：构造 ExecutionCommand 走 coordinator.dispatch
                outcome = self._dispatch_via_coordinator(kind, work_item)
            else:
                # 旧路径（deprecated）：直接调 handler.handle
                outcome = self._dispatch_via_legacy_handler(kind, work_item)
        except BaseException as e:  # noqa: BLE001 —— 后台必须吞掉避免线程死
            exc = e
            log.exception("%s #%s 后台执行异常", kind, wid)
        finally:
            with self._lock:
                self._inflight.pop((kind, wid), None)
                self._finished.append((kind, wid, outcome))
                self._sems[kind].release()
            if on_decision is not None:
                try:
                    on_decision(kind, wid, outcome, exc)
                except Exception:
                    log.exception("on_decision 回调异常（%s #%s）", kind, wid)

    def _dispatch_via_coordinator(self, kind: str, work_item: dict) -> str:
        """构造 ExecutionCommand 走 coordinator.dispatch（统一执行内核入口）。"""
        from .contract import ExecutionCommand
        wt, entity_type = self._KIND_WT_MAP[kind]
        entity_id = int(work_item.get("id", 0) or 0)
        cmd = ExecutionCommand(
            execution_id=f"async_{kind}_{entity_id}",
            work_type=wt,
            entity_type=entity_type,
            entity_id=entity_id,
            context=dict(work_item),
        )
        result = self._coordinator.dispatch(cmd)
        return str(result.action or "dispatched")

    def _dispatch_via_legacy_handler(self, kind: str, work_item: dict) -> str:
        """旧路径（deprecated）：直接调 handler.handle。仅供未迁移测试用。"""
        handler = self._handlers.get(kind)
        if handler is None:
            return "no_handler"
        return str(handler.handle(work_item, self._invoker))

    def inflight_count(self, kind: str | None = None) -> int:
        """当前在跑的后台任务数（调试/测试用）。"""
        with self._lock:
            if kind is None:
                return len(self._inflight)
            return sum(1 for k, _ in self._inflight if k == kind)

    def drain_finished(self) -> list[tuple[str, int, str]]:
        """取走已完成的 (kind, id, outcome) 列表（metrics 接入点）。"""
        with self._lock:
            out = list(self._finished)
            self._finished.clear()
        return out

    def shutdown(self) -> bool:
        """关闭入口：拒绝新任务，等待 in-flight 完成。"""
        with self._lock:
            self._closed = True
            threads = list(self._inflight.values())
        if not threads:
            return True
        log.info("AsyncWorkExecutor 收尾：等 %d 个 in-flight 任务完成（≤%ss）",
                 len(threads), self._join_timeout)
        deadline = time.time() + self._join_timeout
        for t in threads:
            remaining = max(0.0, deadline - time.time())
            t.join(timeout=remaining)
            if t.is_alive():
                log.warning("后台线程 %s 在 join 超时后仍存活，强制退场"
                            "（子进程随主进程回收）", t.name)


        return not any(t.is_alive() for t in threads)

# 向后兼容别名（2026-08-26 前仅有 Story 域时的类名）
_StoryAsyncExecutor = AsyncWorkExecutor
