"""
Epic 63 (v5.0) Story/Epic 详情页描述 Markdown 渲染 —— 端到端验证
- 登录 admin -> 进入 story 102（AUTO63 项目，已播种 Markdown 描述）与 epic 53（已播种 Markdown 描述）
- Story 详情页 (/story/102)：.story-description.task-md 渲染 <h2>/<strong>/<em>/<ol><li>/<blockquote>/<code>，且无原始 ** 标记
- Epic 详情页 (/epic/53)：.task-md 渲染 <h1>/<strong>/<em>/<ul><li>/<code>/<a>，且无原始 ** 标记
- 断言：0 pageerror / console error / .js+.css 404
- 复用既有追踪实体（epic 53 / story 102），不污染业务数据
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
STORY_ID = 102
EPIC_ID = 53
USER = "admin"
PASS = "admin123"


def api(method, path, token=None, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def login():
    st, u = api("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    assert st == 200, f"login failed {st}"
    return u["token"], u["username"]


def goto_retry(page, url, selector, timeout=15000):
    """SPA 深链偶发竞态：最多重试 3 次 goto + reload 直到 selector 出现。"""
    last = None
    for _ in range(3):
        try:
            page.goto(url, wait_until="networkidle")
            page.wait_for_selector(selector, timeout=timeout)
            return
        except Exception as e:  # noqa: BLE001
            last = e
            page.reload(wait_until="networkidle")
    if last:
        raise last


def main():
    token, username = login()
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
        page.on("console", lambda m: errors.append("console:" + m.type + ":" + m.text) if m.type == "error" else None)
        page.on("requestfailed", lambda r: (
            errors.append("404:" + r.url) if (r.url.endswith(".js") or r.url.endswith(".css")) else None
        ))
        page.add_init_script(
            f"localStorage.setItem('agentboard_token','{token}');"
            f"localStorage.setItem('agentboard_user','{username}');"
        )

        # ---- Story 详情页：Markdown 渲染 ----
        goto_retry(page, WEB + f"/story/{STORY_ID}", ".story-description.task-md")
        desc = page.locator(".story-description.task-md").first
        assert desc.locator("h2").count() == 1, "story desc should render <h2>"
        assert "Story 描述" in desc.locator("h2").inner_text(), "story h2 text mismatch"
        assert desc.locator("strong").count() == 1, "story desc should render <strong>"
        assert "Markdown" in desc.locator("strong").inner_text(), "story strong text mismatch"
        assert desc.locator("em").count() == 1, "story desc should render <em>"
        assert "斜体" in desc.locator("em").inner_text(), "story em text mismatch"
        assert desc.locator("ol li").count() >= 2, "story desc should render <ol><li>"
        assert desc.locator("blockquote").count() == 1, "story desc should render <blockquote>"
        assert desc.locator("code").count() >= 1, "story desc should render <code>"
        inner = desc.inner_text()
        assert "**" not in inner, "raw '**' markers should be rendered away"
        assert "*斜体*" not in inner, "raw '*斜体*' should be rendered away"
        print("story desc markdown rendered:", repr(inner[:60]))

        # ---- Epic 详情页：Markdown 渲染 ----
        goto_retry(page, WEB + f"/epic/{EPIC_ID}", ".card.md.task-md")
        edesc = page.locator(".card.md.task-md").first
        assert edesc.locator("h1").count() == 1, "epic desc should render <h1>"
        assert "Epic 63 标题" in edesc.locator("h1").inner_text(), "epic h1 text mismatch"
        assert edesc.locator("strong").count() == 1, "epic desc should render <strong>"
        assert "加粗" in edesc.locator("strong").inner_text(), "epic strong text mismatch"
        assert edesc.locator("em").count() == 1, "epic desc should render <em>"
        assert "斜体" in edesc.locator("em").inner_text(), "epic em text mismatch"
        assert edesc.locator("ul li").count() >= 2, "epic desc should render <ul><li>"
        assert edesc.locator("code").count() >= 1, "epic desc should render <code>"
        assert edesc.locator("a").count() == 1, "epic desc should render <a>"
        assert "example.com" in edesc.locator("a").get_attribute("href"), "epic link href mismatch"
        einner = edesc.inner_text()
        assert "**" not in einner, "raw '**' markers should be rendered away"
        print("epic desc markdown rendered:", repr(einner[:60]))

        browser.close()

    real_errors = [e for e in errors if "ERR_ABORTED" not in e and "ABORTED" not in e]
    if real_errors:
        print("ERRORS:", real_errors)
        sys.exit(1)
    print("E2E PASSED: 0 pageerror/console/.js+.css 404")


if __name__ == "__main__":
    main()
