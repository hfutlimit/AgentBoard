"""P-03 Logo E2E: verify SVG logo renders, gradient text, favicon loads."""
import os, sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8080"
TEST_USER = "admin"
TEST_PASS = "admin123"

def test_p03_logo():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda err: errors.append(f"PAGE ERROR: {err.message}"))

        # Login
        page.goto(f"{BASE}/")
        page.wait_for_timeout(2000)
        try:
            page.click(".auth-tab")
            page.fill("input[name=username]", TEST_USER)
            page.fill("input[name=password]", TEST_PASS)
            page.click(".login-submit")
            page.wait_for_timeout(3000)
        except Exception as e:
            errors.append(f"LOGIN FAILED: {e}")
            browser.close()
            return errors

        # Verify SVG logo exists
        svg_logo = page.evaluate("""() => {
            const svg = document.querySelector('.logo-mark');
            if (!svg) return 'NO_ELEMENT';
            return svg.tagName;
        }""")
        print(f".logo-mark tag: {svg_logo}")
        if svg_logo != "svg":
            errors.append(f"LOGO: .logo-mark is not SVG, found: {svg_logo}")

        # Verify gradient text on logo-text
        logo_gradient = page.evaluate("""() => {
            const el = document.querySelector('.logo-text');
            if (!el) return 'NO_ELEMENT';
            const cs = getComputedStyle(el);
            return {
                bgClip: cs.backgroundClip || cs.webkitBackgroundClip,
                fillColor: cs.webkitTextFillColor || cs.color
            };
        }""")
        print(f".logo-text gradient: {logo_gradient}")
        if logo_gradient != "NO_ELEMENT" and logo_gradient["bgClip"] != "text":
            errors.append(f"LOGO: .logo-text missing gradient bg-clip: {logo_gradient}")

        # Verify favicon loaded
        favicon_href = page.evaluate("""() => {
            const link = document.querySelector('link[rel="icon"]');
            return link ? link.href : 'NO_ELEMENT';
        }""")
        print(f"favicon href: {favicon_href}")
        if "favicon.svg" not in favicon_href:
            errors.append(f"FAVICON: not pointing to favicon.svg: {favicon_href}")

        # Check no console errors
        console_errors = []
        def on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)
        page.on("console", on_console)
        page.reload()
        page.wait_for_timeout(2000)
        for ce in console_errors[:3]:
            errors.append(f"CONSOLE: {ce}")

        os.makedirs("screenshots", exist_ok=True)
        page.screenshot(path="screenshots/p03_logo.png", full_page=True)
        print("Screenshot: screenshots/p03_logo.png")
        browser.close()

    return errors

if __name__ == "__main__":
    errs = test_p03_logo()
    print(f"\n{'='*50}")
    if errs:
        print(f"FAIL: {len(errs)} error(s):")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("PASS: P-03 logo E2E — all checks passed")
