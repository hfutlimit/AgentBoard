"""AgentBoard 前端 Web 自动化测试（Playwright 真实浏览器 E2E）。

与 `tests/test_web_flow.py`（httpx 模拟 SPA 行为）互补：本文件用 **真实 Chromium**
驱动 SPA，验证真实的 UI 行为（鉴权界面、注册/登录流、DOM 交互），这些 HTTP 等价
校验无法覆盖。

覆盖范围（按 Epic 9 切片推进）：
- Story 9.1 测试骨架：启动真实 API + Web（临时 SQLite）的 fixture、Chromium page fixture、
  `ui_register` / `ui_login` UI 辅助，以及注册 / 登录流冒烟用例。
- Story 9.2 真实交互用例（项目树 CRUD / 状态流转 / spec 编辑 / 错误分支）：后续切片。

运行：
    PYTHONPATH=. python -m pytest tests/test_playwright_e2e.py -q
    # 首次需安装浏览器：
    pip install playwright && playwright install chromium

说明：浏览器二进制 / playwright 未安装时，用例自动 skip（不报错），便于无浏览器环境跑 CI。
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

# 独立临时数据库（与 test_web_flow / test_backend_flow 隔离）
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

# 强制重载 agentboard，使 engine 绑定到上面的临时库
for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(app_import: str, port: int, extra_env: dict | None = None) -> subprocess.Popen:
    """以独立子进程真实拉起 uvicorn 服务（api 或 web）。"""
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


def _wait(url: str, timeout: float = 15.0) -> None:
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"服务在 {url} 启动超时")


@pytest.fixture(scope="module")
def servers():
    """同时启动真实 API 与真实 Web 服务，返回 (api_base, web_base)。"""
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
        pytest.skip("playwright 未安装；运行: pip install playwright && playwright install chromium")
    from playwright.sync_api import sync_playwright
    try:
        pw = sync_playwright().start()
        chromium = pw.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip(f"Chromium 不可用（可能未执行 playwright install chromium）：{e}")
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
    try:
        yield pg
    finally:
        pg.close()
        ctx.close()


# ---------------- UI 辅助 ----------------
def _open_auth(page, base: str):
    """打开应用并点击顶栏「登录」按钮进入鉴权界面（后端默认开放，故需手动点开）。"""
    page.goto(base + "/")
    # 后端开放时应用直接渲染，顶栏显示「登录」按钮；点击进入鉴权界面
    page.wait_for_selector("#login-btn", state="visible", timeout=10000)
    page.click("#login-btn")
    page.wait_for_selector("#auth-form", state="visible", timeout=10000)


def ui_register(page, base: str, username: str, password: str):
    """走真实 UI 完成注册并进入应用。"""
    _open_auth(page, base)
    page.click('.auth-tab[data-mode="register"]')
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click("#auth-submit")
    # 注册成功 -> 应用主体渲染、顶栏出现登出按钮
    page.wait_for_selector("#logout-btn", state="visible", timeout=10000)


def ui_login(page, base: str, username: str, password: str):
    """走真实 UI 完成登录并进入应用。"""
    _open_auth(page, base)
    page.click('.auth-tab[data-mode="login"]')
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click("#auth-submit")
    page.wait_for_selector("#logout-btn", state="visible", timeout=10000)


# ---------------- 错误分支辅助 ----------------
def _wait_toast(page, text: str, timeout: float = 8000.0):
    """等待 #toast 容器文本出现期望子串（证明走了对应 UI 分支）。"""
    page.wait_for_function(
        "document.querySelector('#toast') && "
        "document.querySelector('#toast').textContent.includes(" + json.dumps(text) + ")",
        timeout=timeout,
    )


# ---------------- 用例 ----------------
def test_e2e_register_flow(page, servers):
    """真实浏览器：注册 UI 流 -> 进入应用 + localStorage 写入 token。"""
    api_base, web_base = servers
    ui_register(page, web_base, "e2euser1", "secret123")
    # 应用主体已渲染（品牌 Hero 条）
    page.wait_for_selector(".hero", state="visible", timeout=10000)
    # 登录态持久化：注册成功后 SPA 把 token 存入 localStorage
    token = page.evaluate("localStorage.getItem('agentboard_token')")
    assert token, "注册成功后未写入 agentboard_token"


def test_e2e_login_flow(page, servers):
    """真实浏览器：注册后登出，再用同一账号登录 UI 流 -> 重新进入应用。"""
    api_base, web_base = servers
    ui_register(page, web_base, "e2euser2", "secret123")
    # 登出 -> 回到鉴权界面
    page.click("#logout-btn")
    page.wait_for_selector("#auth-form", state="visible", timeout=10000)
    # 用同一账号登录
    ui_login(page, web_base, "e2euser2", "secret123")
    page.wait_for_selector(".hero", state="visible", timeout=10000)
    token = page.evaluate("localStorage.getItem('agentboard_token')")
    assert token, "登录成功后未写入 agentboard_token"


def test_e2e_auth_error_branch(page, servers):
    """真实浏览器：错误密码登录 + 重复注册 -> UI 报错且停留在鉴权界面（不进入应用）。

    对应 Story 9.2「错误密码/重复注册报错（UI 错误分支）」。后端开放，故需手动点开
    鉴权界面；错误凭证应触发 error toast 且 #logout-btn 不出现、#auth-form 仍可见。
    """
    api_base, web_base = servers
    # 先成功注册一个账号（用于后续错误密码/重复注册场景）
    ui_register(page, web_base, "e2eerr", "secret123")
    page.wait_for_selector("#logout-btn", state="visible", timeout=10000)

    # ---- 1) 错误密码登录 ----
    page.click("#logout-btn")
    page.wait_for_selector("#auth-form", state="visible", timeout=10000)
    page.click('.auth-tab[data-mode="login"]')
    page.fill('input[name="username"]', "e2eerr")
    page.fill('input[name="password"]', "wrongpass")
    page.click("#auth-submit")
    # 错误凭证：出现「登录失败」toast，且停留在鉴权界面
    _wait_toast(page, "登录失败")
    assert page.query_selector("#logout-btn") is None, "错误密码不应进入应用"
    assert page.query_selector("#auth-form") is not None, "错误密码应停留在登录界面"

    # ---- 2) 重复注册（同一用户名）----
    page.click('.auth-tab[data-mode="register"]')
    page.fill('input[name="username"]', "e2eerr")
    page.fill('input[name="password"]', "secret123")
    page.click("#auth-submit")
    _wait_toast(page, "注册失败")
    assert page.query_selector("#logout-btn") is None, "重复注册不应进入应用"
    assert page.query_selector("#auth-form") is not None, "重复注册应停留在注册界面"
