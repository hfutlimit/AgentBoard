"""Epic 97 P0 · MCP 工具可用性修复 — 真实浏览器 + 真实栈 E2E。

自包含拉起真实 API + Web（临时 SQLite），并用 Chromium 驱动 SPA 验证：

1. 登录 UI 流无崩溃，仪表盘与看板正常渲染（证明本次改动零前端回归）；
2. 无 console error / pageerror / 非预期 404；
3. **关键**：把 MCP 客户端指向同一套真实运行的栈，直接调用本次修复的工具，
   并让浏览器**读回**这些工具造成的数据变更——证明「MCP 写入 → Web 可见」
   这条自动开发闭环真正贯通（修复前 batch_update_task_status 会直接 NameError）。
4. 截图留证。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic97_mcp_tool_availability_e2e.py -q
未安装 playwright / Chromium 时自动 skip。
"""
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest

_HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None

# 独立临时数据库（与其它测试隔离）
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agentboard import mcp_server  # noqa: E402


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
        [sys.executable, "-m", "uvicorn", app_import,
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait(url: str, timeout: float = 30.0) -> None:
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"服务在 {url} 启动超时")


@pytest.fixture(scope="module")
def servers():
    api_port = _free_port()
    web_port = _free_port()
    api_proc = _start_server("agentboard.api:app", api_port)
    web_proc = _start_server(
        "agentboard.web_app:app", web_port,
        {"AGENTBOARD_API_URL": f"http://127.0.0.1:{api_port}"},
    )
    api_base = f"http://127.0.0.1:{api_port}"
    web_base = f"http://127.0.0.1:{web_port}"
    prev_url = mcp_server.API_URL
    try:
        _wait(api_base + "/api/meta")
        _wait(web_base + "/")
        mcp_server.API_URL = api_base  # MCP 工具打向同一套真实栈
        yield api_base, web_base
    finally:
        mcp_server.API_URL = prev_url
        for p in (api_proc, web_proc):
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()


@pytest.fixture(scope="module")
def browser():
    if not _HAS_PLAYWRIGHT:
        pytest.skip("playwright 未安装")
    from playwright.sync_api import sync_playwright
    try:
        pw = sync_playwright().start()
        chromium = pw.chromium.launch(headless=True, args=["--no-proxy-server"])
    except Exception as e:
        pytest.skip(f"Chromium 不可用: {e}")
    try:
        yield chromium
    finally:
        try:
            chromium.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    errors: list[tuple[str, str]] = []
    bad_responses: list[str] = []

    def _on_console(m):
        if m.type == "error":
            errors.append(("console", m.text))

    def _on_pageerror(e):
        errors.append(("pageerror", str(e)))

    def _on_response(r):
        # 只统计静态资源 404；/api/* 的 abort 属良性（SPA 竞态）
        if r.status == 404 and any(r.url.endswith(ext) for ext in (".js", ".css", ".svg", ".ico")):
            bad_responses.append(f"{r.status} {r.url}")

    pg.on("console", _on_console)
    pg.on("pageerror", _on_pageerror)
    pg.on("response", _on_response)
    pg._errors = errors            # type: ignore[attr-defined]
    pg._bad_responses = bad_responses  # type: ignore[attr-defined]
    try:
        yield pg
    finally:
        pg.close()
        ctx.close()


def _ui_login(page, base: str, username: str, password: str) -> None:
    page.goto(base + "/", wait_until="networkidle")
    page.wait_for_selector('input[name="username"]', state="visible", timeout=15000)
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click("button.login-submit")
    page.wait_for_selector("#home-new-project", state="visible", timeout=15000)


