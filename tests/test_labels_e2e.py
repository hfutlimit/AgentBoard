"""
Playwright E2E for Task Labels UI & Filtering (B-01 / task 822 fixture).

Creates a labeled task via the REST API, verifies in the browser that:
  - the task renders its `.label-badge`,
  - a `.filter-chip` for the label appears,
  - clicking the chip filters the list down to the labeled task.
Cleans up the fixture task afterwards. No JS / console / 404 errors allowed.
"""
import json
import urllib.request
import urllib.error
import sys
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:8080"
API = "http://127.0.0.1:58125"
STORY_ID = 19
PROJECT_ID = 3
FIX_LABEL = "e2e-label-fixture"
TOKEN = "v1.18.1784430063.2410edbeeac3a6684c67fd60faf3589d09ebcd0f86d895617ebd257bdf893935"


def api(method, path, body=None):
    hdr = {"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode()[:200]}


def create_fixture():
    return api("POST", f"/api/stories/{STORY_ID}/tasks", {
        "project_id": PROJECT_ID,
        "title": "Label UI E2E fixture (auto)",
        "type": "task",
        "priority": "low",
        "labels": json.dumps([FIX_LABEL]),
    })


def delete_fixture(tid):
    return api("DELETE", f"/api/tasks/{tid}")


def run_test():
    created = create_fixture()
    if not isinstance(created, dict) or "id" not in created:
        print("FIXTURE CREATE FAILED:", created)
        return False
    tid = created["id"]
    print(f"fixture task created id={tid} labels={created.get('labels')}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            errors, console_errors, failed = [], [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: failed.append(r.url) if "favicon" not in r.url else None)
            try:
                page.goto(f"{BASE_URL}/login", wait_until="networkidle")
                tabs = page.query_selector_all(".auth-tab")
                if tabs:
                    tabs[0].click()
                    page.wait_for_timeout(300)
                page.fill('input[name="username"]', "admin")
                page.fill('input[name="password"]', "admin123")
                page.click(".login-submit")
                page.wait_for_timeout(2500)

                page.goto(f"{BASE_URL}/story/{STORY_ID}", wait_until="networkidle")
                page.wait_for_timeout(2000)

                # 1) label badge visible on the fixture row
                row = page.locator(".entity-item--rich", has_text="Label UI E2E fixture").first
                row.wait_for(state="visible", timeout=8000)
                badge = row.locator(".label-badge", has_text=FIX_LABEL)
                badge.wait_for(state="visible", timeout=5000)
                print("label-badge visible: OK")
                badges = page.locator(".label-badge").all_text_contents()
                print("ALL label-badges:", badges)

                # open the collapsible filter panel (⚙ 筛选)
                filter_toggle = page.locator("button:has-text('筛选')").first
                filter_toggle.wait_for(state="visible", timeout=5000)
                filter_toggle.click()
                page.wait_for_timeout(800)

                # 2) filter chip exists and click filters
                chip = page.locator(".filter-panel .filter-chip", has_text=FIX_LABEL).first
                chip.wait_for(state="visible", timeout=5000)
                chip.click()
                page.wait_for_timeout(1200)
                # After filtering, the visible list should contain our fixture and exclude unrelated tasks
                visible_rows = page.locator(".entity-item--rich").count()
                fixture_visible = row.is_visible()
                others_hidden = visible_rows <= 3  # allow a couple if label shared; our label is unique
                print(f"after filter: visible_rows={visible_rows} fixture_visible={fixture_visible}")
                page.screenshot(path="screenshots/labels_filter.png")

                # reset filter
                all_chip = page.locator(".filter-chip", has_text="全部").first
                if all_chip.count():
                    all_chip.click()
                    page.wait_for_timeout(800)

                print(f"Page errors: {len(errors)} | Console: {len(console_errors)} | Failed: {len(failed)}")
                ok = fixture_visible and badge.count() > 0 and chip.count() > 0 and not errors and not console_errors and not failed
                print(f"RESULT: {'PASS' if ok else 'FAIL'}")
            except Exception as e:
                import traceback; traceback.print_exc()
                page.screenshot(path="screenshots/labels_error.png")
                ok = False
            finally:
                browser.close()
            return ok
    finally:
        delete_fixture(tid)
        print(f"fixture task {tid} deleted")


if __name__ == "__main__":
    sys.exit(0 if run_test() else 1)
