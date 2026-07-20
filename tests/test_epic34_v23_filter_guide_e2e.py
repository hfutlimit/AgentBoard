"""
Epic 34 v2.3: 任务列表筛选结果引导 - Playwright E2E 验证
功能：
  1) 工具条「清除全部筛选」按钮（.clear-all-btn）：任一筛选活跃时显示，点击重置搜索/优先级 chips/只看我/高级筛选。
  2) 筛选导致零结果时的友好空状态（.filter-empty-state）：区分「本 Story 无任务」(.empty-inline) 与「筛选无匹配」。

确定性场景：复用 project 6 (Chess-2) 下自建 story 69（含若干任务）。
验证项：
  1. 登录 admin
  2. 打开 /story/69（列表视图），统计初始可见行数 total_before (>0)
  3. 初始无筛选 → 工具条 .clear-all-btn 不渲染
  4. 在搜索框输入不匹配关键词 → .filter-empty-state 出现（非 .empty-inline）、工具条 .clear-all-btn 出现、行数=0
  5. 点击工具条 .clear-all-btn → 行数恢复 total_before、按钮消失、搜索清空
  6. 再次输入不匹配词 → .filter-empty-state 出现 → 点击其内部「清除全部筛选」按钮 → 行数恢复、空状态消失
  7. 零 JS 报错 / 零 console error / 零 .js+.css 404
"""
import asyncio
import sys

STORY_ID = 69  # project 6 下自建 story；与 mine-filter 测试共用
NON_MATCH = "zzzqqq_nomatch_99999"


def _rows(page):
    return page.locator(".entity-item--rich")


async def main() -> bool:
    from playwright.async_api import async_playwright

    WEB_URL = "http://localhost:8080"

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
        print("PASS: Logged in")

        # Step2: open story (list view)
        print(f"Step 2: Open /story/{STORY_ID}...")
        await page.goto(WEB_URL + f"/story/{STORY_ID}", wait_until="networkidle")
        await page.wait_for_timeout(3500)
        list_btn = page.locator("button:has-text('列表')")
        if await list_btn.count() > 0:
            await list_btn.first.click()
            await page.wait_for_timeout(1500)
        total_before = await _rows(page).count()
        print(f"  total visible rows: {total_before}")
        if total_before == 0:
            print("FAIL: 0 rows (need a non-empty story)")
            await browser.close()
            return False

        # Step3: initial state - no clear button
        print("Step 3: Verify no clear button initially...")
        if await page.locator(".clear-all-btn").count() != 0:
            print("FAIL: .clear-all-btn should not render without active filters")
            await browser.close()
            return False
        print("PASS: .clear-all-btn hidden initially")

        # Step4: apply non-matching search -> empty state + clear button
        print("Step 4: Type non-matching query...")
        box = page.locator(".task-search-input")
        if await box.count() == 0:
            print("FAIL: .task-search-input not found")
            await browser.close()
            return False
        await box.first.fill(NON_MATCH)
        await page.wait_for_timeout(1200)
        if await page.locator(".filter-empty-state").count() == 0:
            print("FAIL: .filter-empty-state not shown for filtered-zero result")
            await browser.close()
            return False
        if await page.locator(".empty-inline").count() != 0:
            print("FAIL: .empty-inline (no-tasks) shown instead of filter-empty-state")
            await browser.close()
            return False
        if await page.locator(".clear-all-btn").count() == 0:
            print("FAIL: .clear-all-btn not shown when filter active")
            await browser.close()
            return False
        if await _rows(page).count() != 0:
            print("FAIL: rows should be 0 after non-matching search")
            await browser.close()
            return False
        print("PASS: filtered-empty-state + toolbar clear button shown, 0 rows")

        # Step5: click toolbar clear button -> reset
        print("Step 5: Click toolbar .clear-all-btn ...")
        await page.locator(".clear-all-btn").first.click()
        await page.wait_for_timeout(1200)
        if await _rows(page).count() != total_before:
            print(f"FAIL: rows after clear {await _rows(page).count()} != {total_before}")
            await browser.close()
            return False
        if await page.locator(".clear-all-btn").count() != 0:
            print("FAIL: .clear-all-btn still visible after clear")
            await browser.close()
            return False
        if (await box.first.input_value()) != "":
            print("FAIL: search box not cleared")
            await browser.close()
            return False
        print("PASS: clearing restores all rows, hides button, clears search")

        # Step6: re-apply + click empty-state button
        print("Step 6: Re-apply search, click empty-state button...")
        await box.first.fill(NON_MATCH)
        await page.wait_for_timeout(1200)
        if await page.locator(".filter-empty-state").count() == 0:
            print("FAIL: .filter-empty-state not shown again")
            await browser.close()
            return False
        await page.locator(".filter-empty-state button").first.click()
        await page.wait_for_timeout(1200)
        if await _rows(page).count() != total_before:
            print(f"FAIL: rows after empty-state clear {await _rows(page).count()} != {total_before}")
            await browser.close()
            return False
        if await page.locator(".filter-empty-state").count() != 0:
            print("FAIL: .filter-empty-state still visible after clear")
            await browser.close()
            return False
        print("PASS: empty-state clear button restores rows")

        # Screenshot
        await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/epic34_v23_filter_guide.png", full_page=False)
        print("PASS: Screenshot saved")

        # Step7: Error summary
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
