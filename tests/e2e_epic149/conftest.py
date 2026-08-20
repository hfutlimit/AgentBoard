"""Epic 151 / Story 330 / Task 1323：E2E 共享 pytest fixtures。

设计原则（2026-08-20）：
- 每个 test 函数拿独立 Page（避免 Playwright sync API 多线程 hang）
- token / base_url 用 env var 默认值，conftest 不直接调 login
- 浏览器 launch 一次（session scope），context + page 每次新建
- 显式提供 e2e 标记的 fixture，pytest -m e2e 自动收集
"""
from __future__ import annotations

import os
import urllib.request
import json
import sys
import time
from pathlib import Path

import pytest

# Windows console 默认 GBK → emoji 输出炸；统一 reconfigure UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# ----- 常量（供 test_*.py 复用）-----

FRONTEND_ORIGIN = os.environ.get("AGENTBOARD_E2E_BASE", "http://127.0.0.1:4200")
API_BASE = os.environ.get("AGENTBOARD_API_BASE", "http://127.0.0.1:18000")
ADMIN_USER = os.environ.get("AGENTBOARD_E2E_USER", "admin")
ADMIN_PASS = os.environ.get("AGENTBOARD_E2E_PASS", "admin123")

ROOT = Path(__file__).resolve().parent
SHOT_DIR = ROOT / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


# ----- Pytest fixtures -----

@pytest.fixture(scope="session")
def api_base() -> str:
    """dev API base URL。"""
    return API_BASE


@pytest.fixture(scope="session")
def frontend_origin() -> str:
    """Angular dev server origin。"""
    return FRONTEND_ORIGIN


@pytest.fixture(scope="session")
def admin_creds() -> tuple[str, str]:
    """(user, pass) 元组。"""
    return (ADMIN_USER, ADMIN_PASS)


@pytest.fixture(scope="session")
def admin_token(admin_creds) -> str:
    """经 dev API 拿 admin token（session scope 复用）。"""
    user, password = admin_creds
    req = urllib.request.Request(
        f"{API_BASE}/api/auth/login",
        data=json.dumps({"username": user, "password": password}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["token"]


@pytest.fixture(scope="session")
def browser():
    """Playwright chromium 浏览器（session scope 一次启动）。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def page(browser):
    """每个 test 拿独立 context + page（避免 session 状态污染）。"""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        yield page
    finally:
        ctx.close()


# ----- 兼容旧 main() 入口的 helper -----

def log(msg: str, *, flush: bool = True) -> None:
    """Safe print: 替换非 ASCII 字符为 ? 避免 Windows GBK 控制台崩溃。"""
    safe = msg.encode("ascii", "replace").decode("ascii")
    print(safe, flush=flush)


def goto_url_with_token(page, token: str, url: str, first: bool = False) -> None:
    """与原 test_*.py 共享的 token 注入 + reload helper。

    2-step 模式（first=True）：先 / 完成 auth 状态，再切 target URL。
    1-step 模式（first=False）：直接 reload target URL（依赖 Task 1310d 修复）。
    """
    if first:
        page.goto(f"{FRONTEND_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)
        page.evaluate(f"localStorage.setItem('agentboard_token', {json.dumps(token)})")
        page.evaluate("localStorage.setItem('agentboard_user', 'admin')")
        page.reload(wait_until="domcontentloaded", timeout=30000)
        time.sleep(2.0)
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.reload(wait_until="domcontentloaded", timeout=30000)
    time.sleep(3.0)
