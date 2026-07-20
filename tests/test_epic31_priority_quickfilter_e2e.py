"""
Epic 31 v2.0: 任务列表优先级快速筛选 chips - Playwright E2E 验证
验证项：
1. 登录 admin 用户
2. 打开含任务的 Story（列表视图）
3. .task-quickfilter-bar 与 .qf-chip 渲染（全部 + 5 级优先级），计数正确
4. 点击某优先级 chip → 列表按该优先级过滤、chip active
5. 刷新页面 → 选择持久化（chip 仍 active、列表仍已筛、localStorage 保留）
6. 点击「全部」→ 清除筛选、列表恢复
7. 零 JS 报错 / 零 .js+.css 404 / 零 console error
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

        row_count_before = await page.locator(".entity-item--rich").count()
        print(f"  task rows on story 25: {row_count_before}")
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

        # Step 3: Verify quick-filter bar + chips
        print("Step 3: Verify .task-quickfilter-bar and .qf-chip...")
        bar = page.locator(".task-quickfilter-bar")
        if await bar.count() == 0:
            print("FAIL: .task-quickfilter-bar not found")
            await browser.close()
            return False
        chips = page.locator(".qf-chip")
        chip_count = await chips.count()
        print(f"  .qf-chip count: {chip_count} (expected 6: 全部 + 5 优先级)")
        if chip_count != 6:
            print("FAIL: expected 6 qf-chip")
            await browser.close()
            return False
        # 全部 chip 计数应等于任务行数
        all_count_text = (await chips.nth(0).locator(".qf-count").text_content() or "").strip()
        print(f"  全部 chip count text: {all_count_text} (expected {row_count_before})")
        if all_count_text != str(row_count_before):
            print("FAIL: 全部 chip count mismatch")
            await browser.close()
            return False
        print("PASS: quick-filter bar + chips rendered with correct 全部 count")

        # Step 4: Pick a priority chip with count > 0 and click it
        print("Step 4: Click a priority chip with count > 0...")
        target_idx = -1
        target_label = ""
        for i in range(1, chip_count):
            txt = (await chips.nth(i).locator(".qf-count").text_content() or "").strip()
            try:
                c = int(txt)
            except Exception:
                c = 0
            if c > 0:
                target_idx = i
                target_label = (await chips.nth(i).text_content() or "").strip()
                break
        if target_idx < 0:
            print("FAIL: no priority chip has count > 0")
            await browser.close()
            return False
        target_count = int((await chips.nth(target_idx).locator(".qf-count").text_content() or "0").strip())
        print(f"  clicking priority chip[{target_idx}]: {target_label} (count={target_count})")
        await chips.nth(target_idx).click()
        await page.wait_for_timeout(1200)

        # chip should be active
        active_now = await chips.nth(target_idx).get_attribute("class") or ""
        if "active" not in active_now:
            print("FAIL: clicked priority chip not active")
            await browser.close()
            return False
        # 全部 chip should no longer be active
        all_active = await chips.nth(0).get_attribute("class") or ""
        if "active" in all_active:
            print("FAIL: 全部 chip still active after selecting a priority")
            await browser.close()
            return False
        rows_after_filter = await page.locator(".entity-item--rich").count()
        print(f"  rows after filter: {rows_after_filter} (expected {target_count})")
        if rows_after_filter != target_count:
            print("FAIL: filtered row count mismatch")
            await browser.close()
            return False
        print("PASS: clicking priority chip filters list & marks active")

        # Step 5: Persistence across reload
        print("Step 5: Reload and verify persistence...")
        ls_before = await page.evaluate("localStorage.getItem('agentboard_quick_priority')")
        print(f"  localStorage before reload: {ls_before}")
        if not ls_before or ls_before == "[]":
            print("FAIL: localStorage not persisted before reload")
            await browser.close()
            return False
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(3500)
        # chips re-rendered; verify target chip still active
        chips2 = page.locator(".qf-chip")
        active_after_reload = await chips2.nth(target_idx).get_attribute("class") or ""
        if "active" not in active_after_reload:
            print("FAIL: priority chip not active after reload (persistence broken)")
            await browser.close()
            return False
        rows_after_reload = await page.locator(".entity-item--rich").count()
        print(f"  rows after reload: {rows_after_reload} (expected {target_count})")
        if rows_after_reload != target_count:
            print("FAIL: list not filtered after reload")
            await browser.close()
            return False
        print("PASS: selection persisted across reload")

        # Step 6: Click 全部 to clear
        print("Step 6: Click 全部 to clear filter...")
        await chips2.nth(0).click()
        await page.wait_for_timeout(1200)
        rows_cleared = await page.locator(".entity-item--rich").count()
        print(f"  rows after clear: {rows_cleared} (expected {row_count_before})")
        if rows_cleared != row_count_before:
            print("FAIL: clearing filter did not restore all rows")
            await browser.close()
            return False
        ls_after = await page.evaluate("localStorage.getItem('agentboard_quick_priority')")
        print(f"  localStorage after clear: {ls_after}")
        if ls_after and ls_after != "[]":
            print("FAIL: localStorage not cleared after 全部")
            await browser.close()
            return False
        print("PASS: 全部 clears filter and localStorage")

        # Screenshot
        await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/epic31_priority_quickfilter.png", full_page=False)
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
