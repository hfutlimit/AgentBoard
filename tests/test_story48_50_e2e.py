"""
Playwright E2E test for Story 48 (Task detail enhancements) + Story 50 (Comments & members).
"""
from playwright.sync_api import sync_playwright
import sys

BASE_URL = "http://localhost:8080"

def run_test():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        errors = []
        console_errors = []
        failed_requests = []

        page.on("pageerror", lambda err: errors.append(str(err)))
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("requestfailed", lambda req: failed_requests.append(req.url) if "favicon" not in req.url else None)

        step = 0
        try:
            # Step 1: Login
            step = 1
            page.goto(f"{BASE_URL}/login", wait_until="networkidle")
            tabs = page.query_selector_all(".auth-tab")
            if tabs:
                tabs[0].click()  # Login tab
                page.wait_for_timeout(300)
            page.fill('input[name="username"]', "admin")
            page.fill('input[name="password"]', "admin123")
            page.click(".login-submit")
            page.wait_for_timeout(3000)
            print("Step 1: Login - OK")

            # Step 2: Navigate directly to AgentBoard project (id=3)
            step = 2
            page.goto(f"{BASE_URL}/project/3", wait_until="networkidle")
            page.wait_for_timeout(2000)
            print(f"Step 2: Navigate to project 3 - URL: {page.url}")

            # Step 3: Check for epics in the project
            step = 3
            epic_links = page.query_selector_all("a[href*='/epic/']")
            print(f"Step 3: Epic links: {len(epic_links)}")

            # Step 4: Click on first epic to see stories
            step = 4
            if epic_links:
                epic_links[0].click()
                page.wait_for_timeout(2000)
                print(f"Step 4: Clicked epic - URL: {page.url}")

            # Step 5: Look for story links
            step = 5
            story_links = page.query_selector_all("a[href*='/story/']")
            print(f"Step 5: Story links: {len(story_links)}")

            # Step 6: Click on first story to see tasks
            step = 6
            if story_links:
                story_links[0].click()
                page.wait_for_timeout(2000)
                print(f"Step 6: Clicked story - URL: {page.url}")

            # Step 7: Check task list
            step = 7
            task_links = page.query_selector_all("a[href*='/task/']")
            print(f"Step 7: Task links: {len(task_links)}")

            # Check assignee avatars in task list (Task 818)
            assignee_avatars = page.query_selector_all(".assignee-avatar-sm")
            print(f"  Assignee avatars in list: {len(assignee_avatars)}")

            # Step 8: Click on first task to see detail
            step = 8
            if task_links:
                task_links[0].click()
                page.wait_for_timeout(2000)
                print(f"Step 8: Clicked task - URL: {page.url}")
                page.screenshot(path="screenshots/test_task_detail.png")

                # Task 809: Breadcrumb
                crumb = page.query_selector(".crumb-bar")
                if crumb:
                    crumb_text = crumb.text_content()
                    print(f"  Task 809 - Breadcrumb: {crumb_text[:100]}")
                else:
                    print("  Task 809 - Breadcrumb: NOT found")

                # Task 810: Meta bar with assignee/creator/update time
                meta = page.query_selector(".task-meta-bar")
                if meta:
                    meta_text = meta.text_content()
                    has_assignee = "负责人" in meta_text or "未指派" in meta_text
                    has_created = "创建" in meta_text
                    has_updated = "更新" in meta_text
                    print(f"  Task 810 - Meta: assignee={has_assignee} created={has_created} updated={has_updated}")
                    print(f"  Meta text: {meta_text[:200]}")
                else:
                    print("  Task 810 - Meta bar: NOT found")

                # Task 811: Subtask progress bar
                pb = page.query_selector_all(".subtask-progress-bar")
                pf = page.query_selector_all(".subtask-progress-fill")
                print(f"  Task 811 - Progress bars: {len(pb)} bars, {len(pf)} fills")

                # Task 812: Related tasks
                rt = page.query_selector(".related-tasks-card")
                print(f"  Task 812 - Related tasks: {'found' if rt else 'not found (no deps)'}")

                # Task 816: Comment preview toggle
                btns = page.query_selector_all("button")
                has_preview = False
                for b in btns:
                    t = b.text_content() or ""
                    if "预览" in t or "编辑" in t:
                        has_preview = True
                        break
                print(f"  Task 816 - Comment preview: {has_preview}")
            else:
                print("Step 8: No task links found - trying to navigate to a known task")
                # Try direct navigation to a task
                page.goto(f"{BASE_URL}/task/809", wait_until="networkidle")
                page.wait_for_timeout(2000)
                print(f"  Direct nav to task 809 - URL: {page.url}")
                page.screenshot(path="screenshots/test_task_809.png")

                crumb = page.query_selector(".crumb-bar")
                meta = page.query_selector(".task-meta-bar")
                print(f"  Breadcrumb: {'found' if crumb else 'NOT found'}")
                print(f"  Meta bar: {'found' if meta else 'NOT found'}")
                if meta:
                    print(f"  Meta: {meta.text_content()[:200]}")

            # Step 9: Check member avatars (Task 817) - go back to project
            step = 9
            page.goto(f"{BASE_URL}/project/3", wait_until="networkidle")
            page.wait_for_timeout(1500)
            member_avatars = page.query_selector_all(".member-avatar")
            print(f"Step 9: Member avatars: {len(member_avatars)}")

            # Step 10: Check empty epic guide (Task 819)
            step = 10
            # This would only show for projects with no epics
            empty_guide = page.query_selector(".empty-state-guide")
            print(f"Step 10: Empty epic guide: {'found' if empty_guide else 'not found (epics exist)'}")

            # Summary
            print(f"\n=== VALIDATION SUMMARY ===")
            print(f"Page errors: {len(errors)}")
            print(f"Console errors: {len(console_errors)}")
            print(f"Failed requests: {len(failed_requests)}")
            if errors:
                print(f"Errors: {errors[:3]}")
            if console_errors:
                print(f"Console errors: {console_errors[:3]}")
            if failed_requests:
                print(f"Failed requests: {failed_requests[:5]}")

            passed = len(errors) == 0 and len(console_errors) == 0 and len(failed_requests) == 0
            print(f"\nRESULT: {'PASS' if passed else 'FAIL'}")

        except Exception as e:
            print(f"Error at step {step}: {e}")
            import traceback
            traceback.print_exc()
            page.screenshot(path="screenshots/test_error.png")
            print(f"\nRESULT: FAIL")

        browser.close()
        return True

if __name__ == "__main__":
    run_test()
