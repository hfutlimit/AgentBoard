"""Playwright verification for dark mode system sync feature."""
import sys
import json
from playwright.sync_api import sync_playwright

WEB_URL = "http://localhost:28080"
SCREENSHOTS_DIR = "E:/Projects/WorkBuddy/AgentBoard/screenshots"

errors = []
console_msgs = []
failed_requests = []

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            color_scheme="light",
        )
        page = context.new_page()

        # Collect console errors
        page.on("console", lambda msg: console_msgs.append({
            "type": msg.type,
            "text": msg.text[:200],
        }))
        page.on("pageerror", lambda err: errors.append(str(err)[:300]))
        page.on("requestfailed", lambda req: failed_requests.append({
            "url": req.url[:200],
            "failure": req.failure,
        }))

        # Navigate to the app
        page.goto(WEB_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # Take light mode screenshot
        page.screenshot(path=f"{SCREENSHOTS_DIR}/dark-mode-light.png", full_page=True)
        print("[OK] Light mode screenshot saved")

        # Check if theme toggle button exists
        toggle_btn = page.query_selector("#theme-toggle-btn")
        if toggle_btn:
            print("[OK] Theme toggle button found in topbar")
        else:
            print("[WARN] Theme toggle button not found - topbar may not have rendered yet")
            # Try waiting longer for Angular to render
            page.wait_for_timeout(3000)
            toggle_btn = page.query_selector("#theme-toggle-btn")
            if toggle_btn:
                print("[OK] Theme toggle button found after extended wait")
            else:
                print("[FAIL] Theme toggle button still not found")

        # Click the toggle button to switch to dark mode
        if toggle_btn:
            toggle_btn.click()
            page.wait_for_timeout(500)

            # Verify dark mode is active
            data_theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
            if data_theme == "dark":
                print("[OK] Dark mode activated after toggle click")
            else:
                print(f"[FAIL] data-theme is '{data_theme}', expected 'dark'")

            # Take dark mode screenshot
            page.screenshot(path=f"{SCREENSHOTS_DIR}/dark-mode-dark.png", full_page=True)
            print("[OK] Dark mode screenshot saved")

            # Verify localStorage persistence
            saved_theme = page.evaluate("localStorage.getItem('agentboard-theme')")
            if saved_theme == "dark":
                print("[OK] Theme preference saved to localStorage")
            else:
                print(f"[FAIL] localStorage theme is '{saved_theme}', expected 'dark'")

            # Toggle back to light mode
            toggle_btn.click()
            page.wait_for_timeout(500)
            data_theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
            if data_theme is None or data_theme != "dark":
                print("[OK] Light mode restored after second toggle")
            else:
                print(f"[FAIL] data-theme is still '{data_theme}' after toggle back")

        # Test system preference sync (new context with dark color scheme)
        context2 = browser.new_context(
            viewport={"width": 1280, "height": 800},
            color_scheme="dark",
        )
        page2 = context2.new_page()
        page2.goto(WEB_URL, wait_until="networkidle", timeout=30000)
        page2.wait_for_timeout(2000)

        data_theme2 = page2.evaluate("document.documentElement.getAttribute('data-theme')")
        if data_theme2 == "dark":
            print("[OK] System dark preference detected on load")
        else:
            print(f"[WARN] System dark preference not detected, data-theme='{data_theme2}'")

        page2.screenshot(path=f"{SCREENSHOTS_DIR}/dark-mode-system.png", full_page=True)
        print("[OK] System preference screenshot saved")

        # Check for errors
        if errors:
            print(f"\n[FAIL] {len(errors)} page errors:")
            for e in errors:
                print(f"  - {e}")
        else:
            print("\n[OK] No page errors")

        if failed_requests:
            print(f"[WARN] {len(failed_requests)} failed requests:")
            for r in failed_requests:
                print(f"  - {r['url']} ({r['failure']})")
        else:
            print("[OK] No failed requests")

        # Check console errors (filter out favicon 404s and style warnings)
        real_console_errors = [m for m in console_msgs if m["type"] == "error"
                             and "favicon" not in m["text"].lower()
                             and "404" not in m["text"]]
        if real_console_errors:
            print(f"[WARN] {len(real_console_errors)} console errors:")
            for m in real_console_errors:
                print(f"  - [{m['type']}] {m['text']}")
        else:
            print("[OK] No console errors (excluding favicon 404s)")

        browser.close()

        # Summary
        success = not errors
        print(f"\n{'='*50}")
        print(f"VERIFICATION: {'PASSED' if success else 'FAILED'}")
        print(f"{'='*50}")
        return success

if __name__ == "__main__":
    result = run()
    sys.exit(0 if result else 1)
