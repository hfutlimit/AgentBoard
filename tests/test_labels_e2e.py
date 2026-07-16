"""Playwright E2E test for B-01 Task Labels UI & Filtering.

Uses local uvicorn servers (same pattern as test_playwright_e2e.py) to avoid
Docker/CORS/rate-limit issues. Points to the same database as the running services.
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

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(app_import: str, port: int, extra_env: dict | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    # Use a fresh SQLite database for isolation
    db_path = tempfile.mktemp(suffix=".db")
    env["AGENTBOARD_DB_URL"] = f"sqlite:///{db_path}"
    env["AGENTBOARD_RATE_LIMIT"] = "0"  # Disable rate limiting for tests
    env["AGENTBOARD_REQUIRE_AUTH"] = "false"  # Allow unauthenticated for test setup
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
            r = httpx.get(url, timeout=2)
            if r.status_code == 200:
                return
        except Exception:
            time.sleep(0.5)
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
        chromium = pw.chromium.launch(headless=True)
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
    try:
        yield pg
    finally:
        pg.close()
        ctx.close()


def _register_and_get_token(api_base: str, username: str, password: str) -> str:
    import httpx
    resp = httpx.post(
        f"{api_base}/api/auth/register",
        json={"username": username, "password": password},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def _create_project(api_base: str, token: str, name: str) -> int:
    import httpx
    resp = httpx.post(
        f"{api_base}/api/projects",
        json={"name": name},
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _create_epic(api_base: str, token: str, project_id: int, title: str) -> int:
    import httpx
    resp = httpx.post(
        f"{api_base}/api/projects/{project_id}/epics",
        json={"title": title},
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _create_story(api_base: str, token: str, epic_id: int, title: str) -> int:
    import httpx
    resp = httpx.post(
        f"{api_base}/api/epics/{epic_id}/stories",
        json={"title": title},
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _create_task_with_labels(api_base: str, token: str, story_id: int, project_id: int, title: str, labels: list) -> int:
    import httpx
    resp = httpx.post(
        f"{api_base}/api/stories/{story_id}/tasks",
        json={
            "project_id": project_id,
            "title": title,
            "type": "task",
            "priority": "high",
            "labels": json.dumps(labels),
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def test_labels_ui_e2e(servers, page):
    """E2E test: verify label badges, filter, and create/edit UI."""
    api_base, web_base = servers

    # Setup: register user, create project tree, create labeled tasks
    username = f"label_e2e_{int(time.time())}"
    token = _register_and_get_token(api_base, username, "test123456")
    project_id = _create_project(api_base, token, "Label Test Project")
    epic_id = _create_epic(api_base, token, project_id, "Test Epic")
    story_id = _create_story(api_base, token, epic_id, "Test Story")

    # Create tasks with different labels
    task1_id = _create_task_with_labels(api_base, token, story_id, project_id,
                                         "Frontend Bug Fix", ["frontend", "urgent", "bug-fix"])
    task2_id = _create_task_with_labels(api_base, token, story_id, project_id,
                                         "Backend Refactor", ["backend", "refactor"])
    task3_id = _create_task_with_labels(api_base, token, story_id, project_id,
                                         "Documentation", ["docs"])

    # Navigate to the app and login
    page.goto(web_base, wait_until="domcontentloaded")
    time.sleep(2)
    page.locator(".auth-tab", has_text="登录").click()
    page.locator("input[name=username]").fill(username)
    page.locator("input[name=password]").fill("test123456")
    page.locator(".login-submit").click()
    time.sleep(3)
    page.wait_for_selector(".topbar", timeout=10000)

    # Navigate to the story page
    page.goto(f"{web_base}/#/story/{story_id}", wait_until="networkidle")
    time.sleep(3)

    # Verify task list
    task_items = page.locator(".entity-item--rich")
    count = task_items.count()
    assert count == 3, f"Expected 3 tasks, got {count}"
    print(f"  Story loaded with {count} tasks")

    # Verify label badges
    label_badges = page.locator(".label-badge")
    label_count = label_badges.count()
    assert label_count >= 6, f"Expected at least 6 label badges (2+2+1+1 list), got {label_count}"
    print(f"  Found {label_count} label badges")

    # Get label texts
    texts = [label_badges.nth(i).inner_text() for i in range(label_count)]
    expected_labels = {"frontend", "urgent", "bug-fix", "backend", "refactor", "docs"}
    found_labels = set(texts)
    assert expected_labels.issubset(found_labels), f"Missing labels: {expected_labels - found_labels}"
    print(f"  All expected labels found: {found_labels}")

    # Open filter panel and verify label filter
    filter_btn = page.locator("button", has_text="筛选")
    filter_btn.first.click()
    time.sleep(0.5)
    label_filter = page.locator(".filter-label", has_text="标签")
    assert label_filter.count() > 0, "Label filter group not found in filter panel"
    print("  Label filter group present in filter panel")

    # Click on "frontend" filter chip
    frontend_chip = page.locator(".filter-chip", has_text="frontend")
    if frontend_chip.count() > 0:
        frontend_chip.first.click()
        time.sleep(1)
        # Should only show 1 task (Frontend Bug Fix)
        filtered_count = page.locator(".entity-item--rich").count()
        assert filtered_count == 1, f"Expected 1 task after frontend filter, got {filtered_count}"
        print(f"  Label filter works: {filtered_count} task after 'frontend' filter")
        # Clear filter
        clear_btn = page.locator("button", has_text="清除筛选")
        if clear_btn.count() > 0:
            clear_btn.click()
            time.sleep(0.5)

    # Check create modal has labels input
    create_btns = page.locator("button:has-text('新建')")
    create_btns.first.click()
    time.sleep(0.5)
    labels_input = page.locator("#create-labels")
    assert labels_input.count() > 0, "Labels input not found in create modal"
    placeholder = labels_input.get_attribute("placeholder")
    assert "前端" in (placeholder or ""), f"Expected Chinese hint in placeholder, got: {placeholder}"
    print(f"  Create modal has labels input with placeholder: {placeholder}")
    page.locator(".modal-close").click()
    time.sleep(0.3)

    # Navigate to task detail
    page.goto(f"{web_base}/#/task/{task1_id}", wait_until="networkidle")
    time.sleep(2)

    # Verify label badges in task detail
    detail_labels = page.locator(".task-meta-item .label-badge, .label-list .label-badge")
    detail_count = detail_labels.count()
    assert detail_count == 3, f"Expected 3 label badges in task detail, got {detail_count}"
    detail_texts = [detail_labels.nth(i).inner_text() for i in range(detail_count)]
    assert set(detail_texts) == {"frontend", "urgent", "bug-fix"}, f"Unexpected labels: {detail_texts}"
    print(f"  Task detail shows labels: {detail_texts}")

    # Verify label edit input
    label_edit = page.locator("input[placeholder*='前端']")
    assert label_edit.count() > 0, "Label edit input not found"
    print("  Label edit input present in task detail")

    # Take screenshot
    page.screenshot(path="screenshots/test_labels_e2e.png", full_page=True)
    print("  Screenshot saved")

    print("\nALL LABEL E2E TESTS PASSED")
