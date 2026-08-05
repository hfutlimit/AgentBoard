"""Epic 78 Story 177 — Executor daemon 模式 E2E 验证（真实执行器 + Playwright）

验证 Story 177 交付闭环（对应 Epic 78 验收「执行器 daemon 运行后能真正触发
Agent 并落 success/failed」的 CLI 层）：
1. 自起 API + Web（临时 SQLite，走完整 Alembic 迁移链）；
2. 经 REST 创建 project + schedule（agent=codex，本机无 codex bin →
   launcher 失败落 failed —— 证明 daemon 真正驱动 execute_run 完成终态回写）；
3. CLI `python -m agentboard.executor --daemon --daemon-max-runs 1` 处理
   该 pending run 后退出（processed=1，run 离开 pending）；
4. Chromium 登录 Web，进入项目 Schedule/Run 视图无 JS 报错（核心渲染冒烟）。

自包含：不依赖 18001 / 18000 / 28080 / 58125。
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from uuid import uuid4

import pytest

_HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
_RUN_WEB = importlib.util.find_spec("uvicorn") is not None and _HAS_PLAYWRIGHT

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(app_import: str, port: int, extra_env: dict | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app_import, "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_http(url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except Exception:
            time.sleep(0.3)
    return False


@pytest.fixture(scope="module")
def servers():
    if not _RUN_WEB:
        pytest.skip("playwright/uvicorn not installed")
    api_port = _free_port()
    web_port = _free_port()
    api = _start_server("agentboard.api:app", api_port)
    web = _start_server(
        "agentboard.web_app:app", web_port,
        extra_env={"AGENTBOARD_API_URL": f"http://127.0.0.1:{api_port}"},
    )
    assert _wait_http(f"http://127.0.0.1:{api_port}/api/meta"), "api not up"
    assert _wait_http(f"http://127.0.0.1:{web_port}/"), "web not up"
    yield {"api": api_port, "web": web_port}
    for p in (api, web):
        p.terminate()
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()


def _post(url: str, body: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _seed(api_base: str) -> dict:
    """注册用户 + 项目 + schedule（agent=codex），返回 token/project/schedule。"""
    try:
        resp = _post(f"{api_base}/api/auth/login",
                     {"username": "u177e2e", "password": "p177pass"})
        token = resp["token"]
    except urllib.error.HTTPError:
        reg = _post(f"{api_base}/api/auth/register",
                    {"username": "u177e2e", "password": "p177pass"})
        token = reg["token"]

    proj = _post(f"{api_base}/api/projects",
                 {"name": "P177DAEMON", "key": f"P177{uuid4().hex[:4].upper()}"},
                 token=token)

    # schedule_type 仅支持 cron/once；agent=codex（Launcher 模式，本机无 bin → failed）
    sch = _post(f"{api_base}/api/projects/{proj['id']}/schedules",
                {"title": "daemon-e2e",
                 "schedule_type": "once", "agent": "codex"},
                token=token)
    return {"token": token, "project_id": proj["id"], "schedule_id": sch["id"]}


def test_daemon_cli_processes_pending_run_and_web_renders(servers):
    """CLI --daemon 真实处理 1 个 pending run + Web 核心页面无 JS 报错。"""
    api_base = f"http://127.0.0.1:{servers['api']}"
    web_base = f"http://127.0.0.1:{servers['web']}"
    seeded = _seed(api_base)

    # ---- 创建 pending run（手动触发 schedule 或直接 POST run）----
    run = _post(f"{api_base}/api/schedules/{seeded['schedule_id']}/runs",
                {"idempotency_key": f"e2e-{uuid4().hex}"}, token=seeded["token"])
    run_id = run["id"]
    r0 = _get(f"{api_base}/api/runs/{run_id}", seeded["token"])
    assert r0["status"] == "pending", r0

    # ---- CLI daemon 处理 1 个 run 后退出（真实 executor 全链路）----
    env = os.environ.copy()
    env["AGENTBOARD_DB_URL"] = os.environ["AGENTBOARD_DB_URL"]
    env["AGENTBOARD_MCP_BACKEND"] = "db"
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "agentboard.executor",
         "--daemon", "--daemon-max-runs", "1",
         "--daemon-idle-sleep", "0.2", "--max-poll-seconds", "10"],
        capture_output=True, text=True, timeout=60, env=env, cwd=_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "daemon exit" in proc.stdout
    assert "processed=1" in proc.stdout

    # run 离开 pending（本机无 codex → failed；有则 success）
    r1 = _get(f"{api_base}/api/runs/{run_id}", seeded["token"])
    assert r1["status"] in ("success", "failed"), r1

    # ---- Playwright Web 冒烟：登录 → 项目可见 → 核心 Tab 渲染无报错 ----
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        console_errors = []
        page_errors = []
        failed = []
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed", lambda r: failed.append(r.url)
                if any(k in r.url for k in (".js", ".css", "assets/")) else None)

        page.add_init_script(
            f"localStorage.setItem('agentboard_token', '{seeded['token']}');")
        page.goto(f"{web_base}/", wait_until="networkidle", timeout=30000)
        page.wait_for_function(
            "() => document.body.innerText.includes('P177DAEMON')", timeout=15000)
        page.click("text=P177DAEMON")
        page.wait_for_timeout(2000)
        # 进入项目主视图（任务/看板核心），确认渲染
        body = page.inner_text("body")
        assert "P177DAEMON" in body
        browser.close()

    assert not page_errors, f"page errors: {page_errors}"
    assert not [e for e in console_errors if "Failed to load" not in e], \
        f"console errors: {console_errors}"
    assert not failed, f"asset 404s: {failed}"
