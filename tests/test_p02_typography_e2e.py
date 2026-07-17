"""P-02 Typography E2E: verify Inter/JetBrains Mono loading, heading letter-spacing, tabular-nums."""
import os, sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8080"
TEST_USER = "admin"
TEST_PASS = "admin123"

def test_p02_typography():
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda err: errors.append(f"PAGE ERROR: {err.message}"))

        # 1. Login
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

        # 2. Dashboard — check font loading + heading letter-spacing
        page.goto(f"{BASE}/#/dashboard")
        page.wait_for_timeout(2000)

        font_family = page.evaluate("() => getComputedStyle(document.body).fontFamily")
        print(f"Body font-family: {font_family}")
        if "Inter" not in font_family:
            errors.append(f"FONT: Inter not found: {font_family}")

        h2_ls = page.evaluate("() => getComputedStyle(document.querySelector('h2')).letterSpacing")
        print(f"h2 letter-spacing: {h2_ls}")
        if h2_ls == "normal" or h2_ls == "0px":
            errors.append(f"TYPOG: h2 letter-spacing is '{h2_ls}'")

        # 3. Inject test elements to verify CSS rules
        result = page.evaluate("""() => {
            // Test .stat-value
            const sv = document.createElement('div');
            sv.className = 'stat-value';
            sv.style.position = 'fixed';
            sv.style.left = '-9999px';
            document.body.appendChild(sv);
            const sv_tn = getComputedStyle(sv).fontVariantNumeric;
            sv.remove();

            // Test .project-key
            const pk = document.createElement('span');
            pk.className = 'project-key';
            pk.style.position = 'fixed';
            pk.style.left = '-9999px';
            document.body.appendChild(pk);
            const pk_ff = getComputedStyle(pk).fontFamily;
            pk.remove();

            // Test code element
            const cd = document.createElement('code');
            cd.style.position = 'fixed';
            cd.style.left = '-9999px';
            document.body.appendChild(cd);
            const cd_ff = getComputedStyle(cd).fontFamily;
            cd.remove();

            return {statValueTN: sv_tn, projectKeyFont: pk_ff, codeFont: cd_ff};
        }""")
        print(f"Injected CSS check: {result}")
        if result["statValueTN"] != "tabular-nums":
            errors.append(f"CSS: .stat-value tabular-nums = {result['statValueTN']}")
        if "monospace" not in result["projectKeyFont"]:
            errors.append(f"CSS: .project-key monospace = {result['projectKeyFont']}")
        if "monospace" not in result["codeFont"]:
            errors.append(f"CSS: code monospace = {result['codeFont']}")

        # 4. Check no console errors
        console_errors = []
        def on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)
        page.on("console", on_console)
        page.reload()
        page.wait_for_timeout(3000)
        for ce in console_errors[:3]:
            errors.append(f"CONSOLE: {ce}")

        os.makedirs("screenshots", exist_ok=True)
        page.screenshot(path="screenshots/p02_typography.png", full_page=True)
        print("Screenshot: screenshots/p02_typography.png")
        browser.close()

    return errors

if __name__ == "__main__":
    errs = test_p02_typography()
    print(f"\n{'='*50}")
    if errs:
        print(f"FAIL: {len(errs)} error(s):")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("PASS: P-02 typography E2E — all checks passed")
