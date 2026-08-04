"""
Epic 78 Story 102 — 模式 A：Launcher（CLI Agent 主动拉起）单元测试

覆盖验收标准：
1. ADAPTERS 注册 codex / claude 两个具体适配器，可取回并实例化。
2. Fake CLI（stdin 读 prompt 的 python 脚本）退出码 0 → SUCCESS + output 回写。
3. 退出码非 0 → FAILED + error_message 回写。
4. env 指向不存在路径 → FAILED（AdapterError 兜底，不裸崩）。
5. 超时（max_poll_seconds 小值）→ FAILED(timeout)。
6. build_prompt 四要素（title / spec / 项目记忆 / 验收标准）断言。
7. launch_run 全链路：DB pending run → running → success/failed，时间戳回写。
8. CLI --once / --run 冒烟。

自包含：临时 SQLite + Fake CLI 子进程，不依赖 18001 / 18000 / 28080 等外部服务。
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parent.parent


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Fake CLI 脚本（从 stdin 读 prompt，行为由环境变量控制）
# ---------------------------------------------------------------------------
FAKE_AGENT_PY = textwrap.dedent(
    """
    import os, sys, time

    data = sys.stdin.read()          # 读全部 prompt
    sleep = os.environ.get("FAKE_SLEEP", "")
    if sleep:
        time.sleep(float(sleep))
    print(f"FAKE_AGENT got prompt len={len(data)}")
    print("PROMPT_SNIPPET=" + data[:60].replace(chr(10), "\\\\n"))
    sys.stdout.flush()
    code = int(os.environ.get("FAKE_EXIT_CODE", "0"))
    sys.exit(code)
    """
)


def write_fake_agent(tmp_path: Path) -> Path:
    p = tmp_path / "fake_agent.py"
    p.write_text(FAKE_AGENT_PY, encoding="utf-8")
    return p


def fake_env(tmp_path: Path, **overrides) -> dict:
    env = dict(os.environ)
    env["AGENTBOARD_CODEX_BIN"] = (
        f"{sys.executable} {write_fake_agent(tmp_path)}"
    )
    env.pop("FAKE_SLEEP", None)
    env.pop("FAKE_EXIT_CODE", None)
    env.update(overrides)
    return env


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_registry():
    """隔离全局注册表（Story 101 fixture 同款），并恢复 env 快照。"""
    from agentboard.executor import ADAPTERS

    snapshot = dict(ADAPTERS)
    env_snapshot = {
        k: os.environ.get(k)
        for k in ("AGENTBOARD_CODEX_BIN", "AGENTBOARD_CLAUDE_BIN",
                  "AGENTBOARD_DEFAULT_AGENT")
    }
    yield
    ADAPTERS.clear()
    ADAPTERS.update(snapshot)
    for k, v in env_snapshot.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture()
def session_factory(tmp_path, monkeypatch):
    """独立临时 SQLite，patch 全局 engine（复用 test_scheduler 模式）。"""
    db_url = f"sqlite:///{tmp_path}/story102.db"
    os.environ["AGENTBOARD_DB_URL"] = db_url

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        db_url, connect_args={"check_same_thread": False}, future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi, rec):
        c = dbapi.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    import agentboard.database as db_mod

    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "SessionLocal",
                        sessionmaker(bind=engine, autoflush=False,
                                     autocommit=False, future=True))
    db_mod.init_db()

    @contextmanager
    def scoped():
        s = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                         future=True)()
        s.info["auto_commit"] = False
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return scoped


def _seed(session_factory, *, task_spec: str = "## 需求\n做一件事\n## 验收\n- 有结果\n",
          task_id: int | None = None, agent: str = "codex") -> int:
    """建 project + schedule + run，返回 run_id。"""
    from agentboard import service
    from agentboard.domains.common.enums import ScheduleType

    with session_factory() as s:
        proj = service.create_project(s, name="P102", key="P102K")
        sch = service.create_schedule(
            s, project_id=proj.id, title="story102 once",
            schedule_type=ScheduleType.ONCE, cron_expr=None,
        )
        tid = task_id
        if tid is None:
            tk = service.create_task(
                s, project_id=proj.id, story_id=None, title="T102",
                spec=task_spec, priority="highest",
            )
            tid = tk.id
        run = service.create_run(s, schedule_id=sch.id, task_id=tid,
                                 idempotency_key="k1")
        return run.id


# ---------------------------------------------------------------------------
# 1. 注册表
# ---------------------------------------------------------------------------
def test_codex_claude_registered():
    from agentboard.executor import (
        ADAPTERS, ClaudeLauncher, CodexLauncher, get_adapter,
    )

    assert "codex" in ADAPTERS
    assert "claude" in ADAPTERS
    assert get_adapter("codex") is CodexLauncher
    assert get_adapter("claude") is ClaudeLauncher
    # 可实例化
    codex = get_adapter("codex")()
    assert codex.name == "codex"
    assert isinstance(codex, CodexLauncher)


# ---------------------------------------------------------------------------
# 2. build_prompt 四要素
# ---------------------------------------------------------------------------
def test_build_prompt_four_elements():
    from agentboard.executor import AgentRunContext, CodexLauncher

    ctx = AgentRunContext(
        project_id=1, schedule_id=2, run_id=3, task_id=4,
        agent="codex", project_key="K", project_name="P",
        task_title="T", task_spec="## 需求\nxxx",
        memory="记忆内容", extra={"acceptance": "验收标准内容"},
    )
    prompt = CodexLauncher().build_prompt(None, None, ctx)
    assert "任务：T" in prompt
    assert "## 需求\nxxx" in prompt
    assert "记忆内容" in prompt
    assert "验收标准内容" in prompt
    assert "run #3" in prompt


def test_build_command_env_override(tmp_path):
    from agentboard.executor import AgentRunContext, CodexLauncher

    ctx = AgentRunContext(project_id=1, schedule_id=2, run_id=3)
    fake = write_fake_agent(tmp_path)
    os.environ["AGENTBOARD_CODEX_BIN"] = f"{sys.executable} {fake}"
    cmd = CodexLauncher().build_command(ctx)
    assert cmd[0] == sys.executable
    assert cmd[1] == str(fake)


def test_build_command_default():
    from agentboard.executor import AgentRunContext, CodexLauncher

    os.environ.pop("AGENTBOARD_CODEX_BIN", None)
    ctx = AgentRunContext(project_id=1, schedule_id=2, run_id=3)
    assert CodexLauncher().build_command(ctx) == ["codex", "exec", "--json"]


# ---------------------------------------------------------------------------
# 3. Fake CLI 退出码 → SUCCESS / FAILED
# ---------------------------------------------------------------------------
def test_launch_success(session_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {write_fake_agent(tmp_path)}")
    from agentboard import service
    from agentboard.executor import launch_run

    run_id = _seed(session_factory)
    result = launch_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None
    assert result["status"] == "success"
    assert "FAKE_AGENT got prompt len=" in (result.get("output") or "")
    assert result["started_at"] is not None
    assert result["finished_at"] is not None


def test_launch_failed_exit_code(session_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {write_fake_agent(tmp_path)}")
    monkeypatch.setenv("FAKE_EXIT_CODE", "3")
    from agentboard.executor import launch_run

    run_id = _seed(session_factory)
    result = launch_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None
    assert result["status"] == "failed"
    assert "process exited with code 3" in (result.get("error_message") or "")


def test_launch_command_not_found(session_factory, tmp_path, monkeypatch):
    # env 指向不存在的可执行文件
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN", "C:/no/such/codex.exe")
    from agentboard.executor import launch_run

    run_id = _seed(session_factory)
    result = launch_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None
    assert result["status"] == "failed"
    assert "command not found" in (result.get("error_message") or "")


def test_launch_timeout(session_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {write_fake_agent(tmp_path)}")
    monkeypatch.setenv("FAKE_SLEEP", "5")
    from agentboard.executor import launch_run

    run_id = _seed(session_factory)
    t0 = time.monotonic()
    result = launch_run(session_factory, run_id, poll_interval=0.05,
                        max_poll_seconds=0.8)
    elapsed = time.monotonic() - t0
    assert result is not None
    assert result["status"] == "failed"
    assert "timeout" in (result.get("error_message") or "").lower()
    assert elapsed < 4  # 未等满 5s 即超时返回


def test_launch_non_pending_skipped(session_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {write_fake_agent(tmp_path)}")
    from agentboard import service
    from agentboard.domains.common.enums import RunStatus
    from agentboard.executor import launch_run

    run_id = _seed(session_factory)
    with session_factory() as s:
        service.update_run(s, run_id, status=RunStatus.SUCCESS, output="old")
    result = launch_run(session_factory, run_id, poll_interval=0.05)
    assert result is None  # 非 pending 跳过


# ---------------------------------------------------------------------------
# 4. launch_run 全链路 + 记忆/验收加载
# ---------------------------------------------------------------------------
def test_launch_run_acceptance_and_memory(session_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {write_fake_agent(tmp_path)}")
    from agentboard import service
    from agentboard.executor import build_run_context, launch_run

    with session_factory() as s:
        proj = service.create_project(s, name="PM", key="PMK")
        sch = service.create_schedule(s, project_id=proj.id, title="sched",
                                      schedule_type="once")
        tk = service.create_task(
            s, project_id=proj.id, story_id=None, title="TT",
            spec="## 需求\nxxx\n## 验收\n- 标准1\n- 标准2", priority="high",
        )
        run = service.create_run(s, schedule_id=sch.id, task_id=tk.id,
                                 idempotency_key="k2")
        # 建一条 memory 文档
        from agentboard.domains.documents.models import Document
        from agentboard.domains.documents.models import DocumentType
        doc = Document(project_id=proj.id, title="mem", type=DocumentType.MEMORY,
                       content="项目约定：commit 前必须跑测试")
        s.add(doc)
        s.commit()
        run_id = run.id

    # 上下文组装：agent 默认 codex + memory 加载 + acceptance 提取
    with session_factory() as s:
        from agentboard.domains.scheduling.models import AgentRun
        run = s.get(AgentRun, run_id)
        ctx = build_run_context(s, run)
        assert ctx is not None
        assert "commit 前必须跑测试" in ctx.memory
        assert ctx.extra["acceptance"].startswith("## 验收")

    result = launch_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None and result["status"] == "success"


def test_launch_run_claude_adapter(session_factory, tmp_path, monkeypatch):
    """默认 agent 切换为 claude 时走 ClaudeLauncher。"""
    fake = write_fake_agent(tmp_path)
    monkeypatch.setenv("AGENTBOARD_CLAUDE_BIN", f"{sys.executable} {fake}")
    monkeypatch.setenv("AGENTBOARD_DEFAULT_AGENT", "claude")
    from agentboard.executor import launch_run

    run_id = _seed(session_factory)
    result = launch_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None and result["status"] == "success"


def test_launch_first_pending(session_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {write_fake_agent(tmp_path)}")
    from agentboard.executor import launch_first_pending

    run_id = _seed(session_factory)
    result = launch_first_pending(session_factory, poll_interval=0.05)
    assert result is not None and result["id"] == run_id
    assert result["status"] == "success"
    # 再跑一次：已无 pending
    assert launch_first_pending(session_factory, poll_interval=0.05) is None


# ---------------------------------------------------------------------------
# 5. CLI 冒烟（子进程调用 python -m agentboard.executor）
# ---------------------------------------------------------------------------
def test_cli_run_and_once(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {write_fake_agent(tmp_path)}")
    # 用 test_scheduler 同款临时 DB
    db_url = f"sqlite:///{tmp_path}/cli.db"
    os.environ["AGENTBOARD_DB_URL"] = db_url
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(db_url, connect_args={"check_same_thread": False},
                           future=True)
    import agentboard.database as db_mod
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "SessionLocal",
                        sessionmaker(bind=engine, autoflush=False,
                                     autocommit=False, future=True))
    db_mod.init_db()

    from agentboard import service

    with db_mod.session_scope() as s:
        proj = service.create_project(s, name="CLI", key="CLIK")
        sch = service.create_schedule(s, project_id=proj.id, title="cli once",
                                      schedule_type="once")
        tk = service.create_task(s, project_id=proj.id, story_id=None,
                                 title="TC", spec="## 需求\nx", priority="high")
        run = service.create_run(s, schedule_id=sch.id, task_id=tk.id,
                                 idempotency_key="kcli")
        rid = run.id

    # --run
    out = subprocess.run(
        [sys.executable, "-m", "agentboard.executor", "--run", str(rid)],
        capture_output=True, text=True, cwd=str(ROOT), env=dict(os.environ),
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    assert "status=success" in out.stdout

    # --once（此时无 pending）
    out2 = subprocess.run(
        [sys.executable, "-m", "agentboard.executor", "--once"],
        capture_output=True, text=True, cwd=str(ROOT), env=dict(os.environ),
        timeout=30,
    )
    assert out2.returncode == 0, out2.stderr
    assert "no pending run" in out2.stdout
