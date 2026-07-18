"""
AgentSchedule 调度扫描器
=========================
后台定期扫描所有 enabled 的 AgentSchedule，查找到期任务，
通过 DB 行锁（SELECT FOR UPDATE NOWAIT）实现 lease 机制，
确保多个 scanner 实例不会重复触发同一 schedule。

幂等保证
--------
每次触发生成 `idempotency_key = f"schedule:{schedule_id}:{next_run_key}"`，
其中 `next_run_key` 由 schedule 的 next_run_at 时间戳构成。
如果 DB 中已存在相同 idempotency_key 的 AgentRun，直接跳过。

使用方式
--------
# 前台运行（调试/单实例）
python -m agentboard.scheduler

# 后台运行（生产，推荐 supervisor / systemd）
python -m agentboard.scheduler --daemon
"""

from __future__ import annotations

import logging
import argparse
import threading
import time
import uuid
from datetime import datetime, UTC

from croniter import croniter

from . import database as _db
from . import service
from .domains.common.enums import RunStatus, ScheduleType
from .domains.scheduling.models import AgentRun, AgentSchedule

log = logging.getLogger("agentboard.scheduler")

# 多实例 lease 超时（秒）：超过此时间未更新则视为 scanner 实例崩溃，其他实例可接管
LEASE_TTL_SECONDS = 30

# 扫描间隔（秒）
SCAN_INTERVAL_SECONDS = 10


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def compute_next_run(cron_expr: str, base_time: datetime | None = None) -> datetime | None:
    """
    根据 5 字段 cron 表达式（分 时 日 月 周）计算 base_time 之后的下次触发时间。

    croniter 默认用 6 字段（多一个秒字段），本项目用标准 5 字段，
    传入 base_time 时需要去掉秒字段保持一致。
    """
    if base_time is None:
        base_time = _now()
    # croniter 默认第二字段是月内日，第三字段是月；本项目是 5 字段：
    #   0 * * * *   →  分 时 日 月 周
    # 因此传入 base_time 的秒要抹零，以免边界差 1 秒
    anchor = base_time.replace(second=0, microsecond=0)
    try:
        crit = croniter(cron_expr, anchor)
        return crit.get_next(datetime)
    except (ValueError, KeyError):
        log.warning("invalid cron expression: %s", cron_expr)
        return None


def compute_next_run_for_once(scheduled_at: datetime) -> datetime | None:
    """
    一次性（once）调度：若 scheduled_at 未过期则返回其本身，否则返回 None。
    scheduled_at 存储在 AgentSchedule.next_run_at 字段中。
    """
    now = _now()
    if scheduled_at >= now:
        return scheduled_at
    return None  # 已过期，不再触发


def _idempotency_key(schedule: AgentSchedule, trigger_time: datetime) -> str:
    """生成幂等键：schedule:{schedule_id}:{run_key}"""
    run_key = trigger_time.strftime("%Y%m%d%H%M%S")
    return f"schedule:{schedule.id}:{run_key}"


def scan_and_trigger(session_factory, poll_interval: int = SCAN_INTERVAL_SECONDS) -> None:
    """
    扫描到期 schedule 并触发 AgentRun。

    Args:
        session_factory: 一个返回 Session 的可调用对象（不带参数），
                         即 `db.SessionLocal` 或 `db.session_scope`（返回 context manager）。
                         函数内部通过 `with session_factory() as s:` 使用。
    """
    """
    单次扫描：查找所有 enabled、next_run_at <= now 的 AgentSchedule，
    尝试加行锁后创建 AgentRun。

    多实例安全通过 SELECT FOR UPDATE NOWAIT 实现：
    - 若获取锁成功 → 负责触发，并更新 next_run_at
    - 若获取锁失败（被其他实例持有）→ 跳过

    该函数在 DaemonScheduler.run() 的主循环中反复调用。
    """
    from sqlalchemy import text

    with session_factory() as s:
        now = _now()
        # 找所有到期且 enabled 的 schedule
        due = (
            s.query(AgentSchedule)
            .filter(
                AgentSchedule.enabled == True,
                AgentSchedule.next_run_at != None,
                AgentSchedule.next_run_at <= now,
            )
            .all()
        )

        for schedule in due:
            _trigger_one(s, schedule, now)


