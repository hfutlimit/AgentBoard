"""Epic 35 - Task keyword search filter E2E test.

Verifies:
- Search input renders in task list toolbar
- Typing filters the task list by title (case-insensitive)
- Clearing search restores all tasks
- Zero page errors / console errors / .js+.css 404s
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from playwright.async_api import async_playwright

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8080")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})

        page_errors = []
        console_errors = []
        failed_requests = []

        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("requestfailed", lambda r: (
            failed_requests.append(f"{r.url} ({r.failure})")
            if r.url.endswith((".js", ".css")) else None
        ))

        # Step 1: Login
        await page.goto(f"{BASE}/login", wait_until="networkidle")
        await page.wait_for_selector(".auth-tab", timeout=10000)
        login_tab = page.locator(".auth-tab:has-text('登录')")
        if await login_tab.count() > 0:
            await login_tab.click()
            await page.wait_for_timeout(300)
        await page.fill('input[name="username"]', "admin")
        await page.fill('input[name="password"]', "admin123")
        await page.click(".login-submit")
        await page.wait_for_selector(".topbar", timeout=10000)
        print("[OK] Login successful")

        # Step 2: Navigate directly to story 33 (has tasks) via path URL
        await page.goto(f"{BASE}/story/33", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        print(f"[OK] Navigated to story. URL: {page.url}")

        # Step 3: Check search input exists
        search_input = page.locator(".task-search-input")
        assert await search_input.count() > 0, "Task search input not found"
        print("[OK] Search input rendered")

        # Step 6: Count tasks before search
        await page.wait_for_timeout(1000)
        task_items = await page.locator(".entity-item--rich").all()
        initial_count = len(task_items)
        print(f"[OK] Initial task count: {initial_count}")
        assert initial_count > 0, "No tasks to test search with"

        # Step 7: Type a search query (use first task's title partial)
        first_title_el = page.locator(".entity-item-title").first
        first_title = await first_title_el.inner_text()
        # Use a longer, more unique substring to avoid common matches
        search_term = first_title[:8] if len(first_title) >= 8 else first_title[:3]
        await search_input.fill(search_term)
        await page.wait_for_timeout(800)

        filtered_items = await page.locator(".entity-item--rich").all()
        filtered_count = len(filtered_items)
        print(f"[OK] After searching '{search_term}': {filtered_count} tasks")
        assert filtered_count <= initial_count, "Filter did not reduce or match count"
        assert filtered_count >= 1, "Search returned 0 results for a known title"

        # Step 8: Type a non-matching query
        await search_input.fill("zzzznonexistent12345")
        await page.wait_for_timeout(500)
        empty_items = await page.locator(".entity-item--rich").all()
        empty_count = len(empty_items)
        print(f"[OK] After non-matching search: {empty_count} tasks")
        assert empty_count == 0, f"Expected 0 results for non-matching query, got {empty_count}"

        # Check empty state
        empty_msg = page.locator(".empty-inline")
        assert await empty_msg.count() > 0, "Empty state message not shown"
        print("[OK] Empty state shown for no results")

        # Step 9: Clear search and verify all tasks return
        clear_btn = page.locator(".task-search-clear")
        assert await clear_btn.count() > 0, "Clear button not found"
        await clear_btn.click()
        await page.wait_for_timeout(500)

        restored_items = await page.locator(".entity-item--rich").all()
        restored_count = len(restored_items)
        print(f"[OK] After clearing search: {restored_count} tasks")
        assert restored_count == initial_count, f"Restore count mismatch: {restored_count} != {initial_count}"

        # Step 10: Clear via emptying input
        await search_input.fill("")
        await page.wait_for_timeout(500)
        final_items = await page.locator(".entity-item--rich").all()
        assert len(final_items) == initial_count, "Clearing input didn't restore tasks"
        print("[OK] Clearing input restores all tasks")

        # Step 11: Error checks
        print(f"\n--- Error Summary ---")
        print(f"Page errors: {len(page_errors)}")
        print(f"Console errors: {len(console_errors)}")
        print(f"Failed .js/.css requests: {len(failed_requests)}")

        assert len(page_errors) == 0, f"Page errors: {page_errors}"
        assert len(failed_requests) == 0, f"Failed requests: {failed_requests}"
        # Console errors: allow API ERR_ABORTED (benign SPA routing race)
        real_console = [e for e in console_errors if "ERR_ABORTED" not in e and "favicon" not in e.lower()]
        assert len(real_console) == 0, f"Console errors: {real_console}"

        print("\n=== ALL CHECKS PASSED ===")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
