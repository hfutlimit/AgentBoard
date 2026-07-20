import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8080"
STORY = "/story/72"

console_errors = []
page_errors = []
failed_requests = []

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        # 1) Load app
        page.goto(BASE, wait_until="networkidle", timeout=30000)

        # 2) Handle login (register admin/admin123 if needed)
        if "/login" in page.url or page.query_selector(".login-submit") is not None:
            reg = page.query_selector(".auth-tab")
            if reg is not None:
                reg.click()
            page.fill("input[name=username]", "admin")
            page.fill("input[name=password]", "admin123")
            page.click(".login-submit", timeout=10000)
            page.wait_for_timeout(2000)

        # 3) Navigate to story task list
        page.goto(BASE + STORY, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)

        # 4) Wait for status chips (.qf-chip with status group right after priority group)
        chips = page.query_selector_all(".task-quickfilter-bar .qf-chip")
        print(f"TOTAL_QF_CHIPS={len(chips)}")

        # Find the status chip bar (second .task-quickfilter-bar)
        bars = page.query_selector_all(".task-quickfilter-bar")
        print(f"QF_BARS={len(bars)}")
        status_bar = bars[1] if len(bars) > 1 else (bars[0] if bars else None)
        status_labels = []
        if status_bar:
            for c in status_bar.query_selector_all(".qf-chip"):
                txt = c.inner_text().replace("\n", " ").strip()
                status_labels.append(txt)
        print("STATUS_CHIPS=" + " | ".join(status_labels))

        # 5) Verify '全部' count and a non-zero status chip (待办池/backlog)
        all_chip = status_bar.query_selector_all(".qf-chip")[0] if status_bar else None
        all_count = all_chip.inner_text().strip() if all_chip else "?"
        print("ALL_CHIP=" + all_count)

        # 6) Click '待办池' (backlog) chip and verify it becomes active + list filters
        backlog_chip = None
        for c in (status_bar.query_selector_all(".qf-chip") if status_bar else []):
            if "待办池" in c.inner_text():
                backlog_chip = c
                break
        visible_before = page.query_selector_all(".task-row, .task-card, tr.task, .task-item")
        print(f"VISIBLE_TASKS_BEFORE={len(visible_before)}")
        if backlog_chip is not None:
            backlog_chip.click()
            page.wait_for_timeout(800)
            print("BACKLOG_ACTIVE=" + str("active" in (backlog_chip.get_attribute("class") or "")))
            visible_after = page.query_selector_all(".task-row, .task-card, tr.task, .task-item")
            print(f"VISIBLE_TASKS_AFTER_BACKLOG={len(visible_after)}")

        # 7) Click '进行中' (in_progress) -> should yield 0 tasks in this story
        inprog_chip = None
        for c in (status_bar.query_selector_all(".qf-chip") if status_bar else []):
            if "进行中" in c.inner_text():
                inprog_chip = c
                break
        if inprog_chip is not None:
            inprog_chip.click()
            page.wait_for_timeout(800)
            print("INPROG_ACTIVE=" + str("active" in (inprog_chip.get_attribute("class") or "")))
            visible_inprog = page.query_selector_all(".task-row, .task-card, tr.task, .task-item")
            print(f"VISIBLE_TASKS_AFTER_INPROGRESS={len(visible_inprog)}")

        # 8) Screenshot
        page.screenshot(path="e2e_status_chips.png", full_page=False)

        # 9) Report errors
        js_css_failed = [u for u in failed_requests if u.endswith(".js") or u.endswith(".css")]
        print("CONSOLE_ERRORS=" + str(len(console_errors)))
        for e in console_errors[:10]:
            print("  ERR: " + e[:200])
        print("PAGE_ERRORS=" + str(len(page_errors)))
        for e in page_errors[:10]:
            print("  PE: " + e[:200])
        print("FAILED_REQ(js/css)=" + str(len(js_css_failed)))
        for u in js_css_failed[:10]:
            print("  FR: " + u)

        browser.close()

        ok = len(chips) >= 7 and len(bars) >= 2 and len(console_errors) == 0 and len(page_errors) == 0 and len(js_css_failed) == 0
        print("RESULT=" + ("PASS" if ok else "FAIL"))
        sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
