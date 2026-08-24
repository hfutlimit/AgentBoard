"""Playwright 端到端验证：register → /bugs 渲染（双栈 BFF 修复回归 + #1433 复验）

验证流程：
1. 打开前端 http://127.0.0.1:4200/
2. 切到 register tab
3. 填写 username + 9 字符 password（避开 8 字符边界）
4. 提交 → 期望跳转到 home（不能 500，不能 skeleton 卡死）
5. 访问 /bugs 路由 → 期望渲染 "全局 Bugs 概览" 内容（不是空白 skeleton）
6. 收集 console 错误 + page 错误 + 失败网络请求

修复前（commit 88fc556 不完整）：
- register 提交 → 后端 500 → 前端 form 一直 submitting → 整个登录流卡死

修复后（本 PR UserConfiguration.cs HasDefaultValueSql）：
- register 提交 → 201 → 自动跳到 home → /bugs 渲染
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

FRONTEND = "http://127.0.0.1:4200"
BFF = "http://127.0.0.1:18099"
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)


def main() -> int:
    username = f"pw_{uuid.uuid4().hex[:10]}"
    password = f"PlayPass_{uuid.uuid4().hex[:6]}"
    print(f"[e2e] using username={username} password={password[:6]}…")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = ctx.new_page()

        console_errors: list[str] = []
        page_errors: list[str] = []
        net_failures: list[dict] = []

        page.on("console", lambda m: console_errors.append(m.text[:300]) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)[:300]))

        def _on_response(resp):
            if resp.status >= 500 and "/api/" in resp.url:
                net_failures.append({"url": resp.url, "status": resp.status, "method": resp.request.method})

        page.on("response", _on_response)

        # 1. 打开登录页（直跳避免根路由重定向 race）
        page.goto(FRONTEND + "/login", wait_until="domcontentloaded", timeout=30000)
        # 等登录表单渲染
        try:
            page.wait_for_selector("app-login .auth-form", timeout=20000)
        except Exception:
            token = page.evaluate("() => localStorage.getItem('agentboard_token')")
            if token:
                print(f"[e2e] detected existing session, token len={len(token)}")
            else:
                page.screenshot(path=str(SCREENSHOT_DIR / "e2e_01_landing.png"), full_page=True)
                raise
        page.screenshot(path=str(SCREENSHOT_DIR / "e2e_01_landing.png"), full_page=True)

        # 2. 切到 register tab（点 "注册" 按钮）
        try:
            page.get_by_role("button", name="注册").click(timeout=5000)
        except Exception:
            # 备用选择器
            page.locator(".auth-tab", has_text="注册").first.click(timeout=5000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(SCREENSHOT_DIR / "e2e_02_register_tab.png"), full_page=True)

        # 3. 填表
        page.locator('input[name="username"]').fill(username)
        page.locator('input[name="password"]').fill(password)

        # 4. 提交
        submit_btn = page.locator('button[type="submit"]').first
        submit_btn.click()

        # 5. 等跳转（要么 URL 离开 /login，要么出现 home 信号）
        # 给后端 register 最多 10s
        deadline = time.time() + 15
        landed_home = False
        last_url = page.url
        while time.time() < deadline:
            page.wait_for_timeout(500)
            try:
                # 注册成功会跳到 /projects 或 / 之类
                cur_url = page.url
                if cur_url != last_url and not cur_url.endswith("/login"):
                    landed_home = True
                    break
                # 或者 home-shell-mode 出现
                if page.locator("app-root app-home, .home-shell, .project-browser").count() > 0:
                    landed_home = True
                    break
            except Exception:
                pass
            last_url = page.url
        page.screenshot(path=str(SCREENSHOT_DIR / "e2e_03_after_submit.png"), full_page=True)

        # 6. 访问 /bugs 路由
        page.goto(FRONTEND + "/bugs", wait_until="domcontentloaded", timeout=30000)
        # 等内容渲染（不是 skeleton）
        for _ in range(40):
            page.wait_for_timeout(500)
            main_text_len = page.evaluate("""() => {
                const m = document.querySelector('main') || document.querySelector('app-root');
                return m ? (m.innerText || '').trim().length : 0;
            }""")
            skel_count = page.evaluate("""() => {
                return document.querySelectorAll('[class*=skeleton], [class*=Skeleton], .loading, .spinner, [class*=loading]').length;
            }""")
            if main_text_len > 50 and skel_count == 0:
                break
        page.screenshot(path=str(SCREENSHOT_DIR / "e2e_04_bugs_route.png"), full_page=True)

        bugs_main_len = page.evaluate("""() => {
            const m = document.querySelector('main') || document.querySelector('app-root');
            return m ? (m.innerText || '').trim().length : 0;
        }""")
        bugs_h1 = page.evaluate("() => { const h = document.querySelector('h1'); return h ? h.innerText.trim() : ''; }")
        bugs_skel = page.evaluate("() => document.querySelectorAll('[class*=skeleton], [class*=Skeleton], .loading').length")

        browser.close()

    # 7. 报告
    print("\n" + "=" * 60)
    print(f"[RESULT] landed_home_after_register = {landed_home}")
    print(f"[RESULT] /bugs main_text_len         = {bugs_main_len}")
    print(f"[RESULT] /bugs h1                    = {bugs_h1!r}")
    print(f"[RESULT] /bugs skeleton_count        = {bugs_skel}")
    print(f"[RESULT] console_errors              = {len(console_errors)}")
    for e in console_errors[:5]:
        print(f"   - {e}")
    print(f"[RESULT] page_errors                 = {len(page_errors)}")
    for e in page_errors[:5]:
        print(f"   - {e}")
    print(f"[RESULT] net_5xx_failures            = {len(net_failures)}")
    for f in net_failures[:5]:
        print(f"   - {f}")

    ok = (
        landed_home
        and bugs_main_len > 50
        and bugs_skel == 0
        and len(net_failures) == 0
        and "Bug" in (bugs_h1 or "")
    )
    print("=" * 60)
    print(f"[{'PASS' if ok else 'FAIL'}] e2e regression suite for BFF register fix + /bugs render")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
