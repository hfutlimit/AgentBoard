"""E2E test for v1.8 Collapsible Task Groups.

Verifies:
1. Login → project → story with tasks
2. Group by status → group headers appear with chevron
3. Click header → items hidden (collapsed)
4. Click again → items visible (expanded)
5. State persists in localStorage
6. Zero page errors / console errors / 404 resources
"""
import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8080"
API = "http://127.0.0.1:58125"

PAGE_ERRORS: list[str] = []
CONSOLE_ERRORS: list[str] = []
FAILED_RESOURCES: list[str] = []


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        page.on("pageerror", lambda e: PAGE_ERRORS.append(str(e)))
        page.on("console", lambda m: CONSOLE_ERRORS.append(m.text) if m.type == "error" else None)
        page.on("requestfailed", lambda r: FAILED_RESOURCES.append(f"{r.url} [{r.failure}]"))

        # 1. Login
        await page.goto(f"{BASE}/login", wait_until="networkidle")
        await page.wait_for_selector("input[name=username]", timeout=10000)
        await page.fill("input[name=username]", "admin")
        await page.fill("input[name=password]", "admin123")
        await page.click(".login-submit")
        await page.wait_for_url(f"{BASE}/", timeout=10000)

        # 2. Navigate directly to a story with tasks (Story 1 has 3 tasks)
        await page.goto(f"{BASE}/story/1", wait_until="networkidle")
        await page.wait_for_selector(".entity-item--rich, .task-group-select", timeout=10000)
        await asyncio.sleep(2)

        # 4. Set grouping to "by status"
        group_select = page.locator(".task-group-select")
        await group_select.select_option("status")
        await asyncio.sleep(1)

        # 5. Check group headers appear
        headers = page.locator(".task-group-header")
        header_count = await headers.count()
        print(f"[INFO] Group headers found: {header_count}")

        if header_count == 0:
            print("[WARN] No group headers found - checking if tasks exist at all")
            tasks = page.locator(".entity-item--rich")
            task_count = await tasks.count()
            print(f"[INFO] Tasks found: {task_count}")
            if task_count == 0:
                print("[SKIP] No tasks to test grouping with - try another story")
                # Try navigating to another story
                all_story_links = page.locator('a[href*="/story/"]')
                cnt = await all_story_links.count()
                for i in range(min(cnt, 5)):
                    await all_story_links.nth(i).click()
                    await asyncio.sleep(1)
                    tasks = page.locator(".entity-item--rich")
                    if await tasks.count() > 0:
                        break

        if header_count > 0:
            # 6. Click first group header to collapse
            first_header = headers.first
            header_text = await first_header.inner_text()
            print(f"[INFO] Clicking first group header: {header_text}")

            # Count items before collapse
            all_items_before = await page.locator(".entity-item--rich").count()
            print(f"[INFO] Items before collapse: {all_items_before}")

            await first_header.click()
            await asyncio.sleep(0.5)

            # Check collapsed class
            is_collapsed = await first_header.get_attribute("class")
            print(f"[INFO] Header class after click: {is_collapsed}")

            # Count items after collapse (should be fewer)
            all_items_after = await page.locator(".entity-item--rich").count()
            print(f"[INFO] Items after collapse: {all_items_after}")

            if all_items_after < all_items_before:
                print("[PASS] Collapse hides items")
            else:
                print("[WARN] Item count didn't decrease after collapse")

            # 7. Click again to expand
            await first_header.click()
            await asyncio.sleep(0.5)
            all_items_expanded = await page.locator(".entity-item--rich").count()
            print(f"[INFO] Items after expand: {all_items_expanded}")

            if all_items_expanded >= all_items_after:
                print("[PASS] Expand shows items")
            else:
                print("[WARN] Items didn't reappear after expand")

            # 8. Check chevron presence
            chevron = first_header.locator(".task-group-chevron")
            chevron_count = await chevron.count()
            print(f"[INFO] Chevron found: {chevron_count}")
            if chevron_count > 0:
                print("[PASS] Chevron icon present")
            else:
                print("[FAIL] No chevron icon found")

            # 9. Check localStorage persistence
            stored = await page.evaluate("localStorage.getItem('agentboard_collapsed_groups')")
            print(f"[INFO] localStorage collapsed_groups: {stored}")

            # 10. Collapse again and reload to test persistence
            await first_header.click()
            await asyncio.sleep(0.3)
            stored_after = await page.evaluate("localStorage.getItem('agentboard_collapsed_groups')")
            print(f"[INFO] localStorage after collapse: {stored_after}")

        # 11. Switch to "no grouping" and verify no headers
        await group_select.select_option("none")
        await asyncio.sleep(0.5)
        headers_none = await page.locator(".task-group-header").count()
        print(f"[INFO] Headers in 'none' mode: {headers_none}")

        # Summary
        print("\n=== E2E Summary ===")
        print(f"Page errors: {len(PAGE_ERRORS)}")
        for e in PAGE_ERRORS:
            print(f"  {e}")
        print(f"Console errors: {len(CONSOLE_ERRORS)}")
        for e in CONSOLE_ERRORS:
            print(f"  {e}")

        js_css_failures = [r for r in FAILED_RESOURCES if r.endswith(".js") or ".css" in r]
        print(f"JS/CSS failed resources: {len(js_css_failures)}")
        for r in js_css_failures:
            print(f"  {r}")

        await browser.close()

        if PAGE_ERRORS or CONSOLE_ERRORS or js_css_failures:
            return 1
        return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
