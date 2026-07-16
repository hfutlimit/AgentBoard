"""验证「列表密度切换（紧凑视图）」功能 — Task 831。

真实浏览器驱动 SPA：
0. 首次进入若显示登录页，走「注册」流程建立会话（REQUIRE_AUTH=0）
1. 进入 Story 19 任务列表（默认舒适视图）
2. 点击密度切换按钮 → 断言 .entity-list.density-compact 出现
3. 测量 .entity-item--rich 的纵向内边距（舒适 > 紧凑）
4. 再点一次 → 回到舒适视图，断言 compact 类消失
5. 收集 pageerror / console error / 4xx/5xx 资源

输出 JSON 摘要到 stdout，并保存截图到 scripts/verify_density_*.png。
"""
import json
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8092"
STORY_URL = f"{BASE}/story/19"
import time as _time
USER = f"verify_{int(_time.time())}"
PASS = "Verify831!"

errors = []
console_errors = []
failed_resources = []


def ensure_logged_in(page):
    # 登录页出现 → 走注册流程建立会话
    try:
        page.wait_for_selector("input[name='username']", timeout=8000)
    except Exception:
        return  # 已登录或无需登录
    page.locator("button.auth-tab", has_text="注册").click()
    page.fill("input[name='username']", USER)
    page.fill("input[name='password']", PASS)
    page.locator("button.login-submit").click()
    # 等待离开登录态（出现应用主体或侧栏）
    page.wait_for_selector(".sidebar, .entity-list", timeout=15000)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on(
            "response",
            lambda r: failed_resources.append(f"HTTP {r.status} :: {r.url}")
            if r.status >= 400
            else None,
        )

        print("[verify] goto", BASE)
        page.goto(BASE, wait_until="networkidle")
        ensure_logged_in(page)

        print("[verify] goto", STORY_URL)
        page.goto(STORY_URL, wait_until="networkidle")
        try:
            page.wait_for_selector(".entity-list .entity-item--rich", timeout=15000)
        except Exception as e:
            print("[verify] ERROR: 任务列表未渲染:", e)
            page.screenshot(path="scripts/verify_density_fail.png")
            browser.close()
            dump(False, "list-not-rendered")
            return

        initial_compact = page.evaluate(
            "!!document.querySelector('.entity-list.density-compact')"
        )
        pad_comfortable = page.evaluate(
            "getComputedStyle(document.querySelector('.entity-item--rich')).paddingTop"
        )

        toggle = page.locator("#s-density-toggle")
        toggle_text_before = toggle.inner_text()
        toggle.click()
        page.wait_for_timeout(400)

        compact_now = page.evaluate(
            "!!document.querySelector('.entity-list.density-compact')"
        )
        pad_compact = page.evaluate(
            "getComputedStyle(document.querySelector('.entity-item--rich')).paddingTop"
        )
        toggle_text_after = page.locator("#s-density-toggle").inner_text()
        page.screenshot(path="scripts/verify_density_compact.png")

        page.locator("#s-density-toggle").click()
        page.wait_for_timeout(400)
        back_compact = page.evaluate(
            "!!document.querySelector('.entity-list.density-compact')"
        )

        ok = (
            initial_compact is False
            and compact_now is True
            and back_compact is False
            and pad_compact != pad_comfortable
            and len(errors) == 0
            and len(console_errors) == 0
            and all("HTTP 4" not in r and "HTTP 5" not in r for r in failed_resources)
        )

        browser.close()
        dump(
            ok,
            "ok" if ok else "assertion-failed",
            initial_compact=initial_compact,
            compact_after_toggle=compact_now,
            back_to_comfortable=back_compact,
            pad_comfortable=pad_comfortable,
            pad_compact=pad_compact,
            toggle_text_before=toggle_text_before,
            toggle_text_after=toggle_text_after,
        )


def dump(ok, stage, **extra):
    summary = {
        "ok": ok,
        "stage": stage,
        "page_errors": errors,
        "console_errors": console_errors,
        "failed_resources": failed_resources,
        **extra,
    }
    print("===VERIFY_RESULT===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
