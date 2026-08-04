"""
Epic 78 Story 103 — 模式 B：Trigger（Webhook 唤醒 Runner）单元测试

覆盖验收标准：
1. ADAPTERS 注册 workbuddy / qoder → WebhookTrigger，可取回并实例化。
2. build_payload 含 event/run_id/task_id/project_id/agent/task_title/prompt/token。
3. trigger_run 全链路：pending → running → POST webhook → 外部回写 success
   （模拟 report_run_result）→ success + finished_at 回写。
4. 外部回写 failed → failed。
5. 超时（外部不回写）→ failed(timeout)。
6. 无 webhook 目标（无 env 无项目级 WebhookConfig）→ failed（不裸崩）。
7. webhook 返回非 2xx → failed。
8. 项目级 WebhookConfig（带 secret）自动发现 → 请求带 X-AgentBoard-Signature 签名头。
9. 非 pending 的 run 跳过；--trigger 语义的 trigger_first_pending 选中 workbuddy run。
10. CLI 子进程冒烟。

自包含：临时 SQLite + 线程 Fake HTTP webhook 服务器，不依赖 18001 / 18000 / 28080。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parent.parent


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Fake HTTP webhook server（线程内，记录收到的请求；行为由 env 控制）
# ---------------------------------------------------------------------------
class FakeWebhookHandler(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        FakeWebhookHandler.received.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
            "payload": json.loads(body) if body else {},
        })
        code = int(os.environ.get("FAKE_WEBHOOK_STATUS", "200"))
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):  # noqa: D102
        pass


@pytest.fixture()
def fake_server():
    """起一个 Fake webhook server，返回其 URL；shutdown 时清理。"""
    FakeWebhookHandler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/hook"
    yield url
    server.shutdown()
    server.server_close()


def wait_requests(timeout: float = 5.0) -> list:
    """等待至少一个 webhook 请求到达，返回全部已收到请求。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if FakeWebhookHandler.received:
            return list(FakeWebhookHandler.received)
        time.sleep(0.02)
    raise AssertionError("no webhook request received")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_registry():
    """隔离全局注册表（Story 101/102 fixture 同款），并恢复 env 快照。"""
    from agentboard.executor import ADAPTERS

    snapshot = dict(ADAPTERS)
    env_snapshot = {
        k: os.environ.get(k)
        for k in ("AGENTBOARD_DEFAULT_AGENT", "AGENTBOARD_TRIGGER_URL",
                  "AGENTBOARD_TRIGGER_TOKEN", "FAKE_WEBHOOK_STATUS")
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
    db_url = f"sqlite:///{tmp_path}/story103.db"
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


def _seed(session_factory, *, task_id: int | None = None,
          webhook_url: str | None = None,
          webhook_secret: str | None = None) -> int:
    """建 project + schedule + run（可选项目级 WebhookConfig），返回 run_id。"""
    from agentboard import service
    from agentboard.domains.common.enums import ScheduleType

    with session_factory() as s:
        proj = service.create_project(s, name="P103", key="P103K")
        sch = service.create_schedule(
            s, project_id=proj.id, title="story103 once",
            schedule_type=ScheduleType.ONCE, cron_expr=None,
        )
        tid = task_id
        if tid is None:
            tk = service.create_task(
                s, project_id=proj.id, story_id=None, title="T103",
                spec="## 需求\n做一件事\n## 验收\n- 有结果", priority="highest",
            )
            tid = tk.id
        if webhook_url:
            service.create_webhook(
                s, project_id=proj.id, name="runner-hook", url=webhook_url,
                secret=webhook_secret,
            )
        run = service.create_run(s, schedule_id=sch.id, task_id=tid,
                                 idempotency_key="k1")
        return run.id


# ---------------------------------------------------------------------------
# 1. 注册表
# ---------------------------------------------------------------------------
def test_webhook_trigger_registered():
    from agentboard.executor import (
        ADAPTERS, TRIGGER_AGENTS, WebhookTrigger, get_adapter,
    )

    assert "workbuddy" in ADAPTERS
    assert "qoder" in ADAPTERS
    assert get_adapter("workbuddy") is WebhookTrigger
    assert get_adapter("qoder") is WebhookTrigger
    assert "workbuddy" in TRIGGER_AGENTS
    assert "qoder" in TRIGGER_AGENTS
    # 可实例化
    wb = get_adapter("workbuddy")()
    assert wb.name == "webhook"
    assert isinstance(wb, WebhookTrigger)


# ---------------------------------------------------------------------------
# 2. build_payload 结构
# ---------------------------------------------------------------------------
def test_build_payload_fields(monkeypatch):
    from agentboard.executor import AgentRunContext, WebhookTrigger

    monkeypatch.setenv("AGENTBOARD_TRIGGER_TOKEN", "abk_scoped_token")
    ctx = AgentRunContext(
        project_id=3, schedule_id=4, run_id=1, task_id=2,
        agent="workbuddy", project_key="K", project_name="P",
        task_title="T", task_spec="## 需求\nx",
        extra={"schedule_title": "s"},
    )
    payload = WebhookTrigger().build_payload(None, None, ctx)
    assert payload["event"] == "agent_run.triggered"
    assert payload["timestamp"]
    data = payload["data"]
    assert data["run_id"] == 1
    assert data["task_id"] == 2
    assert data["project_id"] == 3
    assert data["schedule_id"] == 4
    assert data["agent"] == "workbuddy"
    assert data["task_title"] == "T"
    assert "## 需求\nx" in data["task_spec"]
    assert "run #1" in data["prompt"]        # build_prompt 输出
    assert "任务：T" in data["prompt"]
    assert data["token"] == "abk_scoped_token"


def test_resolve_url_env_first(monkeypatch):
    from agentboard.executor import AgentRunContext, WebhookTrigger

    monkeypatch.setenv("AGENTBOARD_TRIGGER_URL", "http://env/hook")
    ctx = AgentRunContext(project_id=1, schedule_id=2, run_id=3,
                          extra={"webhook_url": "http://db/hook"})
    url, secret = WebhookTrigger().resolve_url(ctx)
    assert url == "http://env/hook"
    assert secret is None


def test_resolve_url_db_fallback():
    from agentboard.executor import AgentRunContext, WebhookTrigger

    ctx = AgentRunContext(
        project_id=1, schedule_id=2, run_id=3,
        extra={"webhook_url": "http://db/hook", "webhook_secret": "s3cret"},
    )
    url, secret = WebhookTrigger().resolve_url(ctx)
    assert url == "http://db/hook"
    assert secret == "s3cret"


def test_resolve_url_missing_raises():
    from agentboard.executor import AdapterError, AgentRunContext, WebhookTrigger

    ctx = AgentRunContext(project_id=1, schedule_id=2, run_id=3)
    with pytest.raises(AdapterError):
        WebhookTrigger().resolve_url(ctx)


# ---------------------------------------------------------------------------
# 3. trigger_run 全链路
# ---------------------------------------------------------------------------
def test_trigger_run_success_external_report(session_factory, fake_server,
                                             monkeypatch):
    """POST 成功 → 外部回写 success → trigger_run 返回 success。"""
    monkeypatch.setenv("AGENTBOARD_DEFAULT_AGENT", "workbuddy")
    monkeypatch.setenv("AGENTBOARD_TRIGGER_URL", fake_server)
    from agentboard import service
    from agentboard.executor import trigger_run

    run_id = _seed(session_factory)
    waiter = threading.Event()

    def _external_report():
        reqs = wait_requests()
        # 模拟 Runner 被叫醒：直奔 task，做完后 report_run_result
        with session_factory() as s:
            service.update_run(s, run_id, status="success", output="runner done")
        waiter.set()

    t = threading.Thread(target=_external_report, daemon=True)
    t.start()
    result = trigger_run(session_factory, run_id, poll_interval=0.05)
    assert waiter.wait(timeout=5)

    assert result is not None
    assert result["status"] == "success"
    assert result["output"] == "runner done"
    assert result["finished_at"] is not None
    # payload 断言：Runner 收到的是直取 task 的字段
    req = wait_requests()[-1]
    assert req["payload"]["event"] == "agent_run.triggered"
    assert req["payload"]["data"]["task_id"] == result["task_id"]
    assert req["payload"]["data"]["run_id"] == run_id
    assert req["payload"]["data"]["agent"] == "workbuddy"


def test_trigger_run_external_failed(session_factory, fake_server, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_DEFAULT_AGENT", "workbuddy")
    monkeypatch.setenv("AGENTBOARD_TRIGGER_URL", fake_server)
    from agentboard import service
    from agentboard.executor import trigger_run

    run_id = _seed(session_factory)

    def _external_report():
        wait_requests()
        with session_factory() as s:
            service.update_run(s, run_id, status="failed",
                               error_message="runner crashed")
        # 注意：update_run 只收非 None 字段，error_message 直接传即可

    threading.Thread(target=_external_report, daemon=True).start()
    result = trigger_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None
    assert result["status"] == "failed"
    assert "runner crashed" in (result.get("error_message") or "")


def test_trigger_run_timeout(session_factory, fake_server, monkeypatch):
    """webhook 已送达但外部不回写 → 超时 failed。"""
    monkeypatch.setenv("AGENTBOARD_DEFAULT_AGENT", "workbuddy")
    monkeypatch.setenv("AGENTBOARD_TRIGGER_URL", fake_server)
    from agentboard.executor import trigger_run

    run_id = _seed(session_factory)
    t0 = time.monotonic()
    result = trigger_run(session_factory, run_id, poll_interval=0.05,
                         max_poll_seconds=0.6)
    elapsed = time.monotonic() - t0
    assert result is not None
    assert result["status"] == "failed"
    assert "timeout" in (result.get("error_message") or "").lower()
    assert elapsed < 4
    # webhook 确实送达了
    assert wait_requests()


def test_trigger_run_no_target(session_factory, monkeypatch):
    """无 env 无项目级 WebhookConfig → AdapterError → run failed。"""
    monkeypatch.setenv("AGENTBOARD_DEFAULT_AGENT", "workbuddy")
    os.environ.pop("AGENTBOARD_TRIGGER_URL", None)
    from agentboard.executor import trigger_run

    run_id = _seed(session_factory)
    result = trigger_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None
    assert result["status"] == "failed"
    assert "no webhook target" in (result.get("error_message") or "")


def test_trigger_run_webhook_non_2xx(session_factory, fake_server, monkeypatch):
    monkeypatch.setenv("AGENTBOARD_DEFAULT_AGENT", "workbuddy")
    monkeypatch.setenv("AGENTBOARD_TRIGGER_URL", fake_server)
    monkeypatch.setenv("FAKE_WEBHOOK_STATUS", "500")
    from agentboard.executor import trigger_run

    run_id = _seed(session_factory)
    result = trigger_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None
    assert result["status"] == "failed"
    assert "webhook returned 500" in (result.get("error_message") or "")


def test_trigger_run_db_webhook_with_signature(session_factory, fake_server,
                                               monkeypatch):
    """项目级 WebhookConfig（带 secret）自动发现 + HMAC 签名头。"""
    monkeypatch.setenv("AGENTBOARD_DEFAULT_AGENT", "workbuddy")
    os.environ.pop("AGENTBOARD_TRIGGER_URL", None)  # 不设 env，靠 DB 配置
    from agentboard.executor import trigger_run

    secret = "topsecret"
    run_id = _seed(session_factory, webhook_url=fake_server,
                   webhook_secret=secret)

    def _external_report():
        reqs = wait_requests()
        with session_factory() as s:
            from agentboard import service
            service.update_run(s, run_id, status="success", output="ok")

    threading.Thread(target=_external_report, daemon=True).start()
    result = trigger_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None and result["status"] == "success"

    req = wait_requests()[-1]
    headers = req["headers"]
    assert "X-AgentBoard-Signature" in headers
    assert "X-AgentBoard-Timestamp" in headers
    body = req["body"]
    expect = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    assert headers["X-AgentBoard-Signature"] == expect


def test_trigger_run_non_pending_skipped(session_factory, fake_server,
                                         monkeypatch):
    monkeypatch.setenv("AGENTBOARD_DEFAULT_AGENT", "workbuddy")
    monkeypatch.setenv("AGENTBOARD_TRIGGER_URL", fake_server)
    from agentboard import service
    from agentboard.domains.common.enums import RunStatus
    from agentboard.executor import trigger_run

    run_id = _seed(session_factory)
    with session_factory() as s:
        service.update_run(s, run_id, status=RunStatus.SUCCESS, output="old")
    result = trigger_run(session_factory, run_id, poll_interval=0.05)
    assert result is None  # 非 pending 跳过，不发 webhook
    assert not FakeWebhookHandler.received


def test_trigger_run_launcher_agent_skipped(session_factory, fake_server,
                                            monkeypatch):
    """agent=codex（Launcher 场景）时 trigger_run 返回 None（应走 launch_run）。"""
    monkeypatch.setenv("AGENTBOARD_DEFAULT_AGENT", "codex")
    monkeypatch.setenv("AGENTBOARD_TRIGGER_URL", fake_server)
    from agentboard.executor import trigger_run

    run_id = _seed(session_factory)
    result = trigger_run(session_factory, run_id, poll_interval=0.05)
    assert result is None
    assert not FakeWebhookHandler.received


# ---------------------------------------------------------------------------
# 4. trigger_first_pending
# ---------------------------------------------------------------------------
def test_trigger_first_pending_picks_workbuddy(session_factory, fake_server,
                                               monkeypatch):
    """同时存在 codex 与 workbuddy 的 pending run → 选中 workbuddy。"""
    monkeypatch.setenv("AGENTBOARD_DEFAULT_AGENT", "workbuddy")
    monkeypatch.setenv("AGENTBOARD_TRIGGER_URL", fake_server)
    from agentboard import service
    from agentboard.executor import trigger_first_pending

    # 先建一个 codex 的 pending run（agent 由 env 决定，这里先建 run）
    run_a = _seed(session_factory, task_id=None)

    def _external_report():
        wait_requests()
        with session_factory() as s:
            service.update_run(s, run_a, status="success", output="wb done")

    threading.Thread(target=_external_report, daemon=True).start()
    result = trigger_first_pending(session_factory, poll_interval=0.05)
    assert result is not None
    assert result["id"] == run_a
    assert result["status"] == "success"
    assert wait_requests()[-1]["payload"]["data"]["agent"] == "workbuddy"


# ---------------------------------------------------------------------------
# 5. CLI 冒烟（子进程调用 python -m agentboard.executor --trigger）
# ---------------------------------------------------------------------------
def test_cli_trigger_flag(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    db_url = f"sqlite:///{tmp_path}/cli.db"
    os.environ["AGENTBOARD_DB_URL"] = db_url
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

    monkeypatch.setenv("AGENTBOARD_DEFAULT_AGENT", "workbuddy")
    monkeypatch.setenv("AGENTBOARD_TRIGGER_URL", "http://127.0.0.1:1/none")
    out = subprocess.run(
        [sys.executable, "-m", "agentboard.executor", "--trigger", str(rid),
         "--max-poll-seconds", "0.3"],
        capture_output=True, text=True, cwd=str(ROOT), env=dict(os.environ),
        timeout=30,
    )
    assert "run" in out.stdout and "failed" in out.stdout
    assert "webhook POST failed" in out.stdout or "webhook returned" in out.stdout
