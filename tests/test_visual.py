"""
Visual regression test for AgentBoard UI.
Usage: python tests/test_visual.py [--url URL] [--output PATH] [--pages PAGE...]
Takes full-page screenshots of key pages for visual verification.
Auto-logins before screenshotting authenticated pages.
"""
import argparse
import sys
import os
import time
from pathlib import Path

# Ensure project root is on path for agentboard imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:58123"
OUTPUT_DIR = Path(__file__).parent.parent / "tests" / "screenshots"
TEST_USER = "jzhong"
TEST_PASS = "12345678"

PAGES = {
    "settings": "/project/1",
    "dashboard": "/",
    "projects": "/projects",
}


def login(page, base_url: str):
    """Auto-login via API to get token, then inject into localStorage."""
    import httpx
    # API is on a different port than the web frontend
    api_url = os.environ.get("AGENTBOARD_API_URL", base_url.replace(":58123", ":58124"))
    # Login via REST API to get token
    resp = httpx.post(f"{api_url}/api/auth/login", json={
        "username": TEST_USER,
        "password": TEST_PASS,
    }, timeout=5)
    data = resp.json()
    token = data.get("access_token") or data.get("token", "")
    if not token:
        raise RuntimeError(f"Login failed: {data}")
    print(f"  Logged in as {TEST_USER} via {api_url}, injecting token...")
    # Navigate first so we can set localStorage
    page.goto(base_url + "/", wait_until="domcontentloaded", timeout=10000)
    page.evaluate(f"""(token) => {{
        localStorage.setItem('agentboard_token', token);
        localStorage.setItem('agentboard_user', '{TEST_USER}');
    }}""", token)
    # Reload to pick up the token
    page.goto(base_url + "/", wait_until="networkidle", timeout=15000)
    time.sleep(0.3)


def screenshot_page(page, name: str, url: str, output_dir: Path) -> Path:
    """Take a full-page screenshot of a page."""
    print(f"  Screenshotting {name}: {url}")
    page.goto(url, wait_until="networkidle", timeout=15000)
    
    # If targeting settings tab, click it
    if name == "settings":
        try:
            settings_tab = page.locator("text=设置").first
            if settings_tab.is_visible(timeout=3000):
                settings_tab.click()
                page.wait_for_load_state("networkidle", timeout=10000)
                time.sleep(0.3)
                print(f"  Clicked '设置' tab")
        except Exception as e:
            print(f"  WARN: Could not click settings tab: {e}")
    
    # Small delay to let any animations settle
    time.sleep(0.5)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    size = path.stat().st_size
    print(f"  Saved: {path} ({size} bytes)")
    return path


def check_visual_issues(page) -> list[str]:
    """Check common visual issues via JS evaluation."""
    issues = []
    # Only check main content area, skip login overlay elements
    overflow = page.evaluate("""() => {
        const els = document.querySelectorAll('.app-content *, .main *');
        const problems = [];
        els.forEach(el => {
            if (el.scrollWidth > el.clientWidth + 3 || el.scrollHeight > el.clientHeight + 3) {
                const tag = el.tagName.toLowerCase();
                const cls = el.className ? String(el.className).toString().slice(0, 40) : '';
                problems.push(tag + '.' + cls);
            }
        });
        return problems.slice(0, 8);
    }""")
    if overflow:
        issues.append(f"[OVERFLOW] Content overflow: {', '.join(overflow[:4])}")
    return issues


def run(pages: list[str] | None = None, url: str = BASE_URL, output_dir: Path | None = None):
    """Run visual tests on specified pages."""
    output_dir = output_dir or OUTPUT_DIR
    pages = pages or list(PAGES.keys())
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # Auto-login first
        login(page, url)

        all_ok = True
        for name in pages:
            if name not in PAGES:
                print(f"SKIP: Unknown page '{name}'")
                continue
            try:
                path = screenshot_page(page, name, url + PAGES[name], output_dir)
                issues = check_visual_issues(page)
                results.append({"page": name, "path": str(path), "issues": issues})
                if issues:
                    all_ok = False
                    for issue in issues:
                        print(f"  WARN: {issue}")
            except Exception as e:
                print(f"  FAIL: {name} - {e}")
                results.append({"page": name, "error": str(e)})
                all_ok = False

        browser.close()

    print(f"\n{'='*50}")
    print(f"Visual test: {'PASS' if all_ok else 'ISSUES FOUND'}")
    for r in results:
        status = "OK" if not r.get("issues") and not r.get("error") else "WARN"
        print(f"  [{status}] {r['page']} -> {r.get('path', r.get('error', 'N/A'))}")
    return all_ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AgentBoard Visual Test")
    parser.add_argument("--url", default=BASE_URL, help="Base URL")
    parser.add_argument("--output", default=None, help="Output directory")
    parser.add_argument("--pages", nargs="+", default=None, help="Pages to test")
    args = parser.parse_args()
    ok = run(pages=args.pages, url=args.url, output_dir=Path(args.output) if args.output else None)
    sys.exit(0 if ok else 1)
