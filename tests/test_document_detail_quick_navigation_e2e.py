"""文档详情正文/评论快速定位端到端验收。

运行前先执行 ``cd src/frontend && npm run build``，再显式运行：
``python -m pytest tests/test_document_detail_quick_navigation_e2e.py -m e2e -q``。

测试使用临时 SQLite 和临时 Web/API 进程，不写入共享开发数据；默认 pytest
配置排除 ``e2e`` 标记，因此普通单元测试不会启动浏览器或服务。
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest


_HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
_HAS_UVICORN = importlib.util.find_spec("uvicorn") is not None
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (_HAS_PLAYWRIGHT and _HAS_UVICORN),
        reason="需要 uvicorn + playwright + Chromium",
    ),
]

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "src" / "backend-fastapi"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(
    app_import: str,
    port: int,
    db_path: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen:
    env = os.environ.copy()
    env.update({
        "AGENTBOARD_DB_URL": f"sqlite:///{db_path}",
        "AGENTBOARD_MCP_BACKEND": "db",
        "AGENTBOARD_ALLOW_REGISTRATION": "1",
        "PYTHONPATH": str(BACKEND) + os.pathsep + env.get("PYTHONPATH", ""),
    })
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app_import,
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for(url: str, timeout: float = 30.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"服务在 {url} 启动超时")


def _mock_signalr_negotiate(route) -> None:
    """Keep the unrelated global proposal client quiet in the FastAPI-only fixture."""
    route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({
            "negotiateVersion": 1,
            "connectionId": "quick-navigation-test",
            "connectionToken": "quick-navigation-test",
            "availableTransports": [
                {"transport": "WebSockets", "transferFormats": ["Text", "Binary"]},
            ],
        }),
    )


def _mock_signalr_websocket(websocket) -> None:
    """Complete the SignalR JSON handshake and acknowledge void invocations."""
    def handle_message(message) -> None:
        if not isinstance(message, str):
            return
        for frame in message.split("\x1e"):
            if not frame:
                continue
            payload = json.loads(frame)
            if payload.get("protocol") == "json":
                websocket.send("{}\x1e")
            elif payload.get("type") == 1 and payload.get("invocationId"):
                websocket.send(json.dumps({
                    "type": 3,
                    "invocationId": payload["invocationId"],
                }) + "\x1e")

    websocket.on_message(handle_message)


@pytest.fixture(scope="module")
def servers(tmp_path_factory: pytest.TempPathFactory):
    db_path = tmp_path_factory.mktemp("document-quick-navigation") / "agentboard.db"
    api_port = _free_port()
    web_port = _free_port()
    api = _start_server("agentboard.api:app", api_port, db_path)
    web = _start_server(
        "agentboard.web_app:app",
        web_port,
        db_path,
        {"AGENTBOARD_WEB_API_URL": f"http://127.0.0.1:{api_port}"},
    )
    api_base = f"http://127.0.0.1:{api_port}"
    web_base = f"http://127.0.0.1:{web_port}"
    try:
        _wait_for(api_base + "/api/meta")
        _wait_for(web_base + "/")
        yield api_base, web_base
    finally:
        for process in (web, api):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    try:
        playwright = sync_playwright().start()
        chromium = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
    except Exception as exc:
        pytest.skip(f"Chromium 不可用: {exc}")
    try:
        yield chromium
    finally:
        chromium.close()
        playwright.stop()


@pytest.fixture
def page(browser, tmp_path: Path):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    current_page = context.new_page()
    # The app starts a proposal SignalR client after authentication even on a
    # document route. Mock that unrelated transport so page-error assertions
    # cover the feature under test, and serve the remote font import locally.
    current_page.route("**/hubs/proposals/negotiate*", _mock_signalr_negotiate)
    current_page.route_web_socket("**/hubs/proposals*", _mock_signalr_websocket)
    current_page.route(
        "https://fonts.googleapis.com/**",
        lambda route: route.fulfill(status=200, content_type="text/css", body=""),
    )
    current_page._quick_nav_errors = []  # type: ignore[attr-defined]
    current_page.on(
        "console",
        lambda message: current_page._quick_nav_errors.append(("console", message.text))  # type: ignore[attr-defined]
        if message.type == "error" else None,
    )
    current_page.on(
        "pageerror",
        lambda error: current_page._quick_nav_errors.append(("pageerror", str(error)))  # type: ignore[attr-defined]
    )
    current_page._quick_nav_screenshot = tmp_path / "document-quick-navigation.png"  # type: ignore[attr-defined]
    try:
        yield current_page
    finally:
        current_page.close()
        context.close()


def _api_call(base: str, method: str, path: str, token: str | None = None, body=None):
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        payload = error.read().decode("utf-8") or "{}"
        return error.code, json.loads(payload)


def _assert_nav_is_pinned(page) -> None:
    """滚到最底部且不点击任何按钮时，快速定位条必须仍贴在视口顶部。

    这是 sticky 的可证伪判据：若 ``position: sticky`` 失效（祖先裁切、滚动根不对），
    nav 会随文档流滚出视口，nav.top 将远小于 0。
    """
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_function(
        """
        () => {
          const nav = document.querySelector('.doc-quick-nav')?.getBoundingClientRect();
          return !!nav && nav.top >= 0 && nav.top <= 60 && nav.bottom <= innerHeight;
        }
        """,
        timeout=3000,
    )
    geometry = page.evaluate(
        """
        () => {
          const nav = document.querySelector('.doc-quick-nav')?.getBoundingClientRect();
          return {
            ok: !!nav && nav.top >= 0 && nav.top <= 60 && nav.bottom <= innerHeight,
            nav: nav && { top: nav.top, bottom: nav.bottom },
            scrollY,
            innerHeight,
          };
        }
        """
    )
    assert geometry["ok"], geometry


def _assert_target_is_below_sticky_nav(page, target_id: str) -> None:
    page.wait_for_function(
        """
        targetId => {
          const nav = document.querySelector('.doc-quick-nav')?.getBoundingClientRect();
          const target = document.getElementById(targetId)?.getBoundingClientRect();
          return !!nav && !!target && target.top >= nav.bottom - 4 && target.top < innerHeight;
        }
        """,
        arg=target_id,
        timeout=3000,
    )
    geometry = page.evaluate(
        """
        targetId => {
          const navElement = document.querySelector('.doc-quick-nav');
          const targetElement = document.getElementById(targetId);
          const nav = navElement?.getBoundingClientRect();
          const target = targetElement?.getBoundingClientRect();
          return {
            ok: !!nav && !!target && target.top >= nav.bottom - 4 && target.top < innerHeight,
            nav: nav && { top: nav.top, bottom: nav.bottom },
            target: target && { top: target.top, bottom: target.bottom },
            scrollY,
            scrollHeight: document.documentElement.scrollHeight,
            clientHeight: document.documentElement.clientHeight,
          };
        }
        """,
        target_id,
    )
    assert geometry["ok"], geometry


def test_document_detail_quick_navigation(page, servers):
    """验证长正文、有/无评论、键盘、窄屏、主题、动效和无额外 API 请求。"""
    import httpx

    api_base, web_base = servers
    suffix = str(time.time_ns())
    username = f"quicknav{suffix[-10:]}"
    password = "quicknav-secret"

    response = httpx.post(
        api_base + "/api/auth/register",
        json={"username": username, "password": password},
        timeout=10,
    )
    assert response.status_code == 201, response.text
    token = response.json()["token"]
    auth = {"Authorization": f"Bearer {token}"}

    response = httpx.post(
        api_base + "/api/projects",
        json={"name": f"Quick navigation {suffix}", "key": f"QN{suffix[-6:]}"},
        headers=auth,
        timeout=10,
    )
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]

    long_content = "# 正文起点\n\n" + "\n\n".join(
        f"第 {index} 段长文档内容，用于验证滚动定位。" for index in range(1, 121)
    )
    status, with_comments = _api_call(
        api_base,
        "POST",
        "/api/documents",
        token,
        {
            "project_id": project_id,
            "title": "有评论的长文档",
            "content": long_content,
            "type": "plan",
            "status": "draft",
        },
    )
    assert status == 201, with_comments
    with_comments_id = with_comments["id"]
    status, comment = _api_call(
        api_base,
        "POST",
        f"/api/documents/{with_comments_id}/comments",
        token,
        {"author": username, "content": "快速定位评论"},
    )
    assert status == 201, comment

    status, empty_document = _api_call(
        api_base,
        "POST",
        "/api/documents",
        token,
        {
            "project_id": project_id,
            "title": "空正文无评论文档",
            "content": "",
            "type": "plan",
            "status": "draft",
        },
    )
    assert status == 201, empty_document
    empty_document_id = empty_document["id"]

    api_requests: list[str] = []
    page.on("request", lambda request: api_requests.append(request.url) if "/api/" in request.url else None)
    page.add_init_script(
        f"localStorage.setItem('agentboard_token', {json.dumps(token)});"
        f"localStorage.setItem('agentboard_user', {json.dumps(username)});"
    )
    page.goto(f"{web_base}/documents/{with_comments_id}", wait_until="networkidle")
    page.wait_for_selector('[data-testid="document-quick-navigation"]', state="visible", timeout=15000)

    assert page.get_by_role("button", name="正文开头").count() == 1
    assert page.get_by_role("button", name="评论开头").count() == 1
    assert page.locator("#document-content-start").count() == 1
    assert page.locator("#document-comments-start").count() == 1
    baseline_request_count = len(api_requests)

    _assert_nav_is_pinned(page)
    page.get_by_role("button", name="正文开头").click()
    page.wait_for_timeout(350)
    _assert_target_is_below_sticky_nav(page, "document-content-start")

    page.get_by_role("button", name="评论开头").click()
    page.wait_for_timeout(350)
    _assert_target_is_below_sticky_nav(page, "document-comments-start")
    assert len(api_requests) == baseline_request_count

    # 原生按钮的键盘激活保持焦点，Enter / Space 与鼠标点击使用同一滚动处理器。
    content_button = page.get_by_role("button", name="正文开头")
    content_button.focus()
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement?.id") == "document-jump-to-content"
    comments_button = page.get_by_role("button", name="评论开头")
    comments_button.focus()
    page.keyboard.press("Space")
    assert page.evaluate("document.activeElement?.id") == "document-jump-to-comments"

    # 直接观察调用参数，确保 reduced motion 不被浏览器宿主的平滑滚动覆盖。
    page.emulate_media(reduced_motion="reduce")
    page.evaluate(
        """
        () => {
          const target = document.querySelector('#document-content-start');
          window.__quickNavScrollOptions = null;
          target.scrollIntoView = (options) => { window.__quickNavScrollOptions = options; };
        }
        """
    )
    content_button.click()
    assert page.evaluate("window.__quickNavScrollOptions?.behavior") == "auto"

    page.emulate_media(reduced_motion="no-preference")
    page.evaluate(
        """
        () => {
          const target = document.querySelector('#document-comments-start');
          window.__quickNavScrollOptions = null;
          target.scrollIntoView = (options) => { window.__quickNavScrollOptions = options; };
        }
        """
    )
    comments_button.click()
    assert page.evaluate("window.__quickNavScrollOptions?.behavior") == "smooth"

    # 窄屏、深色主题下入口仍在视口内，焦点环由全局/局部 focus-visible 规则提供。
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
    boxes = page.locator(".doc-quick-nav, .doc-quick-nav button").evaluate_all(
        "nodes => nodes.map(node => { const r = node.getBoundingClientRect(); return [r.left, r.right, r.width]; })"
    )
    viewport_width = page.evaluate("document.documentElement.clientWidth")
    assert all(left >= 0 and right <= viewport_width + 1 and width >= 44 for left, right, width in boxes)

    # 空正文/无评论仍输出两个目标、空态和输入表单，且点击不增加详情/评论请求。
    page.goto(f"{web_base}/documents/{empty_document_id}", wait_until="networkidle")
    page.wait_for_selector('[data-testid="document-quick-navigation"]', state="visible", timeout=15000)
    assert page.locator("#document-content-start").count() == 1
    assert page.locator("#document-comments-start").count() == 1
    assert page.locator(".comments-card .empty-inline").count() == 1
    assert page.locator("form.comment-form textarea").is_visible()
    empty_baseline = len(api_requests)
    page.get_by_role("button", name="评论开头").click()
    page.wait_for_timeout(350)
    assert len(api_requests) == empty_baseline

    page.screenshot(path=str(page._quick_nav_screenshot))  # type: ignore[attr-defined]
    assert not page._quick_nav_errors, "\\n".join(  # type: ignore[attr-defined]
        f"{kind}: {message}" for kind, message in page._quick_nav_errors  # type: ignore[attr-defined]
    )
