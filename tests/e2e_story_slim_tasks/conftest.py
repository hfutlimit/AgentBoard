"""Story 简洁任务列表 v7.3 E2E 共享 fixtures（2026-08-21）。

复刻 e2e_workspace_tabs/conftest 的 helper，另加 API 测试数据构造：
- admin_token：session scope 复用，避免每次 test 都登录
- goto_url_with_token：localStorage 注入 token + 跳转
- api_create_epic/story/task + api_set_task_status：构造确定性任务状态分布
  （v7.3 零计数 chip 隐藏断言需要已知 statusCounts）
- cleanup：删除临时 epic 级联清理 stories/tasks

注意：dev 前端跑在 28080（4200 被 KnowledgeVault 占用），跑本套件必须设
AGENTBOARD_E2E_BASE=http://127.0.0.1:28080。
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

PROJECT_ID = 1  # dev 演示项目

ROOT = Path(__file__).resolve().parent
SHOT_DIR = ROOT / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    safe = msg.encode("ascii", "replace").decode("ascii")
    print(safe, flush=True)


def api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    """带鉴权调 AgentBoard API；返回解析后的 JSON。"""
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


@pytest.fixture(scope="session")
def admin_token() -> str:
    req = urllib.request.Request(
        f"{API_BASE}/api/auth/login",
        data=json.dumps({"username": ADMIN_USER, "password": ADMIN_PASS}).encode(),
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


# ============================================================
# 测试数据构造（API）：epic → story(快速流) → tasks(确定性状态分布)
# ============================================================

@pytest.fixture()
def slim_story(admin_token):
    """建一个临时 epic + story + 3 个 task，返回 story id。

    任务状态分布（零计数 chip 断言的确定性前提）：
    - v73-已完成   → done（todo→done，reason=completed），带 assignee=admin(1)
    - v73-进行中   → in_progress，带 due_date
    - v73-待办     → todo 原样，无 assignee 无 due（行内降噪断言样本）

    注意：建 story 会触发自动编排，额外生成「设计：/实现：」2 个子任务（todo 态）。
    因此 fixture 从 API 读回真实任务列表，附带 expected_total/expected_done
    供测试断言（页面显示 = API 事实，不硬编码计数）。

    测试结束后删除 epic（级联清理 story/tasks）。
    """
    ts = int(time.time())
    token = admin_token
    epic = api("POST", f"/api/projects/{PROJECT_ID}/epics", token,
               {"title": f"v73-e2e-{ts}", "description": "Story 任务列表简化 v7.3 e2e 临时数据"})
    story = api("POST", f"/api/epics/{epic['id']}/stories", token,
                {"title": f"v73 简洁任务列表 e2e {ts}", "description": "e2e", "needs_design": False})
    t_done = api("POST", f"/api/stories/{story['id']}/tasks", token,
                 {"project_id": PROJECT_ID, "title": "v73-已完成", "priority": "high",
                  "assignee_id": 1})
    t_prog = api("POST", f"/api/stories/{story['id']}/tasks", token,
                 {"project_id": PROJECT_ID, "title": "v73-进行中", "priority": "medium",
                  "due_date": "2030-01-15"})
    t_todo = api("POST", f"/api/stories/{story['id']}/tasks", token,
                 {"project_id": PROJECT_ID, "title": "v73-待办", "priority": "low"})
    api("PUT", f"/api/tasks/{t_done['id']}/status", token,
        {"status": "done", "status_reason": "completed"})
    api("PUT", f"/api/tasks/{t_prog['id']}/status", token,
        {"status": "in_progress"})
    # 读回真实分布（含自动编排生成的子任务），供测试动态断言
    listing = api("GET", f"/api/stories/{story['id']}/tasks?limit=100", token)
    items = listing.get("items", listing if isinstance(listing, list) else [])
    yield {"epic_id": epic["id"], "story_id": story["id"],
           "t_done": t_done["id"], "t_prog": t_prog["id"], "t_todo": t_todo["id"],
           "expected_total": len(items),
           "expected_done": sum(1 for t in items if t.get("status") == "done")}
    try:
        api("DELETE", f"/api/epics/{epic['id']}", token)
    except urllib.error.HTTPError:
        pass  # 清理失败不影响断言（临时数据，无害）
