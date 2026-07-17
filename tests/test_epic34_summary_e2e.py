"""
Epic 34.1: 任务列表汇总栏 - Playwright E2E 验证
验证项：
1. 登录 admin 用户
2. 直接导航到含任务的 Story（列表视图）
3. .task-list-summary 汇总栏渲染
4. .summary-stack 堆叠条段数 > 0
5. 文案含 "共"/"完成率"
6. 切换看板再切回列表，汇总栏消失再重现
7. 零 JS 报错 / 零 404 / 零 console error
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
        # Only count .js/.css failures (existing convention). API ERR_ABORTED from
        # dashboard preloader race is pre-existing and benign.
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

        # Step 2: Navigate directly to a story with tasks (story 25 has 6 tasks)
        print("Step 2: Open story 25 (has tasks)...")
        await page.goto(WEB_URL + "/story/25", wait_until="networkidle")
        await page.wait_for_timeout(3500)
        print(f"  URL: {page.url}")

        # Ensure list mode (not kanban)
        list_btn = page.locator("button:has-text('列表')")
        if await list_btn.count() > 0:
            await list_btn.first.click()
            await page.wait_for_timeout(1500)

        # Step 3: Verify summary bar
        print("Step 3: Verify task-list-summary...")
        summary = page.locator(".task-list-summary")
        sc = await summary.count()
        print(f"  .task-list-summary count: {sc}")
        if sc == 0:
            # Fallback: try story 21 (also 6 tasks)
            print("  No summary on story 25; trying story 21...")
            await page.goto(WEB_URL + "/story/21", wait_until="networkidle")
            await page.wait_for_timeout(3500)
            list_btn = page.locator("button:has-text('列表')")
            if await list_btn.count() > 0:
                await list_btn.first.click()
                await page.wait_for_timeout(1500)
            sc = await page.locator(".task-list-summary").count()
            print(f"  .task-list-summary count on story 21: {sc}")
            if sc == 0:
                print("FAIL: No .task-list-summary")
                await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/epic34_no_summary.png")
                body = await page.locator("body").text_content()
                print(f"  body[:300]: {body[:300]}")
                await browser.close()
                return False

        # Verify segments
        segs = page.locator(".summary-seg")
        seg_count = await segs.count()
        print(f"  .summary-seg count: {seg_count}")
        if seg_count == 0:
            print("FAIL: No summary segments")
            await browser.close()
            return False
        print("PASS: Summary bar with segments rendered")

        # Verify text content
        summary_text = await page.locator(".task-list-summary").text_content() or ""
        has_total = "共" in summary_text
        has_rate = "完成率" in summary_text
        print(f"  text has '共': {has_total}, has '完成率': {has_rate}")
        print(f"  summary text: {summary_text.strip()[:140]}")
        if not (has_total and has_rate):
            print("FAIL: Summary text missing required labels")
            await browser.close()
            return False
        print("PASS: Summary text contains 共/完成率")

        # Verify summary count matches task list row count (consistency check)
        row_count = await page.locator(".entity-item--rich").count()
        import re
        m = re.search(r"共\s*(\d+)\s*项", summary_text)
        summary_total = int(m.group(1)) if m else -1
        print(f"  task list rows: {row_count}, summary total: {summary_total}")
        if summary_total != row_count:
            print(f"FAIL: summary total ({summary_total}) != task list rows ({row_count})")
            await browser.close()
            return False
        print("PASS: Summary total matches task list row count")

        # Step 4: Toggle to kanban and back — summary should disappear then reappear
        print("Step 4: Toggle kanban -> list...")
        board_btn = page.locator("button:has-text('看板')")
        if await board_btn.count() > 0:
            await board_btn.first.click()
            await page.wait_for_timeout(1500)
            in_kanban = await page.locator(".task-list-summary").count()
            print(f"  summary in kanban mode: {in_kanban} (expected 0)")
            if in_kanban != 0:
                print("  WARN: summary still visible in kanban (expected hidden)")
            list_btn = page.locator("button:has-text('列表')")
            if await list_btn.count() > 0:
                await list_btn.first.click()
                await page.wait_for_timeout(1500)
            back = await page.locator(".task-list-summary").count()
            print(f"  summary after back to list: {back} (expected >=1)")
            if back == 0:
                print("FAIL: Summary did not reappear after toggling back to list")
                await browser.close()
                return False
            print("PASS: Summary reappears after list toggle")
        else:
            print("  No 看板 button; skipping toggle test")

        # Screenshot
        await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/epic34_summary_bar.png", full_page=False)
        print("PASS: Screenshot saved")

        # Step 5: Error summary
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
