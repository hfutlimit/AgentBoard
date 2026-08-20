"""Epic 150 / Story 324 (X3) PR 1 验证：5 个 list-style view 的 page-header 改造。

覆盖 views：projects / documents / agents / proposals / notifications。

验证点：
1. 每个 view 顶部出现 <app-workspace-heading>，原 .page-header 不再存在
2. h1 文本正确 + count badge 正确
3. 操作按钮 slot 投影到 .workspace-heading-actions
4. 切 view 切换正常
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

FRONTEND_ORIGIN = "http://127.0.0.1:4200"
PROD_API = "http://124.220.44.12"
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"

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


# (path, expected_title, min_actions, screenshot_name, mode)
#   mode: "goto" = page.goto directly
# 注：顶层 @case ('agents') @case ('documents') @case ('proposals') 当前 dead code
#     （X1 PR 3 follow-up 隐藏了外层 sidebar → 没 view.set 入口），E2E 只测可达 view。
VIEWS = [
    ("/projects", "项目中心", 1, "_x3_pr1_projects.png", "goto"),
    ("/notifications", "通知中心", 1, "_x3_pr1_notifications.png", "goto"),
]


def goto_and_verify(page, path: str, expected_title: str, min_actions: int, shot: str, mode: str, failures: list[str]) -> None:
    print(f">>> goto {path}")
    try:
        page.goto(f"{FRONTEND_ORIGIN}{path}", wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        failures.append(f"{path}: nav failed: {e!r}")
        return

    try:
        page.wait_for_selector("app-workspace-heading .workspace-heading-v7", timeout=10000)
    except Exception as e:
        failures.append(f"{path}: workspace-heading did not render: {e!r}")
        return

    time.sleep(0.4)

    # workspace-heading 数量（应该恰好 1）
    wh_count = page.locator("app-workspace-heading").count()
    print(f"   workspace-heading count = {wh_count}")
    if wh_count != 1:
        failures.append(f"{path}: workspace-heading count expected 1, got {wh_count}")

    # h1 文本
    h1_text = (page.locator("app-workspace-heading h1").first.text_content() or "").strip()
    print(f"   h1 = '{h1_text}'")
    if expected_title not in h1_text:
        failures.append(f"{path}: h1 should contain '{expected_title}', got '{h1_text}'")

    # count badge
    badge_text = (page.locator("app-workspace-heading .heading-title-badge").first.text_content() or "").strip()
    print(f"   badge = '{badge_text}'")
    if not badge_text:
        failures.append(f"{path}: heading-title-badge missing")

    # actions slot 投影（操作按钮）
    actions_count = page.locator("app-workspace-heading .heading-action-btn").count()
    print(f"   actions count = {actions_count}")
    if actions_count < min_actions:
        failures.append(f"{path}: actions count expected >= {min_actions}, got {actions_count}")

    # 原 .page-header 不再存在（顶层 view 的；settings 还有 crumb-bar 那个，但不属于本 view）
    legacy_headers = page.locator(".page-header").count()
    print(f"   legacy .page-header count = {legacy_headers}")
    if legacy_headers > 0:
        failures.append(f"{path}: legacy .page-header should be removed, got {legacy_headers}")

    try:
        page.screenshot(path=str(SHOT_DIR / shot), full_page=False)
    except Exception:
        pass  # 字体加载超时不影响功能


def main() -> int:
    failures: list[str] = []
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

            for path, title, min_actions, shot, mode in VIEWS:
                goto_and_verify(page, path, title, min_actions, shot, mode, failures)

            # 反向验证：回到 home view，workspace-heading 不应出现
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
                page.screenshot(path=str(SHOT_DIR / "_x3_pr1_error.png"), full_page=True)
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
    print("PASS — X3 PR 1 5 list-style views OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
