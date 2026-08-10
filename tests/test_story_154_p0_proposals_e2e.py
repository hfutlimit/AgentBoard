"""Epic 96 P0 · Proposal 后端基座 — 真实浏览器 E2E 冒烟。

自包含启动真实 API + Web（临时 SQLite，init_db 会自动 alembic upgrade head 应用
proposals 三表迁移），用 Chromium 驱动 SPA 验证：

1. 注册 / 登录 UI 流无崩溃（验证本次后端增量改动未破坏既有契约）；
2. 仪表盘（lists 区域）与项目看板（board 区域）正常渲染，无 console / pageerror /
   非预期 401 / 404；
3. 通过真实运行的后端端点 `POST /api/proposals` + `GET /api/proposals` + 状态机迁移
   端到端验证新增 Proposal CRUD 契约在完整技术栈下可用；
4. 截图留证。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic96_p0_proposals_e2e.py -q
未安装 playwright / Chromium 时自动 skip。
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
_RUN = importlib.util.find_spec("uvicorn") is not None and _HAS_PLAYWRIGHT

# 独立临时数据库（与 test_epic96_p0_proposals / test_playwright_e2e 隔离）
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
    try:
        _wait(api_base + "/api/meta")
        _wait(web_base + "/")
        yield api_base, web_base
    finally:
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
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    errors = []

    def _on_console(m):
        if m.type == "error":
            errors.append(("console", m.text))

    def _on_pageerror(e):
        errors.append(("pageerror", str(e)))

    pg.on("console", _on_console)
    pg.on("pageerror", _on_pageerror)
    pg._errors = errors  # type: ignore[attr-defined]
    try:
        yield pg
    finally:
        pg.close()
        ctx.close()


def _ui_login(page, base: str, username: str, password: str):
    """走真实 UI 完成登录（当前 SPA 未登录直接重定向到 /login 表单）。"""
    page.goto(base + "/", wait_until="networkidle")
    page.wait_for_selector('input[name="username"]', state="visible", timeout=10000)
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click("button.login-submit")
    # 登录成功 -> 仪表盘渲染（出现「＋ 新建项目」或品牌 Hero 文案）
    page.wait_for_selector("#home-new-project", state="visible", timeout=12000)


def test_epic96_p0_proposal_backend_e2e(page, servers):
    """真实浏览器：登录 + 列表/看板渲染无回归；新增 /api/proposals 端点在完整栈下可用。"""
    import httpx

    api_base, web_base = servers
    ts = str(int(time.time()))
    user = "e2eprop" + ts
    password = "secret123"

    # 通过 API 注册账号（避免依赖 SPA 注册 tab 选择器）
    reg = httpx.post(f"{api_base}/api/auth/register",
                     json={"username": user, "password": password}, timeout=10)
    assert reg.status_code in (200, 201), f"注册应成功: {reg.status_code} {reg.text}"

    # ---- 登录 + 列表（lists）区域渲染（无回归验证）----
    _ui_login(page, web_base, user, password)
    page.wait_for_selector("#app", state="visible", timeout=10000)
    dash_text = page.inner_text("#app")
    assert "项目协作一目了然" in dash_text or "仪表盘" in dash_text, "仪表盘应渲染"
    shot_dir = os.path.join(_ROOT, "screenshots")
    os.makedirs(shot_dir, exist_ok=True)
    page.screenshot(path=os.path.join(shot_dir, "epic96_p0_dashboard.png"), full_page=False)

    # ---- 通过真实运行的后端验证新增 Proposal 端点 ----
    token = page.evaluate("localStorage.getItem('agentboard_token')")
    assert token, "登录后应写入 agentboard_token"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 创建项目（供 proposal 挂靠；同时验证 projects 端点未被本次改动破坏）
    proj = httpx.post(f"{api_base}/api/projects",
                      headers=headers,
                      json={"name": "E2EProposal项目" + ts, "description": "e2e"},
                      timeout=10)
    assert proj.status_code == 201, f"POST /api/projects 应 201: {proj.status_code} {proj.text}"
    pid = proj.json()["id"]

    # 列表端点初始为空（成员作用域）
    list0 = httpx.get(f"{api_base}/api/proposals", headers=headers, timeout=10)
    assert list0.status_code == 200, f"GET /api/proposals 应 200: {list0.status_code}"
    assert list0.json() == [], f"初始 proposals 应为空列表，实际: {list0.text}"

    # 创建 Proposal（draft）
    create = httpx.post(
        f"{api_base}/api/proposals",
        headers=headers,
        json={"project_id": pid, "title": "E2E 提案" + ts, "content": "需求初稿"},
        timeout=10,
    )
    assert create.status_code == 201, f"POST /api/proposals 应 201: {create.status_code} {create.text}"
    body = create.json()
    assert body["status"] == "draft", f"新建 proposal 应为 draft，实际: {body.get('status')}"
    prop_id = body["id"]

    # 取回验证
    get1 = httpx.get(f"{api_base}/api/proposals/{prop_id}", headers=headers, timeout=10)
    assert get1.status_code == 200, f"GET /api/proposals/{{id}} 应 200: {get1.status_code}"
    assert get1.json()["title"] == "E2E 提案" + ts

    # 状态机合法迁移 draft -> queued -> analyzing
    for st in ("queued", "analyzing"):
        r = httpx.put(f"{api_base}/api/proposals/{prop_id}/status",
                      headers=headers, json={"status": st}, timeout=10)
        assert r.status_code == 200, f"proposal {st} 应 200: {r.status_code} {r.text}"

    # 非法迁移应被拒（analyzing 不能直接跳 story_created）
    bad = httpx.put(f"{api_base}/api/proposals/{prop_id}/status",
                    headers=headers, json={"status": "story_created"}, timeout=10)
    assert bad.status_code == 400, f"非法迁移应 400，实际: {bad.status_code} {bad.text}"

    # Worker 轮询端点
    pend = httpx.get(f"{api_base}/api/proposals/pending", headers=headers, timeout=10)
    assert pend.status_code == 200, f"GET /api/proposals/pending 应 200: {pend.status_code}"

    # 列表端点此时应包含该 proposal
    list1 = httpx.get(f"{api_base}/api/proposals", headers=headers, timeout=10)
    assert list1.status_code == 200
    assert any(p["id"] == prop_id for p in list1.json()), "列表应含刚创建的 proposal"

    # ---- 看板（board）区域导航渲染（无回归验证）----
    page.goto(web_base + "/project/" + str(pid), wait_until="networkidle")
    page.wait_for_selector("#app", state="visible", timeout=10000)
    page.wait_for_timeout(1200)
    assert "/project/" + str(pid) in page.url, f"应停留在项目看板: {page.url}"
    page.screenshot(path=os.path.join(shot_dir, "epic96_p0_board.png"), full_page=False)

    # ---- 前端错误采集：过滤良性噪声（ERR_ABORTED / favicon）----
    real = [
        e for e in page._errors  # type: ignore[attr-defined]
        if not (isinstance(e[1], str) and ("ERR_ABORTED" in e[1] or "favicon" in e[1].lower()))
    ]
    assert not real, f"前端存在非预期错误: {real}"

    print(f"[E2E] proposal id={prop_id} project id={pid} — 全链路通过")
    print(f"[E2E] 截图: {shot_dir}/epic96_p0_dashboard.png, {shot_dir}/epic96_p0_board.png")
