import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:28080"
PROJECT_ID = 3

def main():
    api_calls = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page()
        pg.on("response", lambda r: api_calls.append((r.url, r.status)) if "/api/documents" in r.url else None)
        pg.goto(BASE + "/", wait_until="networkidle")
        if pg.locator(".auth-tab.register").count() > 0:
            pg.locator(".auth-tab.register").click()
        pg.fill("input[name=username]", "admin")
        pg.fill("input[name=password]", "admin123")
        pg.click(".login-submit")
        pg.wait_for_timeout(1500)
        pg.goto(BASE + f"/project/{PROJECT_ID}", wait_until="networkidle")
        pg.wait_for_timeout(1200)
        pg.locator("button.tab-btn", has_text="文档").first.click()
        pg.wait_for_timeout(3000)
        print("API /documents calls:")
        for u, s in api_calls:
            print("  ", s, u)
        print("doc rows:", pg.locator(".doc-list .doc-row").count())
        print("empty states:", pg.locator(".empty-state").count())
        b.close()

if __name__ == "__main__":
    main()
