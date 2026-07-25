"""
E2E: Task 816 — 评论 Markdown 实时预览切换
验收:
  1) 评论输入框有「编辑/预览」切换按钮
  2) 预览模式渲染 Markdown（**粗体** → <strong>、列表 → <ul><li>、链接 → <a>）
零 JS 报错 / 控制台错误 / .js+.css 404。
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:18000"
BASE = "http://127.0.0.1:28080"   # docker web 容器，直读 agentboard/web/static 挂载
TASK_ID = 816
MD = "**加粗标题** 与 *斜体* 文本\n\n- 列表项 A\n- 列表项 B\n\n访问 [AgentBoard](https://example.com) 了解更多。`行内代码` 示例。"

console_errors = []
page_errors = []
failed_requests = []


def api_login():
    body = json.dumps({"username": "admin", "password": "admin123"}).encode()
    req = urllib.request.Request(
        f"{API}/api/auth/login", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())["token"]


def main():
    token = api_login()
    print(f"[login] admin token len={len(token)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()

        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed", lambda r: failed_requests.append(r.url))

        # 注入 token 跳过登录
        page.add_init_script(
            f"localStorage.setItem('agentboard_token', '{token}');")

        page.goto(f"{BASE}/task/{TASK_ID}", wait_until="networkidle")
        page.wait_for_selector(".comments-card", timeout=15000)
        print("[nav] task detail + comments-card rendered")

        # 1) 切换按钮存在
        toggle = page.locator(".comments-card .section-header button")
        assert toggle.count() >= 1, "评论区缺少编辑/预览切换按钮"
        btn_text_before = toggle.first.inner_text()
        print(f"[ui] toggle button text (edit mode) = {btn_text_before!r}")

        # 填入 Markdown（#cContent 是 Angular 模板引用变量，非 DOM id；用表单内 textarea 定位）
        ta = page.locator("form#comment-form textarea[name='content']")
        ta.fill(MD)
        print("[ui] filled comment textarea with markdown")

        # 切到预览
        toggle.first.click()
        page.wait_for_selector(".comment-preview strong", timeout=8000)
        btn_text_after = toggle.first.inner_text()
        print(f"[ui] toggle button text (preview mode) = {btn_text_after!r}")
        assert "编辑" in btn_text_after, "点击后按钮未切换到编辑态"

        # 2) 预览渲染 Markdown
        preview = page.locator(".comment-preview")
        html = preview.inner_html()
        print(f"[render] preview html (first 400) = {html[:400]!r}")

        strong = page.locator(".comment-preview strong")
        em = page.locator(".comment-preview em")
        ul = page.locator(".comment-preview ul")
        li = page.locator(".comment-preview ul li")
        code = page.locator(".comment-preview code")
        a = page.locator(".comment-preview a")

        assert strong.count() >= 1, "预览未渲染 <strong> (粗体)"
        assert em.count() >= 1, "预览未渲染 <em> (斜体)"
        assert ul.count() >= 1 and li.count() >= 2, "预览未渲染列表 <ul><li>"
        assert code.count() >= 1, "预览未渲染 <code> (行内代码)"
        assert a.count() >= 1 and "example.com" in (a.first.get_attribute("href") or ""), \
            "预览未渲染链接 <a>"
        print(f"[assert] strong={strong.count()} em={em.count()} ul={ul.count()} "
              f"li={li.count()} code={code.count()} a={a.count()}")

        # 切回编辑态，确认可来回切换
        toggle.first.click()
        page.wait_for_selector("form#comment-form textarea[name='content']:not([disabled])", timeout=5000)
        print("[ui] toggled back to edit mode (textarea re-enabled)")

        # 错误检查
        js_fail = [u for u in failed_requests if u.endswith(".js") or u.endswith(".css")]
        fatal = page_errors + [e for e in console_errors if "ERROR" in e.upper()] + js_fail
        print(f"[errors] pageerror={len(page_errors)} console_error={len(console_errors)} "
              f"js/css_fail={len(js_fail)}")

        page.screenshot(path="tests/_task816_preview.png")
        browser.close()

        assert not page_errors, f"pageerrors: {page_errors}"
        assert not js_fail, f"js/css 404: {js_fail}"
        print("\n✅ PASS: Task 816 评论 Markdown 实时预览渲染正常，零错误")
        return 0


def test_task816_comment_md_preview_e2e():
    assert main() == 0


if __name__ == "__main__":
    try:
        rc = main()
        sys.exit(0 if rc == 0 else 1)
    except AssertionError as e:
        print(f"\n❌ FAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 ERROR: {e}")
        sys.exit(2)
