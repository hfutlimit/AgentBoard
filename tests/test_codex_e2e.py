"""Epic 122 (新增) — Codex Launcher E2E 冒烟

目标：把 Codex 适配器从「Story 102 fake CLI 单元层」推到「launch_run 全链路
端到端」,覆盖：

1. fake codex CLI 按 ``codex exec --json`` 真实协议输出（stderr 进度行 +
   stdout 决策 JSON），CliLauncher.stderr=STDOUT 合并后 output 同时含两类；
2. ``launch_run`` 走 CodexLauncher：DB pending run → running → 拉起 fake
   codex → SUCCESS + output 含决策 JSON；
3. FAKE_DECISION 切到 finalize → output 含 ``converged_spec``；
4. FAKE_DECISION=fail → 仍 SUCCESS（codex 退出码 0），但 output 含 fail
   action（按真实 codex 协议，决策是 fail 也不退非 0）；
5. FAKE_EXIT_CODE=3 → FAILED + ``process exited with code 3``；
6. AGENTBOARD_CODEX_BIN 指向不存在命令 → FAILED + ``command not found``；
7. 超时（FAKE_SLEEP=5 + max_poll_seconds=0.8）→ FAILED + ``timeout``。

约束：自包含临时 SQLite,不依赖 18001 / 18000 / 28080 外部服务,纯 pytest
+ subprocess。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
FAKE_CODEX = TESTS / "_fake_codex.py"

# ---- fixtures -------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _isolate_registry():
    """隔离 ADAPTERS 与 env,避免测试间相互污染（沿用 Story 102 同款）。"""
    from agentboard.executor import ADAPTERS

    snapshot = dict(ADAPTERS)
    env_snapshot = {
        k: os.environ.get(k)
        for k in ("AGENTBOARD_CODEX_BIN", "AGENTBOARD_DEFAULT_AGENT")
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
    """独立临时 SQLite + 全局 engine patch(同 Story 102 fixtures)。"""
    db_url = f"sqlite:///{tmp_path}/codex_e2e.db"
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
    monkeypatch.setattr(
        db_mod, "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True),
    )
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
          agent: str = "codex") -> int:
    from agentboard import service
    from agentboard.domains.common.enums import ScheduleType

    with session_factory() as s:
        proj = service.create_project(s, name="P-codex", key="PCX")
        sch = service.create_schedule(
            s, project_id=proj.id, title="codex e2e once",
            schedule_type=ScheduleType.ONCE, cron_expr=None,
        )
        tk = service.create_task(
            s, project_id=proj.id, story_id=None, title="T-codex",
            spec=task_spec, priority="high",
        )
        run = service.create_run(s, schedule_id=sch.id, task_id=tk.id,
                                 idempotency_key=f"k-codex-{time.time_ns()}")
        return run.id


# ---- 1. CodexLauncher 仍在 ADAPTERS ---------------------------------------


def test_codex_still_registered():
    from agentboard.executor import ADAPTERS, CodexLauncher, get_adapter

    assert "codex" in ADAPTERS
    assert get_adapter("codex") is CodexLauncher


# ---- 2. 全链路 success：fake codex 输出 ask 决策 -------------------------


def test_launch_run_codex_ask_decision(session_factory, monkeypatch):
    """fake codex 写 ask 决策到 stdout,stderr 写 progress chatter。
    CliLauncher 合并后,run.output 同时含 chatter 行与决策 JSON。"""
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {FAKE_CODEX}")
    monkeypatch.delenv("FAKE_DECISION", raising=False)
    from agentboard.executor import launch_run

    run_id = _seed(session_factory)
    result = launch_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None
    assert result["status"] == "success"
    out = result.get("output") or ""
    # 决策 JSON 必在 output（CliLauncher 当 raw 字符串透传）
    assert '"action": "ask"' in out or '"action":"ask"' in out
    assert "目标用户群是" in out  # 决策里的具体问题
    # 进度 chatter 走 stderr 被合并进来
    assert "scanning repository" in out
    assert "drafting questions" in out
    # 时间戳
    assert result["started_at"] is not None
    assert result["finished_at"] is not None


# ---- 3. FAKE_DECISION=finalize → output 含 converged_spec ---------------


def test_launch_run_codex_finalize(session_factory, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {FAKE_CODEX}")
    monkeypatch.setenv("FAKE_DECISION", "finalize")
    from agentboard.executor import launch_run

    run_id = _seed(session_factory)
    result = launch_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None
    assert result["status"] == "success"
    out = result.get("output") or ""
    assert '"action": "finalize"' in out or '"action":"finalize"' in out
    assert "做一件事" in out  # converged_spec 片段
    assert "验收" in out


# ---- 4. FAKE_DECISION=fail → 仍 SUCCESS（codex 决策层面 fail 不退非 0）


def test_launch_run_codex_fail_decision_still_success(session_factory, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {FAKE_CODEX}")
    monkeypatch.setenv("FAKE_DECISION", "fail")
    from agentboard.executor import launch_run

    run_id = _seed(session_factory)
    result = launch_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None
    assert result["status"] == "success"
    out = result.get("output") or ""
    assert '"action": "fail"' in out or '"action":"fail"' in out
    assert "fake codex 主动失败" in out


# ---- 5. FAKE_EXIT_CODE=3 → FAILED ---------------------------------------


def test_launch_run_codex_exit_nonzero(session_factory, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {FAKE_CODEX}")
    monkeypatch.setenv("FAKE_EXIT_CODE", "3")
    from agentboard.executor import launch_run

    run_id = _seed(session_factory)
    result = launch_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None
    assert result["status"] == "failed"
    err = (result.get("error_message") or "")
    assert "process exited with code 3" in err


# ---- 6. AGENTBOARD_CODEX_BIN 指向不存在命令 → FAILED + command not found


def test_launch_run_codex_command_not_found(session_factory, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       "C:/no/such/codex-actually-missing.exe")
    from agentboard.executor import launch_run

    run_id = _seed(session_factory)
    result = launch_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None
    assert result["status"] == "failed"
    err = (result.get("error_message") or "")
    assert "command not found" in err or "No such file" in err


# ---- 7. 超时 → FAILED(timeout) ------------------------------------------


def test_launch_run_codex_timeout(session_factory, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {FAKE_CODEX}")
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
    assert elapsed < 4  # 不会等满 5s


# ---- 8. CLI 冒烟:python -m agentboard.executor --run <id> ------------------


def test_cli_executor_run_codex(tmp_path, monkeypatch):
    """``python -m agentboard.executor --run <id>`` 走 CodexLauncher 全链路。"""
    monkeypatch.setenv("AGENTBOARD_CODEX_BIN",
                       f"{sys.executable} {FAKE_CODEX}")
    db_url = f"sqlite:///{tmp_path}/codex_cli.db"
    os.environ["AGENTBOARD_DB_URL"] = db_url

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(db_url, connect_args={"check_same_thread": False},
                           future=True)
    import agentboard.database as db_mod
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(
        db_mod, "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True),
    )
    db_mod.init_db()

    from agentboard import service
    with db_mod.session_scope() as s:
        proj = service.create_project(s, name="P-cli-codex", key="PCLICX")
        sch = service.create_schedule(
            s, project_id=proj.id, title="codex cli once",
            schedule_type="once",
        )
        tk = service.create_task(
            s, project_id=proj.id, story_id=None, title="T-cli", spec="## 需求\nx",
            priority="high",
        )
        run = service.create_run(s, schedule_id=sch.id, task_id=tk.id,
                                 idempotency_key="k-cli-codex")
        rid = run.id

    out = subprocess.run(
        [sys.executable, "-m", "agentboard.executor", "--run", str(rid)],
        capture_output=True, text=True, cwd=str(ROOT), env=dict(os.environ),
        timeout=30,
    )
    assert out.returncode == 0, (out.stdout, out.stderr)
    assert "status=success" in out.stdout
