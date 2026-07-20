import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:28080"
PROJECT_ID = 3

def main():
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE + "/", wait_until="networkidle")
        # login (admin/admin123 created in prior run)
        pg.fill("input[name=username]", "admin")
        pg.fill("input[name=password]", "admin123")
        pg.click(".login-submit")
        pg.wait_for_timeout(1500)
        if "/login" in pg.url:
            # fallback: register then proceed
            if pg.locator(".auth-tab.register").count() > 0:
                pg.locator(".auth-tab.register").click()
            pg.fill("input[name=username]", "admin")
            pg.fill("input[name=password]", "admin123")
            pg.click(".login-submit")
            pg.wait_for_timeout(1500)
        pg.goto(BASE + f"/project/{PROJECT_ID}", wait_until="networkidle")
        pg.wait_for_timeout(1200)
        pg.locator("button.tab-btn", has_text="文档").first.click()
        pg.wait_for_timeout(2500)
        print("before create, doc rows:", pg.locator(".doc-list .doc-row").count())
        # open create form
        pg.locator("button:has-text('新建文档')").first.click()
        pg.wait_for_timeout(800)
        print("create form present:", pg.locator("form.doc-create").count())
        pg.fill("input[placeholder='文档标题']", "Tab 文档验证 Demo")
        pg.select_option("form.doc-create select", "design")
        pg.fill("form.doc-create textarea", "# 标题\n这是通过项目 Tab 创建的文档。\n\n- 支持 **Markdown**\n- 支持列表")
        pg.locator("form.doc-create button:has-text('创建')").first.click()
        pg.wait_for_timeout(2500)
        rows = pg.locator(".doc-list .doc-row").count()
        print("after create, doc rows:", rows)
        if rows > 0:
            print("first row title:", pg.locator(".doc-list .doc-row .doc-row-title").first.inner_text())
            # open detail
            pg.locator(".doc-list .doc-row").first.click()
            pg.wait_for_timeout(1500)
            print("detail content present:", pg.locator(".doc-content").count())
        print("PAGE ERRORS:", errs)
        b.close()

if __name__ == "__main__":
    main()
