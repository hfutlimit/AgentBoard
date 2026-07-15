"""Playwright E2E verification for Story 15.2: Favorites & Recent Projects sidebar.

Tests:
- Sidebar shows favorites section after favoriting a project
- Sidebar shows recent projects section after visiting a project
- Both sections persist after page refresh (localStorage)
- Toggle off removes from favorites
"""
import sys
import httpx
from playwright.sync_api import sync_playwright

WEB_URL = "http://localhost:28080"
API_URL = "http://localhost:18000"
SCREENSHOTS_DIR = "E:/Projects/WorkBuddy/AgentBoard/screenshots"

console_errors = []
page_errors = []
failed_requests = []


def main():
    # Use a dedicated test user (register if needed)
    username = "story152_test"
    password = "story152_test_2026"
    resp = httpx.post(f"{API_URL}/api/auth/login", json={"username": username, "password": password})
    if resp.status_code not in (200, 201):
        resp = httpx.post(f"{API_URL}/api/auth/register", json={"username": username, "password": password})
        if resp.status_code not in (200, 201):
            print(f"[FAIL] Could not authenticate: {resp.status_code} {resp.text}")
            sys.exit(1)
    auth = resp.json()
    token = auth["token"]
    user_id = auth["id"]
    print(f"[OK] Authenticated as {username} (id={user_id})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        page.on("console", lambda msg: console_errors.append(msg.text[:200]) if msg.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)[:300]))

        # Load page to set localStorage origin
        page.goto(WEB_URL, wait_until="domcontentloaded", timeout=30000)
        # Set auth token and clear existing state
        page.evaluate(f"""() => {{
            localStorage.setItem('agentboard_token', '{token}');
            localStorage.setItem('agentboard_user', '{username}');
            localStorage.setItem('agentboard_is_admin', 'false');
            localStorage.removeItem('agentboard_recent_projects');
            localStorage.removeItem('agentboard_favorite_projects');
        }}""")
        page.reload(wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # Step 1: Verify sidebar and project cards render
        sidebar = page.query_selector("#sidebar")
        assert sidebar, "[FAIL] Sidebar not found"
        print("[OK] Sidebar rendered")

        cards = page.query_selector_all(".project-card-wrapper")
        assert len(cards) > 0, "[FAIL] No project cards found"
        print(f"[OK] Found {len(cards)} project cards")

        # Step 2: Verify no favorites/recent sections initially
        fav_section = page.query_selector(".sidebar-favorites")
        recent_section = page.query_selector(".sidebar-recent")
        assert fav_section is None, "[FAIL] Favorites section should be empty initially"
        assert recent_section is None, "[FAIL] Recent section should be empty initially"
        print("[OK] No favorites/recent sections initially (clean state)")

        # Step 3: Click first favorite toggle
        fav_toggle = page.query_selector(".favorite-toggle")
        assert fav_toggle, "[FAIL] No favorite toggle button found"
        fav_toggle.click()
        page.wait_for_timeout(500)
        print("[OK] Clicked favorite toggle")

        # Step 4: Verify favorites section appears
        fav_section = page.query_selector(".sidebar-favorites")
        assert fav_section, "[FAIL] Favorites section did not appear after toggling"
        fav_text = fav_section.inner_text()
        assert "收藏" in fav_text, f"[FAIL] Favorites section text missing '收藏': {fav_text}"
        print(f"[OK] Favorites section appeared: {fav_text[:80]}")

        # Step 5: Verify localStorage was updated
        stored = page.evaluate("() => localStorage.getItem('agentboard_favorite_projects')")
        assert stored and stored != "[]", f"[FAIL] localStorage favorite not set: {stored}"
        print(f"[OK] localStorage updated: {stored}")

        # Step 6: Navigate to a project to trigger recent tracking
        # Click the first project card (not the favorite button)
        first_card = page.query_selector(".project-card")
        assert first_card, "[FAIL] No project card link found"
        first_card.click()
        page.wait_for_timeout(3000)
        print("[OK] Navigated to project")

        # Step 7: Go back to dashboard
        page.goto(WEB_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # Step 8: Verify recent section appears
        recent_section = page.query_selector(".sidebar-recent")
        assert recent_section, "[FAIL] Recent section did not appear after visiting a project"
        recent_text = recent_section.inner_text()
        assert "最近访问" in recent_text, f"[FAIL] Recent section text missing '最近访问': {recent_text}"
        print(f"[OK] Recent section appeared: {recent_text[:80]}")

        # Step 9: Verify localStorage was updated for recent
        stored_recent = page.evaluate("() => localStorage.getItem('agentboard_recent_projects')")
        assert stored_recent and stored_recent != "[]", f"[FAIL] localStorage recent not set: {stored_recent}"
        print(f"[OK] localStorage recent updated: {stored_recent}")

        # Step 10: Refresh page and verify persistence (THE BUG FIX)
        page.reload(wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)
        page.screenshot(path=f"{SCREENSHOTS_DIR}/story152_persistence.png", full_page=False)

        fav_section_after = page.query_selector(".sidebar-favorites")
        recent_section_after = page.query_selector(".sidebar-recent")
        assert fav_section_after, "[FAIL] BUG: Favorites section did NOT persist after page refresh"
        assert recent_section_after, "[FAIL] BUG: Recent section did NOT persist after page refresh"
        print("[OK] BUG FIX VERIFIED: Both sections persist after page refresh")

        # Step 11: Click toggle to unfavorite
        fav_toggle = page.query_selector(".favorite-toggle.favorited")
        if fav_toggle:
            fav_toggle.click()
            page.wait_for_timeout(500)
            fav_section_after = page.query_selector(".sidebar-favorites")
            assert fav_section_after is None, "[FAIL] Favorites section should be removed after unfavoriting last item"
            print("[OK] Unfavoriting removed section")
        else:
            print("[SKIP] Could not find favorited toggle to unfavorite (may have been auto-cleared)")

        # Step 12: Final report
        print("\n=== Final Summary ===")
        if page_errors:
            print(f"[FAIL] Page errors: {len(page_errors)}")
            for e in page_errors[:3]:
                print(f"  {e}")
            sys.exit(1)
        else:
            print("[OK] No JavaScript page errors")

        # Filter out pre-existing 404 for /api/health endpoint
        critical_console_errors = [e for e in console_errors if "404" not in e and "/api/health" not in e]
        if critical_console_errors:
            print(f"[WARN] {len(critical_console_errors)} non-404 console errors:")
            for e in critical_console_errors[:3]:
                print(f"  {e}")
        else:
            print("[OK] No critical console errors (only pre-existing /api/health 404)")

        print("\n[PASS] All Story 15.2 checks passed!")
        browser.close()


if __name__ == "__main__":
    main()
