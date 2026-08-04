"""Epic 78 Story 104 — AgentRun 状态机驱动 + report_run_result 单元测试。

覆盖验收标准：
1. service.report_run_result：pending/running → success/failed/cancelled 合法；
   终态不可再迁移（409）；终态重复报告同状态幂等（200，补齐 summary/log_ref）。
2. summary/log_ref 落库；update_run 兼容写入 summary/log_ref。
3. REST POST /api/runs/{rid}/report：200 / 404 / 422 / 409 语义正确。
4. executor.execute_run 状态机主循环：
   - fake Launcher 立即 success → summary/log_ref/finished_at 落库；
   - fake Launcher 抛异常 → failed；
   - Agent 经 report_run_result 外部回写 → execute_run 感知 DB 终态并 finalize；
   - 超时兜底 → failed(timeout)。
5. MCP report_run_result 工具注册 + 走 REST 端点（真实 uvicorn 子进程）。

自包含：临时 SQLite + 真实 uvicorn 子进程 + 直接调 service/executor 函数，
不依赖 18001 / 18000 / 28080 等外部服务。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 独立临时数据库（与其它测试隔离）
_DB = tempfile.mktemp(suffix="_story104.db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def session_factory():
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from agentboard.database import init_db

    engine = create_engine(
        os.environ["AGENTBOARD_DB_URL"],
        connect_args={"check_same_thread": False}, future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi, rec):
        c = dbapi.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    import agentboard.database as db_mod

    db_mod.engine = engine
    db_mod.SessionLocal = sessionmaker(bind=engine, autoflush=False,
                                       autocommit=False, future=True)
    init_db()

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


def _seed_run(session_factory, *, agent: str = "codex") -> int:
    """建 project + schedule + run，返回 run_id（key 唯一，避免跨测试冲突）。"""
    import uuid

    from agentboard import service
    from agentboard.domains.common.enums import ScheduleType

    tag = uuid.uuid4().hex[:8]
    with session_factory() as s:
        proj = service.create_project(s, name=f"P104-{tag}", key=f"P104K{tag[:5].upper()}")
        sch = service.create_schedule(
            s, project_id=proj.id, title="story104 once",
            schedule_type=ScheduleType.ONCE, cron_expr=None,
        )
        tk = service.create_task(
            s, project_id=proj.id, story_id=None, title="T104",
            spec="## 需求\n做一件事\n## 验收\n- 有结果", priority="highest",
        )
        run = service.create_run(s, schedule_id=sch.id, task_id=tk.id,
                                 idempotency_key=f"k104-{tag}")
        return run.id


# ---------------------------------------------------------------------------
# 1. service.report_run_result 状态机
# ---------------------------------------------------------------------------
def test_report_result_pending_to_success(session_factory):
    from agentboard import service
    from agentboard.domains.common.enums import RunStatus

    rid = _seed_run(session_factory)
    with session_factory() as s:
        run = service.report_run_result(
            s, rid, status=RunStatus.SUCCESS,
            summary="**完成**：交付了 X", log_ref="cos://logs/run-1",
        )
        assert run.status == RunStatus.SUCCESS
        assert run.summary == "**完成**：交付了 X"
        assert run.log_ref == "cos://logs/run-1"
        assert run.finished_at is not None
        assert run.started_at is None  # 未经过 running 也可直接报终态


def test_report_result_running_to_failed(session_factory):
    from agentboard import service
    from agentboard.domains.common.enums import RunStatus

    rid = _seed_run(session_factory)
    with session_factory() as s:
        service.update_run(s, rid, status=RunStatus.RUNNING,
                           started_at=utcnow())
        run = service.report_run_result(
            s, rid, status=RunStatus.FAILED, summary="agent 崩溃",
        )
        assert run.status == RunStatus.FAILED
        assert run.summary == "agent 崩溃"
        assert run.finished_at is not None


def test_report_result_terminal_immutable(session_factory):
    from agentboard import service
    from agentboard.domains.common.enums import RunStatus

    rid = _seed_run(session_factory)
    with session_factory() as s:
        service.report_run_result(s, rid, status=RunStatus.SUCCESS, summary="done")
        # 终态 → 其他终态非法
        with pytest.raises(service.IllegalTransition):
            service.report_run_result(s, rid, status=RunStatus.FAILED, summary="x")
        # 终态 → running 非法
        with pytest.raises(service.IllegalTransition):
            service.report_run_result(s, rid, status=RunStatus.RUNNING)
        # 幂等：重复报 success 不抛错，且不覆盖已有 summary
        run = service.report_run_result(s, rid, status=RunStatus.SUCCESS,
                                        summary="第二次报告")
        assert run.status == RunStatus.SUCCESS
        assert run.summary == "done"  # 已有 summary 不被覆盖


def test_report_result_not_found_and_invalid(session_factory):
    from agentboard import service

    with session_factory() as s:
        with pytest.raises(service.NotFound):
            service.report_run_result(s, 99999, status="success")
        with pytest.raises(service.InvalidValue):
            service.report_run_result(s, 1, status="not-a-status")


# ---------------------------------------------------------------------------
# 2. REST POST /api/runs/{rid}/report（真实 uvicorn）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def api_server(session_factory):
    port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(base + "/api/meta", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("API 服务启动超时")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_rest_report_endpoint(session_factory, api_server):
    from agentboard import service
    from agentboard.domains.common.enums import RunStatus

    rid = _seed_run(session_factory, agent="rest")
    base = api_server
    # 成功报告
    r = httpx.post(f"{base}/api/runs/{rid}/report",
                   json={"status": "success", "summary": "REST 完成",
                         "log_ref": "s3://bucket/1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["summary"] == "REST 完成"
    assert body["log_ref"] == "s3://bucket/1"
    assert body["finished_at"] is not None
    # 幂等重复
    r2 = httpx.post(f"{base}/api/runs/{rid}/report",
                    json={"status": "success", "summary": "again"})
    assert r2.status_code == 200
    assert r2.json()["summary"] == "REST 完成"  # 不覆盖
    # 终态 → 其他终态 409
    r3 = httpx.post(f"{base}/api/runs/{rid}/report",
                    json={"status": "failed", "summary": "x"})
    assert r3.status_code == 409, r3.text
    # 非法 status 422
    r4 = httpx.post(f"{base}/api/runs/{rid}/report",
                    json={"status": "hacked"})
    assert r4.status_code == 422
    # 404
    r5 = httpx.post(f"{base}/api/runs/99999/report",
                    json={"status": "success"})
    assert r5.status_code == 404


# ---------------------------------------------------------------------------
# 3. executor.execute_run 状态机主循环
# ---------------------------------------------------------------------------
class _FakeLauncher:
    """story104 测试用 fake LauncherAdapter（不注册进全局表，直接 monkeypatch）。"""

    def __init__(self, *, fail: bool = False, raise_on_launch: bool = False):
        self.fail = fail
        self.raise_on_launch = raise_on_launch
        self.timeout_seconds = 30.0

    def launch(self, run, task, ctx):
        from agentboard.executor import RunHandle
        from agentboard.domains.common.enums import RunStatus

        if self.raise_on_launch:
            raise RuntimeError("launch boom")
        h = RunHandle(run_id=run.id, adapter="fake")
        h.mark_running()
        if self.fail:
            h.fail("agent failed badly")
        else:
            h.complete("fake done output")
        return h

    def poll_status(self, handle):
        return handle.status


def test_execute_run_success_path(session_factory, monkeypatch):
    import agentboard.executor as ex

    rid = _seed_run(session_factory, agent="fake-ok")
    monkeypatch.setattr(ex, "resolve_adapter", lambda name: _FakeLauncher())
    result = ex.execute_run(session_factory, rid, poll_interval=0.05,
                            max_poll_seconds=10)
    assert result is not None
    assert result["status"] == "success"
    assert result["summary"] == "fake done output"
    assert result["output"] == "fake done output"
    assert result["finished_at"] is not None
    assert result["started_at"] is not None


def test_execute_run_failed_path(session_factory, monkeypatch):
    import agentboard.executor as ex

    rid = _seed_run(session_factory, agent="fake-fail")
    monkeypatch.setattr(ex, "resolve_adapter", lambda name: _FakeLauncher(fail=True))
    result = ex.execute_run(session_factory, rid, poll_interval=0.05,
                            max_poll_seconds=10)
    assert result["status"] == "failed"
    assert result["error_message"] == "agent failed badly"


def test_execute_run_launch_exception(session_factory, monkeypatch):
    import agentboard.executor as ex

    rid = _seed_run(session_factory, agent="fake-boom")
    monkeypatch.setattr(ex, "resolve_adapter",
                        lambda name: _FakeLauncher(raise_on_launch=True))
    result = ex.execute_run(session_factory, rid, poll_interval=0.05,
                            max_poll_seconds=10)
    assert result["status"] == "failed"
    assert "launch failed" in result["error_message"]


def test_execute_run_external_report_wins(session_factory, monkeypatch):
    """Agent 经 report_run_result 回写终态 → 执行器轮询感知并以外部为准 finalize。"""
    import agentboard.executor as ex
    from agentboard import service

    class _SlowLauncher:
        timeout_seconds = 60.0

        def launch(self, run, task, ctx):
            from agentboard.executor import RunHandle
            from agentboard.domains.common.enums import RunStatus

            h = RunHandle(run_id=run.id, adapter="slow")
            h.mark_running()
            return h  # 不立即完成，等外部回写

        def poll_status(self, handle):
            from agentboard.domains.common.enums import RunStatus
            return handle.status  # 恒 running

    rid = _seed_run(session_factory, agent="slow")
    monkeypatch.setattr(ex, "resolve_adapter", lambda name: _SlowLauncher())

    # 外部线程/进程回写 success（模拟 Agent 经 MCP report_run_result）
    def _external_report():
        time.sleep(0.3)
        from agentboard import database as _db
        from agentboard import service as _svc
        from agentboard.domains.common.enums import RunStatus as _RS
        with _db.session_scope() as s:
            _svc.report_run_result(s, rid, status=_RS.SUCCESS,
                                   summary="外部回写成功", log_ref="ext://log")

    import threading
    t = threading.Thread(target=_external_report, daemon=True)
    t.start()
    result = ex.execute_run(session_factory, rid, poll_interval=0.05,
                            max_poll_seconds=15)
    assert result is not None
    assert result["status"] == "success"
    assert result["summary"] == "外部回写成功"
    assert result["log_ref"] == "ext://log"


def test_execute_run_timeout_fallback(session_factory, monkeypatch):
    """超时兜底：Agent 不回写也不退出 → failed(timeout)。"""
    import agentboard.executor as ex

    class _HangingLauncher:
        timeout_seconds = 60.0

        def launch(self, run, task, ctx):
            from agentboard.executor import RunHandle

            h = RunHandle(run_id=run.id, adapter="hang")
            h.mark_running()
            return h

        def poll_status(self, handle):
            from agentboard.domains.common.enums import RunStatus
            return handle.status  # 恒 running

    rid = _seed_run(session_factory, agent="hang")
    monkeypatch.setattr(ex, "resolve_adapter", lambda name: _HangingLauncher())
    result = ex.execute_run(session_factory, rid, poll_interval=0.05,
                            max_poll_seconds=0.3)
    assert result is not None
    assert result["status"] == "failed"
    assert "timeout" in (result["error_message"] or "")


def test_execute_run_skip_non_pending(session_factory, monkeypatch):
    """非 pending（已终态）的 run 不重复执行：execute_run 直接跳过返回 None。"""
    import agentboard.executor as ex
    from agentboard import service
    from agentboard.domains.common.enums import RunStatus

    rid = _seed_run(session_factory, agent="skip")
    with session_factory() as s:
        service.report_run_result(s, rid, status=RunStatus.SUCCESS, summary="done")
    monkeypatch.setattr(ex, "resolve_adapter", lambda name: _FakeLauncher())
    result = ex.execute_run(session_factory, rid)
    assert result is None  # 终态不重复执行


# ---------------------------------------------------------------------------
# 4. MCP report_run_result 工具（真实 uvicorn + 直接调 MCP 工具函数）
# ---------------------------------------------------------------------------
def test_mcp_report_run_result_registered(api_server):
    import agentboard.mcp_server as mcp_mod

    from agentboard.mcp_server import mcp

    tools = asyncio_run_list_tools(mcp)
    names = {t.name for t in tools}
    assert "report_run_result" in names


def asyncio_run_list_tools(mcp):
    import asyncio

    async def _list():
        return await mcp.list_tools()

    return asyncio.run(_list())


def test_mcp_report_run_result_end_to_end(api_server, session_factory, monkeypatch):
    """MCP 工具 → REST 端点全链路：Agent 回写 success → 状态落库。

    执行器对已终态 run 返回 None（防重复执行）—— 幂等闭环。
    """
    import agentboard.mcp_server as mcp_mod
    from agentboard import service

    rid = _seed_run(session_factory, agent="mcp-e2e")
    # 指向本测试的 uvicorn（覆盖模块级 API_URL）
    monkeypatch.setattr(mcp_mod, "API_URL", api_server)

    from agentboard.mcp_server import report_run_result

    result = report_run_result(run_id=rid, status="success",
                               summary="MCP 全链路完成", log_ref="mcp://r1")
    assert isinstance(result, dict), result
    if "error" in result:
        pytest.fail(f"MCP report_run_result 失败: {result}")
    assert result["status"] == "success"
    assert result["summary"] == "MCP 全链路完成"
    assert result["log_ref"] == "mcp://r1"

    # DB 落库校验
    with session_factory() as s:
        run = service.get_run(s, rid)
        assert run is not None
        assert run.status == "success"
        assert run.summary == "MCP 全链路完成"
        assert run.finished_at is not None

    # 执行器对已终态 run 不重复执行（幂等闭环）
    import agentboard.executor as ex
    out = ex.execute_run(session_factory, rid, poll_interval=0.05,
                         max_poll_seconds=5)
    assert out is None
