"""Workspace tabs E2E 共享 fixtures (2026-08-21)。

复刻 e2e_epic149/conftest 的关键 helper，单独成目录便于隔离运行：
- admin_token：session scope 复用，避免每次 test 都登录
- goto_url_with_token：localStorage 注入 token + 跳转
- screenshot helper

前端 :4200 + 后端 :18000 仍是 AgentBoard dev 标准端口，conftest 默认值
与 e2e_epic149/conftest.py 一致。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

FRONTEND_ORIGIN = os.environ.get("AGENTBOARD_E2E_BASE", "http://127.0.0.1:4200")
API_BASE = os.environ.get("AGENTBOARD_API_BASE", "http://127.0.0.1:18000")
ADMIN_USER = os.environ.get("AGENTBOARD_E2E_USER", "admin")
ADMIN_PASS = os.environ.get("AGENTBOARD_E2E_PASS", "admin123")

ROOT = Path(__file__).resolve().parent
SHOT_DIR = ROOT / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    safe = msg.encode("ascii", "replace").decode("ascii")
    print(safe, flush=True)


@pytest.fixture(scope="session")
def admin_token() -> str:
    user, password = ADMIN_USER, ADMIN_PASS
    req = urllib.request.Request(
        f"{API_BASE}/api/auth/login",
        data=json.dumps({"username": user, "password": password}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())["token"]
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        pytest.skip("live E2E credentials/API unavailable")


@pytest.fixture(scope="session")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    sync_playwright = playwright.sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        yield page
    finally:
        ctx.close()


def goto_url_with_token(page, token: str, url: str) -> None:
    """登录态注入：先 /，写 localStorage，reload，再 goto 目标。"""
    page.goto(f"{FRONTEND_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)
    page.evaluate(f"localStorage.setItem('agentboard_token', {json.dumps(token)})")
    page.evaluate("localStorage.setItem('agentboard_user', 'admin')")
    page.reload(wait_until="domcontentloaded", timeout=30000)
    time.sleep(2.0)
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(2.0)
