"""
v1.9: 任务列表分组「一键全折叠 / 全展开」- Playwright E2E 验证
验证项：
1. 登录 admin 用户
2. 打开含任务的 Story（列表视图），按状态分组
3. .task-group-toggle-all 按钮出现且可用
4. 点击「全折叠」后：所有 .task-group-header 带 .collapsed、任务行隐藏（entity-item 归零）
5. 点击「全展开」后：无 .collapsed 头部、任务行恢复
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

        # Ensure there are task rows before grouping
        row_count_before = await page.locator(".entity-item--rich").count()
        print(f"  task rows before grouping: {row_count_before}")
        if row_count_before == 0:
            print("  No rows on story 25; trying story 21...")
            await page.goto(WEB_URL + "/story/21", wait_until="networkidle")
            await page.wait_for_timeout(3500)
            list_btn = page.locator("button:has-text('列表')")
            if await list_btn.count() > 0:
                await list_btn.first.click()
                await page.wait_for_timeout(1500)
            row_count_before = await page.locator(".entity-item--rich").count()
            print(f"  task rows on story 21: {row_count_before}")
            if row_count_before == 0:
                print("FAIL: No task rows on story 25 or 21")
                await browser.close()
                return False

        # Step 3: Group by status
        print("Step 3: Group by status...")
        group_select = page.locator(".task-group-select")
        if await group_select.count() == 0:
            print("FAIL: .task-group-select not found")
            await browser.close()
            return False
        await group_select.select_option("status")
        await page.wait_for_timeout(1500)

        headers = await page.locator(".task-group-header").count()
        print(f"  .task-group-header count after grouping: {headers}")
        if headers == 0:
            print("FAIL: No group headers after grouping by status")
            await browser.close()
            return False
        print("PASS: Grouped by status")

        # Step 4: Verify toggle-all button present & enabled
        print("Step 4: Verify .task-group-toggle-all button...")
        toggle = page.locator(".task-group-toggle-all")
        tc = await toggle.count()
        print(f"  .task-group-toggle-all count: {tc}")
        if tc == 0:
            print("FAIL: .task-group-toggle-all button not found")
            await browser.close()
            return False
        disabled = await toggle.first.is_disabled()
        print(f"  button disabled: {disabled}")
        if disabled:
            print("FAIL: toggle-all button disabled unexpectedly")
            await browser.close()
            return False
        btn_text = (await toggle.first.text_content() or "").strip()
        print(f"  button text (before): {btn_text}")
        if "全折叠" not in btn_text:
            print("FAIL: toggle-all button does not show 全折叠 initially")
            await browser.close()
            return False
        print("PASS: toggle-all button present, enabled, shows 全折叠")

        # Step 5: Click 全折叠 -> all headers collapsed, rows hidden
        print("Step 5: Click 全折叠...")
        await toggle.first.click()
        await page.wait_for_timeout(1200)
        collapsed_headers = await page.locator(".task-group-header.collapsed").count()
        rows_after_collapse = await page.locator(".entity-item--rich").count()
        btn_text_after = (await toggle.first.text_content() or "").strip()
        print(f"  collapsed headers: {collapsed_headers} (expected {headers})")
        print(f"  rows after collapse: {rows_after_collapse} (expected 0)")
        print(f"  button text (after): {btn_text_after}")
        if collapsed_headers != headers:
            print("FAIL: not all group headers collapsed")
            await browser.close()
            return False
        if rows_after_collapse != 0:
            print("FAIL: task rows still visible after collapse-all")
            await browser.close()
            return False
        if "全展开" not in btn_text_after:
            print("FAIL: button did not switch to 全展开")
            await browser.close()
            return False
        print("PASS: All groups collapsed, rows hidden, button -> 全展开")

        # Step 6: Click 全展开 -> headers uncollapsed, rows restored
        print("Step 6: Click 全展开...")
        await toggle.first.click()
        await page.wait_for_timeout(1200)
        collapsed_after_expand = await page.locator(".task-group-header.collapsed").count()
        rows_after_expand = await page.locator(".entity-item--rich").count()
        btn_text_expand = (await toggle.first.text_content() or "").strip()
        print(f"  collapsed headers after expand: {collapsed_after_expand} (expected 0)")
        print(f"  rows after expand: {rows_after_expand} (expected {row_count_before})")
        print(f"  button text (expand): {btn_text_expand}")
        if collapsed_after_expand != 0:
            print("FAIL: groups still collapsed after 全展开")
            await browser.close()
            return False
        if rows_after_expand != row_count_before:
            print(f"FAIL: rows not restored ({rows_after_expand} != {row_count_before})")
            await browser.close()
            return False
        if "全折叠" not in btn_text_expand:
            print("FAIL: button did not switch back to 全折叠")
            await browser.close()
            return False
        print("PASS: All groups expanded, rows restored, button -> 全折叠")

        # Screenshot
        await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/v19_collapse_all_groups.png", full_page=False)
        print("PASS: Screenshot saved")

        # Step 7: Error summary
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
