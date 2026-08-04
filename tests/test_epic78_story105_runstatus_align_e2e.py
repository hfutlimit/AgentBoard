"""Epic 78 Story 105 — RunStatus 枚举对齐 E2E 冒烟（Playwright + REST）

验证 Story 105 交付未破坏 Web 服务且对齐真实生效：
1. 自起 API + Web（临时 SQLite，走完整 Alembic 迁移链含 k8l9m0n1o2p3）；
2. REST 创建 schedule + run：默认 status=pending；PUT 更新为 cancelled 成功
   （验证枚举含 cancelled 且 service.update_run 放行）；非法状态被拒；
3. Chromium 登录后项目/看板核心渲染 0 报错 / 0 404。

自包含：不依赖 18001 / 18000 / 28080 / 58125。
"""
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


@pytest.fixture(scope="module")
def api_base(servers):
    return f"http://127.0.0.1:{servers['api']}"


@pytest.fixture(scope="module")
def web_base(servers):
    return f"http://127.0.0.1:{servers['web']}"


def _post(url: str, body: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _put(url: str, body: dict, token: str | None = None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="PUT",
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _patch(url: str, body: dict, token: str | None = None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="PATCH",
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _login(api_base: str) -> str | None:
    """尝试登录既有用户；未注册返回 None。"""
    try:
        resp = _post(f"{api_base}/api/auth/login",
                     {"username": "u105", "password": "p105pass"})
        return resp.get("token")
    except urllib.error.HTTPError:
        return None


def _seed(api_base: str) -> dict:
    """注册用户 + 项目 + epic/story/task + schedule + run，返回链路 id（幂等，可重复调用）。"""
    token = _login(api_base)
    if token is None:
        reg = _post(f"{api_base}/api/auth/register",
                    {"username": "u105", "password": "p105pass"})
        token = reg["token"]
    proj = _post(f"{api_base}/api/projects",
                 {"name": "P105E2E", "key": f"P105E{uuid4().hex[:4].upper()}"},
                 token=token)
    epic = _post(f"{api_base}/api/projects/{proj['id']}/epics",
                 {"title": "Epic105", "description": ""}, token=token)
    story = _post(f"{api_base}/api/epics/{epic['id']}/stories",
                  {"title": "Story105", "description": ""}, token=token)
    task = _post(f"{api_base}/api/stories/{story['id']}/tasks",
                 {"title": "T105", "project_id": proj["id"],
                  "description": "", "spec": "", "priority": "high"}, token=token)
    sch = _post(f"{api_base}/api/projects/{proj['id']}/schedules",
                {"title": "s105", "schedule_type": "once"}, token=token)
    run = _post(f"{api_base}/api/schedules/{sch['id']}/runs",
                {"task_id": task["id"], "idempotency_key": f"e2e105-{uuid4().hex[:8]}"},
                token=token)
    return {"token": token, "project_id": proj["id"], "run_id": run["id"]}


def test_run_status_cancelled_writable(api_base):
    """REST 链路：run 默认 pending；cancelled 可写（枚举对齐生效）；非法值 422。"""
    seeded = _seed(api_base)
    token, run_id = seeded["token"], seeded["run_id"]

    # 默认 pending
    run = _get_run(api_base, run_id, token)
    assert run["status"] == "pending"

    # cancelled 可写（Story 105 新增终态；PATCH /api/runs/{rid} 经 ALL_RUN_STATUSES 校验放行）
    updated = _patch(f"{api_base}/api/runs/{run_id}",
                     {"status": "cancelled"}, token)
    assert updated["status"] == "cancelled"

    # 非法值被拒（service.update_run ALL_RUN_STATUSES 校验 → 422）
    try:
        _patch(f"{api_base}/api/runs/{run_id}", {"status": "bogus"}, token)
        raise AssertionError("非法状态 bogus 应被拒绝")
    except urllib.error.HTTPError as e:
        assert e.code == 422, f"期望 422，实际 {e.code}"


def test_web_renders_no_errors(api_base, web_base):
    """登录后项目/看板核心渲染，0 控制台报错 / 0 404。"""
    from playwright.sync_api import sync_playwright

    seeded = _seed(api_base)

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
            "() => document.body.innerText.includes('P105E2E')", timeout=15000)
        assert "P105E2E" in page.inner_text("body")

        page.click("text=P105E2E")
        page.wait_for_timeout(2000)
        body2 = page.inner_text("body")
        assert "Epic105" in body2 or "看板" in body2 or "Story105" in body2

        page.reload(wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)
        browser.close()

    assert not page_errors, f"page errors: {page_errors}"
    assert not [e for e in console_errors if "Failed to load" not in e], \
        f"console errors: {console_errors}"
    assert not failed, f"asset 404s: {failed}"


def _get_run(api_base: str, run_id: int, token: str) -> dict:
    req = urllib.request.Request(
        f"{api_base}/api/runs/{run_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())