def test_mcp_tools_drive_real_stack_and_ui_stays_clean(page, servers):
    """MCP 工具写入 → 浏览器读回；同时断言前端零报错。"""
    import httpx

    api_base, web_base = servers
    ts = str(int(time.time()))
    user, password = "e97e2e" + ts, "secret123"

    reg = httpx.post(f"{api_base}/api/auth/register",
                     json={"username": user, "password": password}, timeout=10)
    assert reg.status_code in (200, 201), f"注册应成功: {reg.status_code} {reg.text}"

    # ---- UI 登录 + 仪表盘渲染 ----
    _ui_login(page, web_base, user, password)
    page.wait_for_selector("#app", state="visible", timeout=10000)
    dash_text = page.inner_text("#app")
    assert "项目协作一目了然" in dash_text or "仪表盘" in dash_text, "仪表盘应渲染"

    token = page.evaluate("localStorage.getItem('agentboard_token')")
    assert token, "登录后应写入 agentboard_token"
    os.environ["AGENTBOARD_MCP_TOKEN"] = token
    headers = {"Authorization": f"Bearer {token}"}

    # ---- 备好数据 ----
    pid = httpx.post(f"{api_base}/api/projects", headers=headers,
                     json={"name": "E97闭环项目" + ts}, timeout=10).json()["id"]
    eid = httpx.post(f"{api_base}/api/projects/{pid}/epics", headers=headers,
                     json={"title": "MCP 闭环"}, timeout=10).json()["id"]
    sid = httpx.post(f"{api_base}/api/epics/{eid}/stories", headers=headers,
                     json={"title": "闭环 Story"}, timeout=10).json()["id"]
    tids = [
        httpx.post(f"{api_base}/api/stories/{sid}/tasks", headers=headers,
                   json={"project_id": pid, "title": f"闭环任务{i}", "type": "task"},
                   timeout=10).json()["id"]
        for i in range(2)
    ]

    # ---- 关键：真调 MCP 工具（修复前这里直接 NameError）----
    found = mcp_server.search_tasks_enhanced(project_id=pid, status=["backlog"])
    assert isinstance(found, list) and len(found) >= 2, \
        f"search_tasks_enhanced 应搜到 2 个 backlog 任务，实得 {found!r}"

    upd = mcp_server.batch_update_task_status(tids, "todo")
    assert isinstance(upd, dict) and len(upd.get("updated", [])) == 2, \
        f"batch_update_task_status 应更新 2 条，实得 {upd!r}"

    exported = mcp_server.export_project_data(pid)
    assert isinstance(exported, dict) and "error" not in exported, \
        f"export_project_data 应成功，实得 {exported!r}"

    # ---- 浏览器读回 MCP 造成的变更 ----
    # 项目是登录之后才建的，仪表盘不会自动感知，先刷新拿到最新列表。
    page.reload(wait_until="networkidle")
    page.wait_for_selector("text=打开", state="visible", timeout=20000)
    # 走点击导航而非深链：SPA 假路由对 goto 有已知的 tasks() 信号竞态。
    page.click("text=打开")
    page.wait_for_function("!document.querySelector('.skeleton')", timeout=60000)
    page.wait_for_selector("text=Backlog", state="visible", timeout=15000)
    page.click("text=Backlog")
    page.wait_for_selector("text=闭环任务0", state="visible", timeout=15000)

    board_text = page.inner_text("#app")
    assert "闭环任务0" in board_text and "闭环任务1" in board_text, \
        f"MCP 创建的任务应在 Web 端可见，实际渲染：{board_text[:300]}"
    # 闭环关键断言：MCP batch_update_task_status 写入的 todo 必须在 UI 上显示为「待办」
    assert "待办" in board_text, (
        "MCP 批量改状态的结果未反映到 Web 端——修复前该工具直接 NameError，"
        f"实际渲染：{board_text[:300]}"
    )

    shot_dir = os.path.join(_ROOT, "screenshots")
    os.makedirs(shot_dir, exist_ok=True)
    page.screenshot(path=os.path.join(shot_dir, "epic97_mcp_tools_board.png"), full_page=False)

    # ---- 零报错断言 ----
    assert not page._bad_responses, f"存在静态资源 404：{page._bad_responses}"
    real_errors = [
        (kind, msg) for kind, msg in page._errors
        if "ERR_ABORTED" not in msg and "Failed to load resource" not in msg
    ]
    assert not real_errors, f"页面存在 JS 报错：{real_errors}"
