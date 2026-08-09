"""
test_schedule_unbind.py
=======================
Epic 78 Story 106「AgentSchedule 绑定松绑（项目/Agent 级 + 筛选）」。

覆盖：
- create_schedule 新字段（agent / task_id / task_priority / task_type / epic_id）与校验
- update_schedule 显式置空（解除绑定 / 清除筛选）
- pick_eligible_task：固定 task / 项目级筛选 / 优先级门槛 / 排序 / 空结果
- scheduler._trigger_one：项目级自动选 task / 固定 task / 无 eligible 跳过
- executor.build_run_context：agent 读 schedule.agent（fallback env）

测试策略：与 test_scheduler.py 一致，直接调用内部函数，独立临时 DB。
"""

import os
import sys
import pytest
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def utcnow():
    """naive UTC datetime，与 models._now() / scheduler._now() 一致。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path}/test_schedule_unbind.db"
    os.environ["AGENTBOARD_DB_URL"] = db_url

    from sqlalchemy import event, create_engine
    from sqlalchemy.orm import sessionmaker

    new_engine = create_engine(db_url, connect_args={"check_same_thread": False}, future=True)

    @event.listens_for(new_engine, "connect")
    def _fk(dbapi, rec):
        c = dbapi.cursor(); c.execute("PRAGMA foreign_keys=ON"); c.close()

    import agentboard.database as db_mod
    monkeypatch.setattr(db_mod, "engine", new_engine)
    monkeypatch.setattr(db_mod, "SessionLocal",
                       sessionmaker(bind=new_engine, autoflush=False, autocommit=False, future=True))

    from agentboard import scheduler as sched_mod
    monkeypatch.setattr(sched_mod._db, "engine", new_engine)
    monkeypatch.setattr(sched_mod._db, "SessionLocal",
                       sessionmaker(bind=new_engine, autoflush=False, autocommit=False, future=True))

    db_mod.init_db()

    @contextmanager
    def scoped():
        s = sessionmaker(bind=new_engine, autoflush=False, autocommit=False, future=True,
                         expire_on_commit=False)()
        s.info["auto_commit"] = False
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    yield scoped


def _mk_project_and_tasks(s, *, with_epic=False):
    """建项目 + epic + story，返回 (project, epic, story_id)。"""
    from agentboard import service
    from sqlalchemy import or_

    p = service.create_project(s, name="Unbind Proj")
    s.commit()
    epic = service.create_epic(s, project_id=p.id, title="Epic A")
    s.commit()
    story = service.create_story(s, epic_id=epic.id, title="Story A")
    s.commit()
    # 2026-08-09：Epic/Story 自动带「设计：/实现：」模板 task（backlog）——
    # 调度选择前全部置 done，保持「只挑手动任务」的既有测试语义
    from agentboard.models import Task
    from agentboard.domains.common.enums import Status as _St
    for t in s.query(Task).filter(
        Task.project_id == p.id,
        or_(Task.title.like("设计：%"), Task.title.like("实现：%")),
    ).all():
        t.status = _St.DONE
    s.commit()
    return p, epic, story.id


def _mk_task(s, *, project_id, story_id=None, title="T", status="backlog",
             priority="medium", type="task"):
    from agentboard import service

    t = service.create_task(
        s, project_id=project_id, story_id=story_id, title=title,
        priority=priority, type=type,
    )
    t.status = status  # 测试数据准备：直接赋值（绕过状态机）
    s.commit()
    return t


# ---------- create_schedule 新字段 ----------

def test_create_schedule_with_unbind_fields(test_db):
    from agentboard import service

    with test_db() as s:
        p = service.create_project(s, name="P")
        s.commit()
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron", cron_expr="*/5 * * * *",
            agent="workbuddy", task_priority="high", task_type="bug",
        )
        s.commit()
        assert sch.agent == "workbuddy"
        assert sch.task_priority == "high"
        assert sch.task_type == "bug"
        assert sch.task_id is None
        assert sch.epic_id is None


def test_create_schedule_invalid_agent_rejected(test_db):
    from agentboard import service

    with test_db() as s:
        p = service.create_project(s, name="P")
        s.commit()
        with pytest.raises(service.InvalidValue):
            service.create_schedule(
                s, project_id=p.id, title="S", schedule_type="cron",
                cron_expr="*/5 * * * *", agent="not-an-agent",
            )


def test_create_schedule_invalid_task_priority_rejected(test_db):
    from agentboard import service

    with test_db() as s:
        p = service.create_project(s, name="P")
        s.commit()
        with pytest.raises(service.InvalidValue):
            service.create_schedule(
                s, project_id=p.id, title="S", schedule_type="cron",
                cron_expr="*/5 * * * *", task_priority="urgent",
            )


def test_create_schedule_missing_task_raises_not_found(test_db):
    from agentboard import service

    with test_db() as s:
        p = service.create_project(s, name="P")
        s.commit()
        with pytest.raises(service.NotFound):
            service.create_schedule(
                s, project_id=p.id, title="S", schedule_type="cron",
                cron_expr="*/5 * * * *", task_id=99999,
            )


def test_create_schedule_with_fixed_task_id(test_db):
    from agentboard import service

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        t = _mk_task(s, project_id=p.id, story_id=story_id, title="Fixed")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron",
            cron_expr="*/5 * * * *", task_id=t.id,
        )
        s.commit()
        assert sch.task_id == t.id


# ---------- update_schedule 显式置空 ----------

def test_update_schedule_clear_binding_fields(test_db):
    from agentboard import service

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        t = _mk_task(s, project_id=p.id, story_id=story_id, title="T")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron",
            cron_expr="*/5 * * * *", agent="workbuddy", task_id=t.id,
            task_priority="high",
        )
        s.commit()
        sch_id = sch.id
        # 显式置空：agent → None、task_id → None、task_priority → None
        updated = service.update_schedule(
            s, sch_id, agent=None, task_id=None, task_priority=None,
        )
        assert updated.agent is None
        assert updated.task_id is None
        assert updated.task_priority is None
        # 未传字段保持
        assert updated.title == "S"
        assert updated.schedule_type == "cron"


# ---------- pick_eligible_task ----------

def test_pick_eligible_task_fixed_task_id(test_db):
    from agentboard import service
    from agentboard.models import AgentSchedule

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        t = _mk_task(s, project_id=p.id, story_id=story_id, title="Fixed", status="in_progress")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron",
            cron_expr="*/5 * * * *", task_id=t.id,
        )
        s.commit()
        # 固定 task：即使 in_progress 也返回（兼容旧单任务语义）
        picked = service.pick_eligible_task(s, sch)
        assert picked is not None and picked.id == t.id


def test_pick_eligible_task_skips_non_startable(test_db):
    """项目级：只挑 backlog/todo；in_progress / done 跳过。"""
    from agentboard import service

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        _mk_task(s, project_id=p.id, story_id=story_id, title="InProg", status="in_progress")
        _mk_task(s, project_id=p.id, story_id=story_id, title="Done", status="done")
        eligible = _mk_task(s, project_id=p.id, story_id=story_id, title="Todo", status="todo")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron", cron_expr="*/5 * * * *",
        )
        s.commit()
        picked = service.pick_eligible_task(s, sch)
        assert picked is not None and picked.id == eligible.id


def test_pick_eligible_task_priority_threshold(test_db):
    """task_priority=high 门槛：highest/high 可选，medium/low 排除。"""
    from agentboard import service

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        _mk_task(s, project_id=p.id, story_id=story_id, title="Low", priority="low")
        _mk_task(s, project_id=p.id, story_id=story_id, title="Medium", priority="medium")
        high = _mk_task(s, project_id=p.id, story_id=story_id, title="High", priority="high")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron",
            cron_expr="*/5 * * * *", task_priority="high",
        )
        s.commit()
        picked = service.pick_eligible_task(s, sch)
        assert picked is not None and picked.id == high.id


def test_pick_eligible_task_priority_order(test_db):
    """优先级降序：highest 优先于 high。"""
    from agentboard import service

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        _mk_task(s, project_id=p.id, story_id=story_id, title="High", priority="high")
        highest = _mk_task(s, project_id=p.id, story_id=story_id, title="Highest", priority="highest")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron", cron_expr="*/5 * * * *",
        )
        s.commit()
        picked = service.pick_eligible_task(s, sch)
        assert picked is not None and picked.id == highest.id


def test_pick_eligible_task_type_filter(test_db):
    from agentboard import service

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        _mk_task(s, project_id=p.id, story_id=story_id, title="Bug", type="bug")
        task = _mk_task(s, project_id=p.id, story_id=story_id, title="Task", type="task")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron",
            cron_expr="*/5 * * * *", task_type="task",
        )
        s.commit()
        picked = service.pick_eligible_task(s, sch)
        assert picked is not None and picked.id == task.id


def test_pick_eligible_task_epic_filter(test_db):
    from agentboard import service

    with test_db() as s:
        p, epic, story_id = _mk_project_and_tasks(s, with_epic=True)
        inside = _mk_task(s, project_id=p.id, story_id=story_id, title="Inside")
        # 另一个 free story（无 epic）的任务不应被选中
        _mk_task(s, project_id=p.id, story_id=None, title="Outside")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron",
            cron_expr="*/5 * * * *", epic_id=epic.id,
        )
        s.commit()
        picked = service.pick_eligible_task(s, sch)
        assert picked is not None and picked.id == inside.id


def test_pick_eligible_task_no_match_returns_none(test_db):
    from agentboard import service

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        _mk_task(s, project_id=p.id, story_id=story_id, title="Done", status="done")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron", cron_expr="*/5 * * * *",
        )
        s.commit()
        assert service.pick_eligible_task(s, sch) is None


# ---------- scheduler._trigger_one 绑定 ----------

def _due_schedule(s, sch, minutes_back=2):
    sch.next_run_at = utcnow() - timedelta(minutes=minutes_back)
    s.commit()
    return sch


def test_trigger_project_level_binds_eligible_task(test_db):
    """项目级 schedule（无 task_id）：触发后 run 绑定自动挑选的 task。"""
    from agentboard import service
    from agentboard.scheduler import _trigger_one, _now

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        t = _mk_task(s, project_id=p.id, story_id=story_id, title="PickMe", priority="highest")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron", cron_expr="*/5 * * * *",
        )
        _due_schedule(s, sch)
        sch_id = sch.id
        triggered = _trigger_one(s, sch, _now())
        assert triggered is True
        s.commit()

    with test_db() as s:
        runs = service.list_runs(s, sch_id)
        assert len(runs) == 1
        assert runs[0].task_id == t.id  # 自动绑定


def test_trigger_fixed_task_binds_fixed(test_db):
    """固定 task_id 的 schedule：run 绑定固定 task。"""
    from agentboard import service
    from agentboard.scheduler import _trigger_one, _now

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        t = _mk_task(s, project_id=p.id, story_id=story_id, title="Fixed")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron",
            cron_expr="*/5 * * * *", task_id=t.id,
        )
        _due_schedule(s, sch)
        sch_id = sch.id
        triggered = _trigger_one(s, sch, _now())
        assert triggered is True
        s.commit()

    with test_db() as s:
        runs = service.list_runs(s, sch_id)
        assert len(runs) == 1
        assert runs[0].task_id == t.id


def test_trigger_no_eligible_skips_without_run(test_db):
    """无 eligible task：跳过本次（返回 False，不创建 run），next_run_at 已推进。"""
    from agentboard import service
    from agentboard.scheduler import _trigger_one, _now

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        _mk_task(s, project_id=p.id, story_id=story_id, title="Done", status="done")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron", cron_expr="*/5 * * * *",
        )
        _due_schedule(s, sch)
        sch_id = sch.id
        old_next = sch.next_run_at
        triggered = _trigger_one(s, sch, _now())
        assert triggered is False
        s.commit()

    with test_db() as s:
        runs = service.list_runs(s, sch_id)
        assert len(runs) == 0  # 未创建空 run
        fresh = service.get_schedule(s, sch_id)
        assert fresh.next_run_at is not None and fresh.next_run_at > old_next


# ---------- executor.build_run_context agent 读取 ----------

def test_build_run_context_agent_from_schedule(test_db, monkeypatch):
    """agent 读 schedule.agent（优先于 env）。"""
    from agentboard import service
    from agentboard.executor import build_run_context
    from agentboard.scheduler import _trigger_one, _now

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        _mk_task(s, project_id=p.id, story_id=story_id, title="T1", priority="highest")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron",
            cron_expr="*/5 * * * *", agent="workbuddy",
        )
        _due_schedule(s, sch)
        _trigger_one(s, sch, _now())
        s.commit()
        run = service.list_runs(s, sch.id)[0]
        run_id = run.id

    monkeypatch.delenv("AGENTBOARD_DEFAULT_AGENT", raising=False)
    with test_db() as s:
        ctx = build_run_context(s, s.get(service.AgentRun, run_id))
        assert ctx is not None
        assert ctx.agent == "workbuddy"  # 来自 schedule.agent


def test_build_run_context_agent_fallback_env(test_db, monkeypatch):
    """schedule.agent 为空时 fallback env AGENTBOARD_DEFAULT_AGENT。"""
    from agentboard import service
    from agentboard.executor import build_run_context
    from agentboard.scheduler import _trigger_one, _now

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        _mk_task(s, project_id=p.id, story_id=story_id, title="T1", priority="highest")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron",
            cron_expr="*/5 * * * *",  # 不设 agent
        )
        _due_schedule(s, sch)
        _trigger_one(s, sch, _now())
        s.commit()
        run = service.list_runs(s, sch.id)[0]
        run_id = run.id

    monkeypatch.setenv("AGENTBOARD_DEFAULT_AGENT", "codex")
    with test_db() as s:
        ctx = build_run_context(s, s.get(service.AgentRun, run_id))
        assert ctx is not None
        assert ctx.agent == "codex"  # env fallback


def test_build_run_context_task_bound(test_db):
    """项目级 schedule 触发的 run 绑定了自动挑选的 task，ctx.task_id 一致。"""
    from agentboard import service
    from agentboard.executor import build_run_context
    from agentboard.scheduler import _trigger_one, _now

    with test_db() as s:
        p, _, story_id = _mk_project_and_tasks(s)
        t = _mk_task(s, project_id=p.id, story_id=story_id, title="T1", priority="highest")
        sch = service.create_schedule(
            s, project_id=p.id, title="S", schedule_type="cron", cron_expr="*/5 * * * *",
        )
        _due_schedule(s, sch)
        _trigger_one(s, sch, _now())
        s.commit()
        run = service.list_runs(s, sch.id)[0]
        run_id = run.id
        assert run.task_id == t.id

    with test_db() as s:
        ctx = build_run_context(s, s.get(service.AgentRun, run_id))
        assert ctx is not None
        assert ctx.task_id == t.id
        assert ctx.task_title == "T1"
