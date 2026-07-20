"""
Epic 32 v2.1: 任务列表键盘快捷键增强 - Playwright E2E 验证
验证项：
1. 登录 admin 用户
2. 打开含任务的 Story（列表视图）
3. 搜索框旁显示 `/` 快捷键提示（.search-kbd）
4. 列表区聚焦状态下按 `/` → .task-search-input 获得焦点
5. 在搜索框输入后按 `Esc` → 查询清空且输入框失焦
6. 零 JS 报错 / 零 .js+.css 404 / 零 console error
"""
import asyncio
import sys


async def main() -> bool:
    from playwright.async_api import async_playwright

    WEB_URL = "http://localhost:8080"  # 本地 web 代理到 58125（agentboard.db，数据完整）

    errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda msg: errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.url} - {req.failure}") if req.url.endswith(('.js', '.css')) else None)

        # Step 1: Login
        print("Step 1: Login as admin...")
        await page.goto(WEB_URL + "/", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        if await page.locator(".auth-tab").count() > 0:
            login_tab = page.locator(".auth-tab", has_text="登录")
            if await login_tab.count() > 0:
                await login_tab.first.click()
                await page.wait_for_timeout(500)
            await page.fill('input[name="username"]', "admin")
            await page.fill('input[name="password"]', "admin123")
            await page.click(".login-submit")
            await page.wait_for_timeout(3000)
        token = await page.evaluate("localStorage.getItem('agentboard_token')")
        if not token:
            print("FAIL: Login failed")
            await browser.close()
            return False
        print("PASS: Logged in as admin")

        # Step 2: Open a story with tasks (story 25 has 6 tasks)
        print("Step 2: Open story 25 (has tasks)...")
        await page.goto(WEB_URL + "/story/25", wait_until="networkidle")
        await page.wait_for_timeout(3500)
        list_btn = page.locator("button:has-text('列表')")
        if await list_btn.count() > 0:
            await list_btn.first.click()
            await page.wait_for_timeout(1500)

        row_count = await page.locator(".entity-item--rich").count()
        print(f"  task rows on story 25: {row_count}")
        if row_count == 0:
            print("  No rows on story 25; trying story 21...")
            await page.goto(WEB_URL + "/story/21", wait_until="networkidle")
            await page.wait_for_timeout(3500)
            list_btn = page.locator("button:has-text('列表')")
            if await list_btn.count() > 0:
                await list_btn.first.click()
                await page.wait_for_timeout(1500)
            row_count = await page.locator(".entity-item--rich").count()
            print(f"  task rows on story 21: {row_count}")
            if row_count == 0:
                print("FAIL: No task rows on story 25 or 21")
                await browser.close()
                return False

        # Step 3: Verify / hotkey hint visible
        print("Step 3: Verify .search-kbd hint...")
        kbd = page.locator(".search-kbd")
        if await kbd.count() == 0:
            print("FAIL: .search-kbd hint not found")
            await browser.close()
            return False
        kbd_text = (await kbd.first.text_content() or "").strip()
        print(f"  .search-kbd text: '{kbd_text}' (expected '/')")
        if kbd_text != "/":
            print("FAIL: .search-kbd text is not '/'")
            await browser.close()
            return False
        # 搜索框 placeholder 应提示快捷键
        ph = await page.locator(".task-search-input").get_attribute("placeholder") or ""
        print(f"  search placeholder: '{ph}'")
        if "/" not in ph:
            print("FAIL: placeholder does not mention '/' shortcut")
            await browser.close()
            return False
        print("PASS: / hotkey hint rendered")

        # Step 4: Focus the task list, press '/' -> search input focused
        print("Step 4: Focus list, press '/' -> search input focused...")
        await page.locator(".entity-list").focus()
        await page.wait_for_timeout(300)
        # 确认焦点在列表区（非 input）
        active_before = await page.evaluate("document.activeElement ? document.activeElement.className : ''")
        print(f"  activeElement before '/': '{active_before}'")
        await page.keyboard.press("/")
        await page.wait_for_timeout(400)
        is_search_focused = await page.evaluate(
            "document.activeElement && document.activeElement.classList.contains('task-search-input')"
        )
        print(f"  .task-search-input focused after '/': {is_search_focused}")
        if not is_search_focused:
            print("FAIL: pressing '/' did not focus .task-search-input")
            await browser.close()
            return False
        print("PASS: '/' focuses search input")

        # Step 5: Type into search, then Esc -> cleared + blurred
        print("Step 5: Type query, press Esc -> cleared & blurred...")
        await page.locator(".task-search-input").fill("登录")
        await page.wait_for_timeout(600)
        val_typed = await page.locator(".task-search-input").input_value()
        print(f"  search value after fill: '{val_typed}' (expected '登录')")
        if val_typed != "登录":
            print("FAIL: could not type into search input")
            await browser.close()
            return False
        # 确认此时若焦点在搜索框、再按 '/' 会输入到搜索（不触发聚焦逻辑，无冲突）
        await page.keyboard.press("/")
        await page.wait_for_timeout(200)
        val_with_slash = await page.locator(".task-search-input").input_value()
        print(f"  search value after '/' while focused: '{val_with_slash}' (expected '登录/')")
        if val_with_slash != "登录/":
            print("FAIL: '/' while input focused should type into search (no conflict)")
            await browser.close()
            return False
        # Esc clears
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        val_after_esc = await page.locator(".task-search-input").input_value()
        print(f"  search value after Esc: '{val_after_esc}' (expected '')")
        if val_after_esc != "":
            print("FAIL: Esc did not clear search query")
            await browser.close()
            return False
        # 失焦：activeElement 不应仍是 .task-search-input
        still_focused = await page.evaluate(
            "document.activeElement && document.activeElement.classList.contains('task-search-input')"
        )
        print(f"  search still focused after Esc: {still_focused} (expected False)")
        if still_focused:
            print("FAIL: Esc did not blur search input")
            await browser.close()
            return False
        print("PASS: Esc clears & blurs search input; no conflict with '/' typing")

        # Screenshot
        await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/epic32_tasklist_hotkeys.png", full_page=False)
        print("PASS: Screenshot saved")

        # Step 6: Error summary
        print("\n=== Error Summary ===")
        print(f"  pageerror: {len(page_errors)}")
        print(f"  console errors: {len(errors)}")
        print(f"  failed requests: {len(failed_requests)}")
        if page_errors:
            for e in page_errors[:5]:
                print(f"    pageerror: {e}")
        if errors:
            for e in errors[:5]:
                print(f"    console: {e}")
        if failed_requests:
            for r in failed_requests[:5]:
                print(f"    reqfail: {r}")

        await browser.close()

        ok = not page_errors and not errors and not failed_requests
        if ok:
            print("\n=== ALL PASS ===")
        else:
            print("\n=== FAIL: non-zero errors ===")
        return ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
