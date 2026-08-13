"""Epic 122 (新增) — MiniMax Launcher E2E 冒烟

目标：把 MiniMax 从「minimax_invoker.py 脚本级 e2e」推到「Executor 框架
launch_run 全链路 e2e」,覆盖：

1. ``MiniMaxLauncher`` 已注册到 ``ADAPTERS``;
2. 起本地 fake HTTP server 回放决策 JSON → ``launch_run`` 走 MiniMaxLauncher
   → success + ``run.output`` 含收敛 spec;
3. API 故意 500 → FAILED + error_message 含 ``500``。

约束：自包含临时 SQLite,无外部服务依赖,纯 pytest + subprocess + http.server。
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INVOKER = ROOT / "scripts" / "minimax_invoker.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _isolate_registry():
    """隔离 ADAPTERS 与 env,避免测试间相互污染。"""
    from agentboard.executor import ADAPTERS

    snapshot = dict(ADAPTERS)
    env_snapshot = {
        k: os.environ.get(k)
        for k in (
            "AGENTBOARD_MINIMAX_BIN", "AGENTBOARD_CODEX_BIN",
            "AGENTBOARD_DEFAULT_AGENT", "MINIMAX_API_KEY", "MINIMAX_BASE_URL",
        )
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
    db_url = f"sqlite:///{tmp_path}/minimax_e2e.db"
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
          agent: str = "minimax") -> int:
    from agentboard import service
    from agentboard.domains.common.enums import ScheduleType

    with session_factory() as s:
        proj = service.create_project(s, name="P-minimax", key="PMMX")
        sch = service.create_schedule(
            s, project_id=proj.id, title="minimax e2e once",
            schedule_type=ScheduleType.ONCE, cron_expr=None,
        )
        tk = service.create_task(
            s, project_id=proj.id, story_id=None, title="T-minimax",
            spec=task_spec, priority="high",
        )
        run = service.create_run(s, schedule_id=sch.id, task_id=tk.id,
                                 idempotency_key=f"k-minimax-{time.time_ns()}")
        # 把 schedule.agent 改成 minimax,让 build_run_context 走 MiniMaxLauncher
        from agentboard.domains.scheduling.models import AgentSchedule
        sch_row = s.get(AgentSchedule, sch.id)
        sch_row.agent = agent
        s.commit()
        return run.id


def _make_payload_server(payload: dict, port: int) -> tuple[http.server.HTTPServer, threading.Thread]:
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **kw):
            pass  # 静默 access log

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload_bytes)))
            self.end_headers()
            self.wfile.write(payload_bytes)

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _make_500_server(port: int) -> tuple[http.server.HTTPServer, threading.Thread]:
    body = b'{"error":"internal server error"}'

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **kw):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


# ---- 1. 注册表 -----------------------------------------------------------


def test_minimax_registered_in_adapters():
    from agentboard.executor import ADAPTERS, MiniMaxLauncher, get_adapter

    assert "minimax" in ADAPTERS
    assert get_adapter("minimax") is MiniMaxLauncher
    m = MiniMaxLauncher()
    assert m.name == "minimax"
    assert m.env_var == "AGENTBOARD_MINIMAX_BIN"
    # 默认命令必须能解析为合法 [python, script] 列表
    cmd = m.build_command(None)
    assert cmd[0] == sys.executable
    assert Path(cmd[1]).name == "minimax_invoker.py"


def test_minimax_default_command_points_to_real_script():
    """默认 command 的脚本路径必须存在(防有人改了路径忘了同步)。"""
    from agentboard.executor import MiniMaxLauncher

    cmd = MiniMaxLauncher().build_command(None)
    assert Path(cmd[1]).is_file(), f"minimax_invoker.py not found at {cmd[1]}"


# ---- 2. launch_run 全链路 success --------------------------------------


def test_launch_run_minimax_full_path(session_factory, monkeypatch):
    """起 fake server 回 finalize 决策 → launch_run → success + output 含 spec。"""
    # minimax_invoker 从模型输出里抽 JSON 决策,这里 server 回放一个合法
    # finalize 决策(经过 think 块 + markdown 包裹,验证抽取逻辑端到端)
    payload = {
        "choices": [
            {"message": {
                "role": "assistant",
                "content": (
                    "<think>用户的需求已明确,直接收敛。</think>\n"
                    "```json\n"
                    '{"action":"finalize",'
                    '"converged_spec":"## 需求\\n做一件事\\n## 验收\\n- 有结果\\n",'
                    '"summary":"minimax 收敛"}\n'
                    "```"
                ),
            }}
        ]
    }
    port = _free_port()
    server, thread = _make_payload_server(payload, port)
    try:
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-test-fake")
        monkeypatch.setenv("MINIMAX_BASE_URL", f"http://127.0.0.1:{port}/v1")
        from agentboard.executor import launch_run

        run_id = _seed(session_factory, agent="minimax")
        result = launch_run(session_factory, run_id, poll_interval=0.05)
        assert result is not None
        assert result["status"] == "success", (
            f"expected success, got {result['status']}: {result.get('error_message')}"
        )
        out = result.get("output") or ""
        # minimax_invoker 输出的是决策 JSON, CliLauncher 透传
        assert '"action": "finalize"' in out or '"action":"finalize"' in out
        assert "做一件事" in out
        assert "验收" in out
        assert result["started_at"] is not None
        assert result["finished_at"] is not None
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


# ---- 3. API 500 → run FAILED + error_message 含 500 --------------------


def test_launch_run_minimax_api_500(session_factory, monkeypatch):
    """API 500 → minimax_invoker catch 后写 fail action + exit 0。
    CliLauncher 看到 exit 0 → SUCCESS(子进程协议层面成功),但 run.output
    含 fail 决策 JSON(含 500 详情)。这是 minimax_invoker 的「失败不 crash」
    设计,Excecutor 层透明。
    """
    port = _free_port()
    server, thread = _make_500_server(port)
    try:
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-test-fake")
        monkeypatch.setenv("MINIMAX_BASE_URL", f"http://127.0.0.1:{port}/v1")
        from agentboard.executor import launch_run

        run_id = _seed(session_factory, agent="minimax")
        result = launch_run(session_factory, run_id, poll_interval=0.05)
        assert result is not None
        # 子进程 exit 0 → success(失败信息在 output 里)
        assert result["status"] == "success"
        out = result.get("output") or ""
        # 失败细节在 fail decision 的 error 字段里
        assert '"action": "fail"' in out or '"action":"fail"' in out
        assert "500" in out or "internal server error" in out.lower()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


# ---- 4. AGENTBOARD_MINIMAX_BIN 覆盖:fake minimax invoker --------------


def test_launch_run_minimax_env_override(session_factory, monkeypatch, tmp_path):
    """AGENTBOARD_MINIMAX_BIN 可指向替代命令(便于测试 / 升级 minimax_invoker)。

    fake minimax 只写一行决策 JSON 就退出 0。
    """
    fake = tmp_path / "fake_minimax.py"
    fake.write_text(
        "import sys\n"
        "data = sys.stdin.read()\n"
        "print('{\"action\":\"ask\",\"questions\":[\"q1\",\"q2\"],\"summary\":\"fake\"}')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTBOARD_MINIMAX_BIN", f"{sys.executable} {fake}")
    from agentboard.executor import launch_run

    run_id = _seed(session_factory, agent="minimax")
    result = launch_run(session_factory, run_id, poll_interval=0.05)
    assert result is not None
    assert result["status"] == "success", (
        f"expected success, got {result['status']}: {result.get('error_message')}"
    )
    out = result.get("output") or ""
    assert '"action": "ask"' in out or '"action":"ask"' in out
    assert "q1" in out
    assert "q2" in out
