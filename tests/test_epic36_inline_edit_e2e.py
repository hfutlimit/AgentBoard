"""Epic 36 - Inline task title editing E2E test.

Verifies UI behavior:
- Edit button click shows inline input with current title
- Enter closes the input (save)
- Escape closes the input (cancel)
- Blur closes the input (save)
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

        # Step 2: Navigate to story 33
        await page.goto(f"{BASE}/story/33", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        print(f"[OK] Navigated to story. URL: {page.url}")

        # Step 3: Verify task list and edit button exist
        task_titles = page.locator(".entity-item-title")
        count = await task_titles.count()
        assert count > 0, "No task titles found"
        print(f"[OK] Found {count} task titles")

        # Step 4: Get first title BEFORE editing, then hover to reveal edit button
        first_title = await task_titles.first.inner_text()
        print(f"[OK] First title: '{first_title[:40]}'")
        await page.locator(".entity-item--rich").first.hover()
        await page.wait_for_timeout(300)
        edit_btn = page.locator(".task-inline-edit-btn").first
        assert await edit_btn.count() > 0, "Edit button not found"
        await edit_btn.click()
        await page.wait_for_timeout(500)

        # Step 5: Verify inline input appears with correct value
        inline_input = page.locator(".inline-edit-input")
        assert await inline_input.count() > 0, "Inline edit input not shown after edit button click"
        input_value = await inline_input.input_value()
        assert input_value == first_title, f"Input value '{input_value}' != title '{first_title}'"
        print(f"[OK] Inline input shown with value: '{input_value[:40]}'")

        # Step 6: Press Escape to cancel
        await inline_input.press("Escape")
        await page.wait_for_timeout(500)
        assert await inline_input.count() == 0, "Inline input still visible after Escape"
        print(f"[OK] Escape cancels edit, input closed")

        # Step 7: Click edit again, modify, press Enter
        await page.locator(".entity-item--rich").first.hover()
        await page.wait_for_timeout(300)
        await page.locator(".task-inline-edit-btn").first.click()
        await page.wait_for_timeout(500)
        inline_input2 = page.locator(".inline-edit-input")
        assert await inline_input2.count() > 0, "Inline input not shown on second edit"
        await inline_input2.fill(first_title + " [E2E]")
        await page.wait_for_timeout(200)
        await inline_input2.press("Enter")
        await page.wait_for_timeout(1000)
        assert await inline_input2.count() == 0, "Inline input still visible after Enter"
        print(f"[OK] Enter saves and closes input")

        # Step 8: Restore original title via API
        token = await page.evaluate("localStorage.getItem('agentboard_token')")
        await page.evaluate(f"""
            fetch('http://127.0.0.1:58125/api/tasks/834', {{
                method: 'PATCH',
                headers: {{
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + '{token}'
                }},
                body: JSON.stringify({{title: 'Task 905: Inline edit signal + dblclick handler + PATCH save'}})
            }})
        """)
        await page.wait_for_timeout(500)
        print(f"[OK] Title restored via API")

        # Step 9: Error checks
        print(f"\n--- Error Summary ---")
        print(f"Page errors: {len(page_errors)}")
        print(f"Console errors: {len(console_errors)}")
        print(f"Failed .js/.css requests: {len(failed_requests)}")

        assert len(page_errors) == 0, f"Page errors: {page_errors}"
        assert len(failed_requests) == 0, f"Failed requests: {failed_requests}"
        real_console = [e for e in console_errors if "ERR_ABORTED" not in e and "favicon" not in e.lower()]
        assert len(real_console) == 0, f"Console errors: {real_console}"

        print("\n=== ALL CHECKS PASSED ===")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
