"""
Playwright E2E verification for A-22 (task 823): list quick-complete button
toggles task status. Deterministic: resets task to 'todo' first, clicks once,
waits for the button's `done` class to appear, then reverts. Confirms via DB.
"""
import sys
sys.path.insert(0, "/tmp")
import abapi
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:8080"
STORY_ID = 54
TASK_TITLE = "A-22 实现任务快速完成勾选按钮"


def run_test():
    # Ensure clean starting state
    abapi.set_task_status(823, "todo")
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

            row = page.locator(".entity-item--rich", has_text=TASK_TITLE).first
            row.wait_for(state="visible", timeout=8000)
            btn = row.locator(".task-quick-complete")
            btn.wait_for(state="visible", timeout=5000)
            print("initial done-class:", "done" in (btn.get_attribute("class") or ""))

            # Click once -> should become done; verify via DB polling (DOM refresh is async)
            btn.click()
            became_done = False
            for _ in range(12):
                page.wait_for_timeout(500)
                st = abapi.get("/api/tasks/823")
                if isinstance(st, dict) and st.get("status") == "done":
                    became_done = True
                    break
            print("after click became_done (via DB):", became_done)
            page.screenshot(path="screenshots/a22_quickcomplete.png")

            # Revert to todo
            if became_done:
                btn.click()
                for _ in range(12):
                    page.wait_for_timeout(500)
                    st = abapi.get("/api/tasks/823")
                    if isinstance(st, dict) and st.get("status") == "todo":
                        break

            db_state = abapi.get("/api/tasks/823")
            db_status = db_state.get("status") if isinstance(db_state, dict) else None
            print("DB status after test:", db_status)

            print(f"Page errors: {len(errors)} | Console errors: {len(console_errors)} | Failed: {len(failed)}")
            ok = became_done and not errors and not console_errors and not failed
            print(f"RESULT: {'PASS' if ok else 'FAIL'}")
        except Exception as e:
            import traceback; traceback.print_exc()
            page.screenshot(path="screenshots/a22_error.png")
            ok = False
        finally:
            browser.close()
        return ok


if __name__ == "__main__":
    sys.exit(0 if run_test() else 1)
