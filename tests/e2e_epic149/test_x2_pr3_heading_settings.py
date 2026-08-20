"""Epic 150 / Story 323 (X2) PR 3 验证：workspace-heading 组件 + settings 视图接入。

验证点：
1. settings view 顶部出现 <app-workspace-heading>
2. eyebrow / title / subtitle 文本正确
3. actions slot 没内容时（settings 视图无操作按钮）不报错
4. 原 page-header DOM 不再存在（被替换）
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

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
            # 直接 goto /settings view（loadRoute 会 set view='settings'）
            print(">>> goto /settings")
            page.goto(f"{FRONTEND_ORIGIN}/settings", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("app-workspace-heading .workspace-heading-v7", timeout=15000)
            time.sleep(0.5)

            # Step 1: workspace-heading 存在
            wh_count = page.locator("app-workspace-heading").count()
            print(f"   app-workspace-heading count = {wh_count}")
            if wh_count != 1:
                failures.append(f"workspace-heading should be exactly 1, got {wh_count}")

            # Step 2: 文本正确
            eyebrow_text = page.locator("app-workspace-heading .eyebrow").first.text_content()
            h1_text = page.locator("app-workspace-heading h1").first.text_content()
            subtitle_text = page.locator("app-workspace-heading .muted").first.text_content()
            print(f"   eyebrow = '{eyebrow_text.strip()}'")
            print(f"   h1 = '{h1_text.strip()}'")
            print(f"   subtitle = '{subtitle_text.strip()}'")
            if "ACCOUNT" not in (eyebrow_text or ""):
                failures.append(f"eyebrow text wrong: '{eyebrow_text}'")
            if "个人设置" not in (h1_text or ""):
                failures.append(f"h1 text wrong: '{h1_text}'")
            if "管理个人资料" not in (subtitle_text or ""):
                failures.append(f"subtitle text wrong: '{subtitle_text}'")

            # Step 3: 原 page-header.settings-header 不再存在
            old_header = page.locator(".page-header.settings-header").count()
            print(f"   old .page-header.settings-header count = {old_header}")
            if old_header != 0:
                failures.append(f"old .page-header.settings-header should be removed, got {old_header}")

            # Step 4: actions slot 区域存在（无内容但不报错）
            actions_root = page.locator("app-workspace-heading .workspace-heading-actions").count()
            print(f"   actions root count = {actions_root}")
            if actions_root != 1:
                failures.append(f"actions root should exist even when empty, got {actions_root}")

            page.screenshot(path=str(SHOT_DIR / "_x2_pr3_settings.png"), full_page=False)

        except Exception as e:
            failures.append(f"exception: {e!r}")
            try:
                page.screenshot(path=str(SHOT_DIR / "_x2_pr3_error.png"), full_page=True)
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
    print("PASS — X2 PR 3 settings heading OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
