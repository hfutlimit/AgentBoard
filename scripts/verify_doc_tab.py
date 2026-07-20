import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:28080"
PROJECT_ID = 3  # AgentBoard

def main():
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page()
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))
        pg.goto(BASE + "/", wait_until="networkidle")
        # login/register flow
        if "/login" in pg.url or pg.locator(".auth-tab").count() > 0:
            if pg.locator(".auth-tab.register").count() > 0:
                pg.locator(".auth-tab.register").click()
            pg.fill("input[name=username]", "admin")
            pg.fill("input[name=password]", "admin123")
            pg.click(".login-submit")
            pg.wait_for_timeout(1500)
        # go to project
        pg.goto(BASE + f"/project/{PROJECT_ID}", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        # click 文档 tab
        tab = pg.locator("button.tab-btn", has_text="文档")
        assert tab.count() > 0, "文档 tab button not found"
        tab.first.click()
        pg.wait_for_timeout(2500)
        # verify tab content present
        content = pg.locator(".tab-content")
        print("tab-content count:", content.count())
        # check either list, empty, or skeleton
        has_list = pg.locator(".doc-list .doc-row").count()
        has_empty = pg.locator(".empty-state").count()
        head = pg.locator(".section-header h3").first.inner_text() if pg.locator(".section-header h3").count() else "(none)"
        print("section header:", head)
        print("doc rows:", has_list, "empty states:", has_empty)
        # new doc button present
        new_btn = pg.locator("button:has-text('新建文档')")
        print("new doc button:", new_btn.count())
        # error filter (ignore benign api ERR_ABORTED during nav)
        real_errors = [e for e in errors if "ERR_ABORTED" not in e and "/api/" not in e]
        print("CONSOLE ERRORS (filtered):", real_errors)
        b.close()
    print("DONE")

if __name__ == "__main__":
    main()
