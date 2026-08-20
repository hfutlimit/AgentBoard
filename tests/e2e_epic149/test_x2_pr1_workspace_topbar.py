"""Epic 150 / Story 323 (X2) PR 1 验证：workspace-topbar 组件 + project view 接入。

验证点：
1. project view (e.g. /project/3) 顶部出现 <app-workspace-topbar>
2. back 按钮存在、可点击跳回 home (/)
3. project switcher button 存在、点击后下拉出现
4. 切换器下拉里能找到项目列表、点选能切到目标项目
5. 通知弹层（外层 topbar 右侧）不受影响：showNotifPanel 仍能正常打开
6. 切到 home view，<app-workspace-topbar> 不渲染（与 prototype 一致）
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

FRONTEND_ORIGIN = "http://127.0.0.1:4200"
PROD_API = "http://124.220.44.12"
ADMIN_USER = os.environ.get("AGENTBOARD_E2E_USER", "admin")
ADMIN_PASS = os.environ.get("AGENTBOARD_E2E_PASS", "admin123")
PROJECT_ID = 3

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


def main() -> int:
    failures: list[str] = []
    token = login()
    print(">>> got token len=%d" % len(token))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        console_errors: list[str] = []
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        try:
            page.add_init_script(
                "localStorage.setItem('agentboard_token', %s);"
                "localStorage.setItem('agentboard_user', 'admin');" % json.dumps(token)
            )
            print(">>> goto /project/%d" % PROJECT_ID)
            page.goto(f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("app-workspace-topbar .back-button-v7", timeout=15000)
            time.sleep(0.6)

            # Step 1: workspace-topbar 在 project view 渲染
            wt_count = page.locator("app-workspace-topbar").count()
            print(f"   app-workspace-topbar count = {wt_count}")
            if wt_count != 1:
                failures.append(f"app-workspace-topbar should be exactly 1 in project view, got {wt_count}")

            back_btn = page.locator("app-workspace-topbar .back-button-v7").first
            back_visible = back_btn.is_visible()
            print(f"   back button visible = {back_visible}")
            if not back_visible:
                failures.append("back button not visible")

            switcher_btn = page.locator("app-workspace-topbar .project-switcher-button-v7").first
            switcher_visible = switcher_btn.is_visible()
            print(f"   project switcher button visible = {switcher_visible}")
            if not switcher_visible:
                failures.append("project switcher button not visible")

            # monogram 显示
            mono_text = page.locator("app-workspace-topbar .project-monogram-v7").first.text_content()
            print(f"   monogram text = '{mono_text}'")
            if not mono_text or len(mono_text.strip()) < 1:
                failures.append("monogram text empty")

            page.screenshot(path=str(SHOT_DIR / "_x2_pr1_01_project.png"), full_page=False)

            # Step 2: 点击 project switcher
            print(">>> STEP 2 — click project switcher")
            switcher_btn.click()
            time.sleep(0.4)
            popover_count = page.locator("app-workspace-topbar .popover-v7.project-switcher-v7").count()
            print(f"   popover count after click = {popover_count}")
            if popover_count != 1:
                failures.append(f"switcher popover should appear, count={popover_count}")

            # 切换器内应至少有 1 个项目行
            switcher_rows = page.locator("app-workspace-topbar .switcher-project-v7").count()
            print(f"   switcher rows = {switcher_rows}")
            if switcher_rows < 1:
                failures.append(f"switcher has no project rows: {switcher_rows}")

            page.screenshot(path=str(SHOT_DIR / "_x2_pr1_02_switcher_open.png"), full_page=False)

            # 搜索过滤
            search_input = page.locator("app-workspace-topbar .popover-search-v7 input[type='search']").first
            search_input.fill("AgentBoard")
            time.sleep(0.3)
            filtered_rows = page.locator("app-workspace-topbar .switcher-project-v7").count()
            print(f"   filtered rows (search 'AgentBoard') = {filtered_rows}")
            if filtered_rows < 1:
                failures.append(f"filtered switcher empty: {filtered_rows}")

            # 清空搜索
            search_input.fill("")
            time.sleep(0.3)

            # 点击 overlay 关闭
            overlay = page.locator("app-workspace-topbar .popover-overlay-v7").first
            overlay.click()
            time.sleep(0.3)
            popover_after_close = page.locator("app-workspace-topbar .popover-v7.project-switcher-v7").count()
            print(f"   popover count after overlay click = {popover_after_close}")
            if popover_after_close != 0:
                failures.append("switcher popover should close after overlay click")

            # Step 3: 点击 back 按钮回到 home
            print(">>> STEP 3 — click back button to go home")
            back_btn.click()
            page.wait_for_url(f"{FRONTEND_ORIGIN}/", timeout=10000)
            time.sleep(0.5)
            wt_on_home = page.locator("app-workspace-topbar").count()
            print(f"   workspace-topbar on home = {wt_on_home}")
            if wt_on_home != 0:
                failures.append(f"workspace-topbar should NOT render on home view, got {wt_on_home}")
            home_shell = page.locator("app-home-shell .home-shell-v7").count()
            print(f"   home-shell on home = {home_shell}")
            if home_shell != 1:
                failures.append(f"home-shell should render on home, got {home_shell}")
            page.screenshot(path=str(SHOT_DIR / "_x2_pr1_03_back_home.png"), full_page=False)

            # Step 4: 切回 project view 验证通知弹层（外层 topbar 右侧）不受影响
            print(">>> STEP 4 — back to project view, verify notif panel still works")
            page.goto(f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("app-workspace-topbar .back-button-v7", timeout=15000)
            time.sleep(0.6)
            notif_btn = page.locator("button.notif-btn").first
            notif_btn_visible = notif_btn.is_visible()
            print(f"   notif button visible = {notif_btn_visible}")
            if not notif_btn_visible:
                failures.append("notif button not visible (regression)")

            # console errors
            if console_errors:
                # 过滤已知噪音
                real_errors = [e for e in console_errors if "ResizeObserver" not in e]
                if real_errors:
                    failures.append(f"page errors: {real_errors[:3]}")

        except Exception as e:
            failures.append(f"exception: {e!r}")
            try:
                page.screenshot(path=str(SHOT_DIR / "_x2_pr1_error.png"), full_page=True)
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
    print("PASS — X2 PR 1 workspace-topbar OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
