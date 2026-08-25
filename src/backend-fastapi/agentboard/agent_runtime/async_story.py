"""Story 后台执行器（2026-08-26 根治 process_story 阻塞 main loop）。

背景：
    原 ``ProposalWorker.poll_once`` 同步串行调 ``StoryHandler.handle``，
    而 ``handle`` 会同步阻塞在 ``invoker.invoke`` 上等子 agent（codebuddy 跑
    真实 Story 任务实测分钟级，AGENTBOARD_WORKER_AGENT_TIMEOUT 默认 600s）。
    这导致任何 1 个慢 Story 都会把整个 worker main loop 挂住，期间
    pending proposal / answered / ticket_request 全部饿死。

本模块：
    把 process_story 扔到后台线程池，main loop 立即返回；close() 时
    ``shutdown()`` join 所有 in-flight，超时强制收尾。

设计取舍（2026-08-26）：
- **同一 invoker 不假定线程安全**（SubprocessAgentInvoker 内部没有 lock），
  所以默认 max_concurrent=1（信号量串行化），避免引入并发问题。
- StoryHandler 自己的 ``_story_attempts`` / ``claim`` / ``_story_fail_counts``
  已在 2026-08-26 加 ``_async_lock`` 保护，多线程安全。
- 失败回退：后台任务抛异常或超时都进 StoryHandler._story_fail 路径；
  进程被强杀时已 claim 的 story 由 ``reclaim_stale`` 租约到期自动回退。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger("agentboard.worker.story_async")

# 回调签名：on_decision(story_id, outcome, exc_or_None)
OnDecision = Callable[[int, str, BaseException | None], None]


class _StoryAsyncExecutor:
    """后线程池执行 StoryHandler.handle，不阻塞 main loop。"""

    def __init__(self, invoker: Any, handlers: dict[str, Any],
                 agent: str, max_concurrent: int = 1,
                 join_timeout: float = 30.0):
        self._invoker = invoker
        self._handlers = handlers
        self._agent = agent
        self._sem = threading.BoundedSemaphore(max(1, max_concurrent))
        self._join_timeout = max(0.0, join_timeout)
        # in-flight 追踪：只在主线程注册，回调里清；用 list 保护以应对关闭时的并发
        self._inflight: list[threading.Thread] = []
        self._lock = threading.Lock()
        # 关闭标志：submit 后立即拒绝新任务
        self._closed = False

    def submit(self, story: dict, on_decision: OnDecision | None = None) -> str:
        """立即返回；后台线程执行 StoryHandler.handle。返回 'submitted'。"""
        if self._closed:
            return "rejected_closed"
        sid = story.get("id")
        t = threading.Thread(
            target=self._run_one, args=(story, on_decision),
            name=f"story-async-{sid}", daemon=True,
        )
        with self._lock:
            self._inflight.append(t)
        t.start()
        return "submitted"

    def _run_one(self, story: dict, on_decision: OnDecision | None) -> None:
        sid = story.get("id")
        outcome = ""
        exc: BaseException | None = None
        try:
            with self._sem:  # 串行化（同 invoker 不假定线程安全）
                handler = self._handlers.get("story")
                if handler is None:
                    outcome = "no_handler"
                else:
                    outcome = handler.handle(story, self._invoker)
        except BaseException as e:  # noqa: BLE001 —— 后台必须吞掉避免线程死
            exc = e
            log.exception("Story #%s 后台执行异常", sid)
        finally:
            with self._lock:
                cur = threading.current_thread()
                if cur in self._inflight:
                    self._inflight.remove(cur)
            if on_decision is not None:
                try:
                    on_decision(sid, outcome, exc)
                except Exception:
                    log.exception("on_decision 回调异常（Story #%s）", sid)

    def drain_finished(self) -> list[tuple[int, str]]:
        """本轮回收已完成的后台任务（仅清理，不改 main flow）。"""
        # 当前用 callback 模式上报；本函数保留为后续 metrics 接入点。
        return []

    def shutdown(self) -> None:
        """关闭入口：拒绝新任务，等待 in-flight 完成。"""
        with self._lock:
            self._closed = True
            threads = list(self._inflight)
        if not threads:
            return
        log.info("StoryAsyncExecutor 收尾：等 %d 个 in-flight Story 完成（≤%ss）",
                 len(threads), self._join_timeout)
        deadline = time.time() + self._join_timeout
        for t in threads:
            remaining = max(0.0, deadline - time.time())
            t.join(timeout=remaining)
            if t.is_alive():
                log.warning("Story 后台线程 %s 在 join 超时后仍存活，强制退场（子进程随主进程回收）",
                            t.name)
