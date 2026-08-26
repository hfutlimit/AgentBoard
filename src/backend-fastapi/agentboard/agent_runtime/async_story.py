"""通用工作项后台执行器（2026-08-26 根治 process_story 阻塞 main loop）。

背景：
    原 ``ProposalWorker.poll_once`` 同步串行调各 Handler.handle，而 ``handle``
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

log = logging.getLogger("agentboard.worker.work_async")

# 回调签名：on_decision(kind, work_item_id, outcome, exc_or_None)
OnDecision = Callable[[str, int, str, BaseException | None], None]


class AsyncWorkExecutor:
    """后台线程池执行 Handler.handle(work_item, invoker)，不阻塞 main loop。"""

    #: 支持的业务域（必须与 handlers.build_handlers 的 key 一致）
    KINDS: tuple[str, ...] = ("clarify", "ticket", "story")

    def __init__(self, invoker: Any, handlers: dict[str, Any],
                 max_concurrent: int = 1,
                 join_timeout: float = 30.0):
        self._invoker = invoker
        self._handlers = handlers
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
        """立即返回；后台线程执行对应 handler。in-flight 重复提交被拒绝。"""
        if self._closed:
            return "rejected_closed"
        if kind not in self.KINDS:
            return f"rejected_unknown_kind:{kind}"
        wid = work_item.get("id")
        key = (kind, wid)
        with self._lock:
            if key in self._inflight:
                return "duplicate_inflight"
            t = threading.Thread(
                target=self._run_one, args=(kind, work_item, on_decision),
                name=f"work-async-{kind}-{wid}", daemon=True,
            )
            self._inflight[key] = t
        t.start()
        return "submitted"

    def _run_one(self, kind: str, work_item: dict,
                 on_decision: OnDecision | None) -> None:
        wid = work_item.get("id")
        outcome = ""
        exc: BaseException | None = None
        try:
            with self._sems[kind]:
                handler = self._handlers.get(kind)
                if handler is None:
                    outcome = "no_handler"
                else:
                    outcome = handler.handle(work_item, self._invoker)
        except BaseException as e:  # noqa: BLE001 —— 后台必须吞掉避免线程死
            exc = e
            log.exception("%s #%s 后台执行异常", kind, wid)
        finally:
            with self._lock:
                self._inflight.pop((kind, wid), None)
                self._finished.append((kind, wid, outcome))
            if on_decision is not None:
                try:
                    on_decision(kind, wid, outcome, exc)
                except Exception:
                    log.exception("on_decision 回调异常（%s #%s）", kind, wid)

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

    def shutdown(self) -> None:
        """关闭入口：拒绝新任务，等待 in-flight 完成。"""
        with self._lock:
            self._closed = True
            threads = list(self._inflight.values())
        if not threads:
            return
        log.info("AsyncWorkExecutor 收尾：等 %d 个 in-flight 任务完成（≤%ss）",
                 len(threads), self._join_timeout)
        deadline = time.time() + self._join_timeout
        for t in threads:
            remaining = max(0.0, deadline - time.time())
            t.join(timeout=remaining)
            if t.is_alive():
                log.warning("后台线程 %s 在 join 超时后仍存活，强制退场"
                            "（子进程随主进程回收）", t.name)


# 向后兼容别名（2026-08-26 前仅有 Story 域时的类名）
_StoryAsyncExecutor = AsyncWorkExecutor
