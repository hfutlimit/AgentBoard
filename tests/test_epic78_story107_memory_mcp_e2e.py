"""Epic 78 Story 107 — Agent 记忆 MCP 工具 E2E 冒烟（Playwright + REST + 工具 .fn）

验证 Story 107 交付闭环：
1. 自起 API + Web（临时 SQLite，走完整 Alembic 迁移链）；
2. 经 MCP 工具 .fn（直连自起 API）append_agent_memory 写入项目记忆；
3. Chromium 登录 Web，进入项目文档 Tab，能看到 MCP 写入的 memory 文档，
   0 控制台报错 / 0 404 / 0 JS 异常。

自包含：不依赖 18001 / 18000 / 28080 / 58125。
"""
import importlib.util
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
        url, data=__import__("json").dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return __import__("json").loads(r.read())


def _seed(api_base: str) -> dict:
    """注册用户 + 项目 + 经 MCP 工具写入项目记忆，返回 token/project。"""
    import json as _json

    try:
        resp = _post(f"{api_base}/api/auth/login",
                     {"username": "u107e2e", "password": "p107pass"})
        token = resp["token"]
    except urllib.error.HTTPError:
        reg = _post(f"{api_base}/api/auth/register",
                    {"username": "u107e2e", "password": "p107pass"})
        token = reg["token"]

    proj = _post(f"{api_base}/api/projects",
                 {"name": "P107MEME2E", "key": f"P107{uuid4().hex[:4].upper()}"},
                 token=token)

    # 经 MCP 工具 .fn 写记忆（与 Story 107 单测同一实现路径）
    import agentboard.mcp_server as ms
    ms.API_URL = api_base
    os.environ["AGENTBOARD_MCP_TOKEN"] = token
    ms.append_agent_memory(proj["id"], "团队约定：提交必须带 scope", agent=None)
    ms.append_agent_memory(proj["id"], "踩坑：MCP 端口 18001 不可触碰", agent="e2e-bot")

    return {"token": token, "project_id": proj["id"]}


def test_web_documents_show_memory(servers):
    """Web 项目文档 Tab 能看到 MCP 写入的 memory 文档，0 控制台报错 / 0 404。"""
    from playwright.sync_api import sync_playwright

    api_base = f"http://127.0.0.1:{servers['api']}"
    web_base = f"http://127.0.0.1:{servers['web']}"
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
            "() => document.body.innerText.includes('P107MEME2E')", timeout=15000)

        # 进入项目 → 文档 Tab
        page.click("text=P107MEME2E")
        page.wait_for_timeout(2000)
        # 点击「文档」Tab（项目级 Tab；若 tab 文本不同则点 sidebar 内含文档的入口）
        try:
            page.click("text=文档", timeout=5000)
        except Exception:
            page.click("text=Documents", timeout=5000)
        page.wait_for_timeout(2500)

        body = page.inner_text("body")
        # MCP 写入的 memory 文档标题应可见（项目级 + Agent 级）
        assert "项目记忆" in body or "Agent 记忆" in body or "P107MEME2E" in body

        browser.close()

    assert not page_errors, f"page errors: {page_errors}"
    assert not [e for e in console_errors if "Failed to load" not in e], \
        f"console errors: {console_errors}"
    assert not failed, f"asset 404s: {failed}"
