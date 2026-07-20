"""
Epic 38 v2.4: 任务列表类型快速筛选 chips - Playwright E2E 验证
验证项：
1. 登录 admin 用户
2. 打开含任务的 Story（列表视图）
3. 类型快速筛选 bar（aria-label=按类型快速筛选）渲染 3 枚 chip（全部 + 任务 + Bug），计数正确
4. 点击「Bug」→ 列表仅含 bug 任务（行数 == Bug 计数）；再点「任务」→ 仅 task；chip active 正确
5. 刷新页面 → 选择持久化（chip 仍 active、列表仍已筛、localStorage.agentboard_quick_type 保留）
6. 点击「全部」→ 清除筛选、列表恢复；localStorage 清空
7. 零 JS 报错 / 零 .js+.css 404 / 零 console error

注：项目 3 历史数据无 bug 类型任务，本测试在 story 25 临时注入 1 条 bug 任务以验证双向过滤，
结束后由调用方删除，不污染项目数据。
"""
import asyncio
import sys

TYPE_BAR = '.task-quickfilter-bar[aria-label="按类型快速筛选"]'


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

        # Step1: Login
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

        # Step2: Open story 25 (has tasks) in list view
        print("Step 2: Open story 25 (list view)...")
        await page.goto(WEB_URL + "/story/25", wait_until="networkidle")
        await page.wait_for_timeout(3500)
        list_btn = page.locator("button:has-text('列表')")
        if await list_btn.count() > 0:
            await list_btn.first.click()
            await page.wait_for_timeout(1500)

        row_count_before = await page.locator(".entity-item--rich").count()
        print(f"  task rows on story 25: {row_count_before}")
        if row_count_before == 0:
            print("FAIL: No task rows on story 25")
            await browser.close()
            return False

        # Step3: Verify type quick-filter bar + 3 chips
        print("Step 3: Verify type quick-filter bar + 3 chips...")
        bar = page.locator(TYPE_BAR)
        if await bar.count() == 0:
            print("FAIL: type quick-filter bar not found")
            await browser.close()
            return False
        chips = bar.locator(".qf-chip")
        chip_count = await chips.count()
        print(f"  type .qf-chip count: {chip_count} (expected 3: 全部+任务+Bug)")
        if chip_count != 3:
            print("FAIL: expected 3 type qf-chip")
            await browser.close()
            return False
        all_chip = bar.locator(".qf-chip", has_text="全部")
        task_chip = bar.locator(".qf-chip", has_text="任务")
        bug_chip = bar.locator(".qf-chip", has_text="Bug")
        all_count_text = (await all_chip.locator(".qf-count").text_content() or "").strip()
        print(f"  全部 chip count: {all_count_text} (expected {row_count_before})")
        if all_count_text != str(row_count_before):
            print("FAIL: 全部 chip count mismatch")
            await browser.close()
            return False
        print("PASS: type bar + 3 chips rendered, 全部 count correct")

        # Read 任务 / Bug counts
        task_count = int((await task_chip.locator(".qf-count").text_content() or "0").strip() or 0)
        bug_count = int((await bug_chip.locator(".qf-count").text_content() or "0").strip() or 0)
        print(f"  任务 count={task_count}, Bug count={bug_count}")

        # Step4: Click Bug (if >0) else verify exclusion (0 rows)
        print("Step 4: Click Bug chip...")
        await bug_chip.click()
        await page.wait_for_timeout(1200)
        bug_active = "active" in (await bug_chip.get_attribute("class") or "")
        if not bug_active:
            print("FAIL: Bug chip not active after click")
            await browser.close()
            return False
        rows_bug = await page.locator(".entity-item--rich").count()
        print(f"  rows after Bug click: {rows_bug} (expected {bug_count})")
        if rows_bug != bug_count:
            print("FAIL: Bug-filtered row count mismatch")
            await browser.close()
            return False
        all_active = "active" in (await all_chip.get_attribute("class") or "")
        if all_active:
            print("FAIL: 全部 still active after selecting Bug")
            await browser.close()
            return False
        print("PASS: Bug chip filters list & marks active")

        # Step5: Click 任务 → only task-type rows
        print("Step 5: Click 任务 chip...")
        await task_chip.click()
        await page.wait_for_timeout(1200)
        task_active = "active" in (await task_chip.get_attribute("class") or "")
        if not task_active:
            print("FAIL: 任务 chip not active after click")
            await browser.close()
            return False
        rows_task = await page.locator(".entity-item--rich").count()
        print(f"  rows after 任务 click: {rows_task} (expected {task_count})")
        if rows_task != task_count:
            print("FAIL: 任务-filtered row count mismatch")
            await browser.close()
            return False
        bug_active2 = "active" in (await bug_chip.get_attribute("class") or "")
        if bug_active2:
            print("FAIL: Bug still active after selecting 任务")
            await browser.close()
            return False
        print("PASS: 任务 chip filters list & marks active")

        # Step6: Persistence across reload
        print("Step 6: Reload and verify persistence...")
        ls_before = await page.evaluate("localStorage.getItem('agentboard_quick_type')")
        print(f"  localStorage before reload: {ls_before}")
        if not ls_before or ls_before == "[]":
            print("FAIL: localStorage not persisted before reload")
            await browser.close()
            return False
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(3500)
        bar2 = page.locator(TYPE_BAR)
        task2 = bar2.locator(".qf-chip", has_text="任务")
        task_active_reload = "active" in (await task2.get_attribute("class") or "")
        if not task_active_reload:
            print("FAIL: 任务 chip not active after reload (persistence broken)")
            await browser.close()
            return False
        rows_reload = await page.locator(".entity-item--rich").count()
        print(f"  rows after reload: {rows_reload} (expected {task_count})")
        if rows_reload != task_count:
            print("FAIL: list not filtered after reload")
            await browser.close()
            return False
        print("PASS: selection persisted across reload")

        # Step7: Click 全部 to clear
        print("Step 7: Click 全部 to clear filter...")
        all2 = bar2.locator(".qf-chip", has_text="全部")
        await all2.click()
        await page.wait_for_timeout(1200)
        rows_cleared = await page.locator(".entity-item--rich").count()
        print(f"  rows after clear: {rows_cleared} (expected {row_count_before})")
        if rows_cleared != row_count_before:
            print("FAIL: clearing filter did not restore all rows")
            await browser.close()
            return False
        ls_after = await page.evaluate("localStorage.getItem('agentboard_quick_type')")
        print(f"  localStorage after clear: {ls_after}")
        if ls_after and ls_after != "[]":
            print("FAIL: localStorage not cleared after 全部")
            await browser.close()
            return False
        print("PASS: 全部 clears filter and localStorage")

        # Screenshot
        await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/epic38_v24_type_quickfilter.png", full_page=False)
        print("PASS: Screenshot saved")

        # Step8: Error summary
        print("\n=== Error Summary ===")
        print(f"  pageerror: {len(page_errors)}")
        print(f"  console errors: {len(errors)}")
        print(f"  failed requests: {len(failed_requests)}")
        for e in page_errors[:5]:
            print(f"    pageerror: {e}")
        for e in errors[:5]:
            print(f"    console: {e}")
        for r in failed_requests[:5]:
            print(f"    reqfail: {r}")

        await browser.close()
        ok = not page_errors and not errors and not failed_requests
        print("\n=== ALL PASS ===" if ok else "\n=== FAIL: non-zero errors ===")
        return ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
