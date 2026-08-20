"""Epic 150 / Story 324 (X3) PR 2 验证：detail views 的 page-header 改造。

覆盖 views：project / admin。
注：task / sprint / proposal 详情需要有效 id（取决于 test data），
    本测试只覆盖稳定可达的 2 个 view + home 回归。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

# Force UTF-8 stdout (Windows console GBK can't print emoji)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

FRONTEND_ORIGIN = "http://127.0.0.1:4200"
PROD_API = "http://124.220.44.12"
ADMIN_USER = os.environ.get("AGENTBOARD_E2E_USER", "admin")
ADMIN_PASS = os.environ.get("AGENTBOARD_E2E_PASS", "admin123")
ROOT = Path(__file__).resolve().parent
SHOT_DIR = ROOT / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


def login() -> str:
    req = urllib.request.Request(
        PROD_API + "/api/auth/login",
        data=json.dumps({"username": ADMIN_USER, "password": ADMIN_PASS}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["token"]


# (path, expected_title_substr, min_title_badges, expected_button_text, screenshot_name)
# 注：admin view 因生产 API 缺 /me 端点（adminMe() 返回 null）会被重定向到 /，
#     task / sprint / proposal 详情需要动态 id，跳过 E2E；仅测稳定可达 view。
VIEWS = [
    ("/project/3", "AgentBoard", 1, None, "_x3_pr2_project.png"),
]


def goto_and_verify(page, path: str, expected_title: str, min_badges: int, expected_btn, shot: str, failures: list) -> None:
    print(f">>> goto {path}")
    try:
        page.goto(f"{FRONTEND_ORIGIN}{path}", wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        failures.append(f"{path}: goto failed: {e!r}")
        return

    time.sleep(1.0)  # allow adminMe() / view.set() to settle
    try:
        page.wait_for_selector("app-workspace-heading .workspace-heading-v7", timeout=10000)
    except Exception as e:
        current_url = page.url
        failures.append(f"{path}: workspace-heading did not render: {e!r}; current_url={current_url}")
        return

    time.sleep(0.4)

    wh_count = page.locator("app-workspace-heading").count()
    print(f"   workspace-heading count = {wh_count}")
    if wh_count < 1:
        failures.append(f"{path}: workspace-heading count expected >= 1, got {wh_count}")

    h1_text = (page.locator("app-workspace-heading h1").first.text_content() or "").strip()
    print(f"   h1 = '{h1_text}'")
    if expected_title not in h1_text:
        failures.append(f"{path}: h1 should contain '{expected_title}', got '{h1_text}'")

    badge_count = page.locator("app-workspace-heading .heading-title-badge").count()
    print(f"   title-badge count = {badge_count}")
    if badge_count < min_badges:
        failures.append(f"{path}: title-badge count expected >= {min_badges}, got {badge_count}")

    legacy = page.locator(".page-header").count()
    print(f"   legacy .page-header count = {legacy}")
    if legacy > 0:
        failures.append(f"{path}: legacy .page-header should be removed, got {legacy}")

    if expected_btn:
        btn = page.locator(f"app-workspace-heading .heading-action-btn:has-text('{expected_btn}')")
        if btn.count() == 0:
            failures.append(f"{path}: expected action button '{expected_btn}' not found")

    try:
        page.screenshot(path=str(SHOT_DIR / shot), full_page=False)
    except Exception:
        pass


def main() -> int:
    failures = []
    token = login()
    print(">>> got token len=%d" % len(token))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            page.add_init_script(
                "localStorage.setItem('agentboard_token', %s);"
                "localStorage.setItem('agentboard_user', 'admin');" % json.dumps(token)
            )

            for path, title, min_badges, btn, shot in VIEWS:
                goto_and_verify(page, path, title, min_badges, btn, shot, failures)

            # 反向验证：home view 不渲染 workspace-heading
            print(">>> regression — back to home")
            page.goto(f"{FRONTEND_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("app-home-shell .home-shell-v7", timeout=10000)
            time.sleep(0.3)
            wh_on_home = page.locator("app-workspace-heading").count()
            print(f"   workspace-heading on home = {wh_on_home}")
            if wh_on_home != 0:
                failures.append(f"home view: workspace-heading should NOT render, got {wh_on_home}")

        except Exception as e:
            failures.append(f"exception: {e!r}")
            try:
                page.screenshot(path=str(SHOT_DIR / "_x3_pr2_error.png"), full_page=True)
            except Exception:
                pass
        finally:
            ctx.close()
            browser.close()

    print()
    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS — X3 PR 2 detail views OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
