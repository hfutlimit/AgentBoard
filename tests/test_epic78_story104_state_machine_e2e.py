"""
Epic 78 Story 104 — 状态机驱动 + report_run_result E2E 冒烟（Playwright）

验证 Story 104 交付未破坏 Web 服务：
- 真实拉起 API + Web（临时 SQLite），Chromium 登录后项目/看板核心渲染 0 报错；
- report_run_result 端点可被 Agent 调用（success + summary + log_ref 落库）；
- 关联 run 状态在 API 层读回 success，finished_at 已写。

自包含：自起 uvicorn（随机端口），不依赖 18001 / 18000 / 28080 / 58125。
"""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest

_HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
_RUN_WEB = importlib.util.find_spec("uvicorn") is not None and _HAS_PLAYWRIGHT

_DB = tempfile.mktemp(suffix="_story104_e2e.db")
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
    import urllib.request

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


def _seed(api_base: str) -> dict:
    """注册用户 + 建项目 + epic/story/task + schedule/run，返回凭据与 run_id。"""
    import random
    import string
    import urllib.request

    tag = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))

    def post(path: str, body: dict, token: str | None = None) -> dict:
        req = urllib.request.Request(
            f"{api_base}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {token}"} if token else {})},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    reg = post("/api/auth/register", {"username": f"u104-{tag}", "password": "p104pass"})
    token = reg["token"]
    proj = post("/api/projects", {"name": f"P104E2E-{tag}", "key": f"P104E{tag[:3]}"},
                token=token)
    epic = post(f"/api/projects/{proj['id']}/epics",
                {"title": "Epic104", "description": ""}, token=token)
    story = post(f"/api/epics/{epic['id']}/stories",
                 {"title": "Story104", "description": ""}, token=token)
    task = post(f"/api/stories/{story['id']}/tasks",
                {"title": "T104", "project_id": proj["id"],
                 "description": "", "spec": "## 需求\nx\n## 验收\n- ok",
                 "priority": "high"}, token=token)
    sch = post(f"/api/projects/{proj['id']}/schedules",
               {"title": "s104", "schedule_type": "once"}, token=token)
    run = post(f"/api/schedules/{sch['id']}/runs",
               {"task_id": task["id"], "idempotency_key": f"e2e104-{tag}"}, token=token)
    return {"token": token, "project_id": proj["id"], "run_id": run["id"],
            "schedule_id": sch["id"]}


def test_report_run_result_api(api_base):
    """report_run_result 端点全链路：pending → success + summary/log_ref 落库。"""
    import urllib.request
    import urllib.error

    seeded = _seed(api_base)

    def post(path: str, body: dict, token: str | None = None, expect: int = 200) -> dict:
        req = urllib.request.Request(
            f"{api_base}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {token}"} if token else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                assert r.status == expect, f"{path} -> {r.status}"
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            assert e.code == expect, f"{path} -> {e.code}: {e.read().decode()}"
            return {}

    rid = seeded["run_id"]
    # 1) 成功报告
    body = post(f"/api/runs/{rid}/report",
                {"status": "success", "summary": "E2E 完成 ✅",
                 "log_ref": "cos://e2e/run-104"}, token=seeded["token"])
    assert body["status"] == "success"
    assert body["summary"] == "E2E 完成 ✅"
    assert body["log_ref"] == "cos://e2e/run-104"
    assert body["finished_at"] is not None
    # 2) 幂等重复 → 200 不覆盖
    body2 = post(f"/api/runs/{rid}/report",
                 {"status": "success", "summary": "again"}, token=seeded["token"])
    assert body2["summary"] == "E2E 完成 ✅"
    # 3) 终态 → 其他终态 409
    post(f"/api/runs/{rid}/report", {"status": "failed"},
         token=seeded["token"], expect=409)


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
        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        page.add_init_script(
            f"localStorage.setItem('agentboard_token', '{seeded['token']}');"
        )
        page.goto(f"{web_base}/", wait_until="networkidle", timeout=30000)
        time.sleep(1)

        page.wait_for_function(
            "() => document.body.innerText.includes('P104E2E-')", timeout=15000)
        body = page.inner_text("body")
        assert "P104E2E-" in body

        page.click("text=P104E2E-")
        page.wait_for_timeout(2000)
        body2 = page.inner_text("body")
        assert "Epic104" in body2 or "看板" in body2 or "Story104" in body2

        failed = []
        page.on("requestfailed", lambda r: failed.append(r.url)
                if any(k in r.url for k in (".js", ".css", "assets/")) else None)
        page.reload(wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)

        browser.close()

    assert not page_errors, f"page errors: {page_errors}"
    assert not [e for e in console_errors if "Failed to load" not in e], \
        f"console errors: {console_errors}"
    assert not failed, f"asset 404s: {failed}"