def _trigger_one(s, schedule: AgentSchedule, now: datetime) -> bool:
    """
    触发单个 schedule：加锁 → 幂等检查 → 创建 AgentRun → 更新 next_run_at。
    返回 True 表示成功触发，False 表示跳过（锁被占用或已幂等）。

    行锁策略（多实例安全）：
    - MariaDB/MySQL：使用 FOR UPDATE NOWAIT，立即失败而非阻塞
    - SQLite：不支持行级锁，依赖幂等键保证（适合单实例或 SQLite 调试）
    """
    from sqlalchemy import text

    dialect = s.bind.dialect.name
    if dialect in ("mysql", "mariadb"):
        try:
            locked = s.execute(
                text("SELECT 1 FROM agent_schedules WHERE id = :sid FOR UPDATE NOWAIT"),
                {"sid": schedule.id},
            ).fetchone()
        except Exception:
            log.debug("schedule %d locked by another instance, skip", schedule.id)
            return False
        if locked is None:
            return False
    # SQLite / 其他：跳过行锁，依赖幂等键

    # 重新读取最新状态（加锁后数据可能已变；无锁时保持对象最新）
    s.refresh(schedule)

    # 二次检查：enabled + next_run_at 未被其他实例更新
    if not schedule.enabled or schedule.next_run_at is None:
        log.debug("schedule %d no longer eligible, skip", schedule.id)
        return False
    if schedule.next_run_at > now:
        log.debug("schedule %d next_run_at updated by another instance, skip", schedule.id)
        return False

    # 计算幂等键
    trigger_time = schedule.next_run_at
    idempotency_key = _idempotency_key(schedule, trigger_time)

    # 幂等检查：看是否已有相同 key 的 run
    existing = s.query(AgentRun).filter(AgentRun.idempotency_key == idempotency_key).first()
    if existing:
        log.info("schedule %d already triggered (idempotent skip), skip", schedule.id)
        _advance_next_run(s, schedule)
        return False

    # 创建 AgentRun（pending 状态）
    try:
        run = service.create_run(
            s,
            schedule_id=schedule.id,
            task_id=None,
            idempotency_key=idempotency_key,
        )
        run = service.update_run(s, run.id, status=RunStatus.PENDING)
        log.info(
            "triggered schedule %d '%s' → run %d (idempotency_key=%s)",
            schedule.id, schedule.title, run.id, idempotency_key,
        )
    except service.Duplicate:
        log.info("schedule %d run already created (race), skip", schedule.id)
        _advance_next_run(s, schedule)
        return False

    # 推进 next_run_at（仅对 CRON schedule 有意义）
    _advance_next_run(s, schedule)

    return True


def _advance_next_run(s, schedule: AgentSchedule) -> None:
    """
    根据 schedule_type 推进 next_run_at：
    - once: 设为 None（只执行一次）
    - cron : 用 croniter 计算下次触发时间

    注意：schedule_type 在 DB 中存的是字符串（如 "once"/"cron"），
    所以直接用字符串比较，避免与 StrEnum 实例不匹配的问题。
    """
    if schedule.schedule_type == "once":
        schedule.next_run_at = None
    elif schedule.schedule_type == "cron" and schedule.cron_expr:
        next_time = compute_next_run(schedule.cron_expr)
        schedule.next_run_at = next_time
        if next_time:
            log.debug("schedule %d next_run_at advanced to %s", schedule.id, next_time)

    schedule.last_run_at = _now()
    s.commit()


class DaemonScheduler:
    """
    后台调度守护进程。在独立线程中定期调用 scan_and_trigger()。

    使用方法::

        scheduler = DaemonScheduler(db_url="sqlite:///./agentboard.db")
        scheduler.start()
        # ...
        scheduler.stop()
    """

    def __init__(
        self,
        db_url: str | None = None,
        poll_interval: int = SCAN_INTERVAL_SECONDS,
    ):
        self._db_url = db_url
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            log.warning("scheduler already started")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="AgentScheduler")
        self._thread.start()
        log.info("scheduler daemon started (poll_interval=%ds)", self._poll_interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        log.info("scheduler daemon stopped")

    def _run_loop(self) -> None:
        # 为后台线程配置独立的 session factory
        if self._db_url:
            _db._engine_url = self._db_url  # 覆盖全局 URL（危险但可接受于独立 daemon）
        import os as _os
        if self._db_url:
            _os.environ.setdefault("AGENTBOARD_DB_URL", self._db_url)

        while not self._stop_event.is_set():
            try:
                scan_and_trigger(_db.session_scope)
            except Exception:
                log.exception("error during scan_and_trigger")

            # 分段等待，允许快速响应 stop 信号
            for _ in range(self._poll_interval):
                if self._stop_event.wait(timeout=1):
                    break


# ---------- CLI 入口 ----------
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="AgentBoard Schedule Scanner")
    parser.add_argument("--daemon", action="store_true", help="run as background daemon")
    parser.add_argument("--once", action="store_true", help="run scan once and exit")
    parser.add_argument("--poll-interval", type=int, default=SCAN_INTERVAL_SECONDS,
                        help=f"poll interval in seconds (default {SCAN_INTERVAL_SECONDS})")
    args = parser.parse_args()

    if args.daemon:
        sched = DaemonScheduler(poll_interval=args.poll_interval)
        sched.start()
        # 保持主线程存活
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            sched.stop()
    elif args.once:
        scan_and_trigger(_db.session_scope)
    else:
        # 前台单次扫描（调试模式）
        print("running single scan (Ctrl+C to exit)...")
        sched = DaemonScheduler(poll_interval=args.poll_interval)
        sched.start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            sched.stop()


if __name__ == "__main__":
    main()
