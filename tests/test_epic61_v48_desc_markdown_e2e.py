"""
Epic 61 (v4.8) 快速查看抽屉任务描述 Markdown 渲染 —— 端到端验证
- 登录 admin -> 进入 story 100（AUTO61 项目，确定性种子任务）
- 创建 1 个带 Markdown 描述的种子任务（标题/加粗/斜体/列表/行内代码/链接）
- 打开种子任务快速查看抽屉
- 断言 .qv-desc.md 已渲染：含 <h1>/<strong>/<em>/<ul><li>/<code>/<a>，且不再出现原始 ** 标记
- 断言：0 pageerror / console error / .js+.css 404
- 测试末删除种子任务，不污染数据（保留 task 1111 追踪任务）
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
STORY_ID = 100
PROJECT_ID = 47
USER = "admin"
PASS = "admin123"

SEED = "__E2E_QV_DESC_MD__" + str(1786000000)

MD = (
    "# 标题一\n"
    "这是 **加粗** 文本与 *斜体*。\n"
    "\n"
    "- 列表项一\n"
    "- 列表项二\n"
    "\n"
    "行内 `code` 与 [示例链接](https://example.com)。\n"
)


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


def main():
    token, username = login()
    created = []
    errors = []
    try:
        st, t = api(
            "POST",
            f"/api/stories/{STORY_ID}/tasks",
            token=token,
            body={
                "project_id": PROJECT_ID,
                "story_id": STORY_ID,
                "title": SEED,
                "type": "task",
                "priority": "medium",
                "status": "backlog",
                "description": MD,
            },
        )
        assert st == 201, f"create seed task failed {st} {t}"
        seed_id = t["id"]
        created.append(seed_id)
        print("created seed task:", seed_id)

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
            page.goto(WEB + f"/story/{STORY_ID}", wait_until="networkidle")
            page.wait_for_selector(".entity-item--rich", timeout=15000)

            row = page.locator(".entity-item--rich", has_text=SEED)
            assert row.count() == 1, "seed task row not found"
            row.locator(".task-quick-view-btn").click()
            page.wait_for_selector(".quick-view-drawer", timeout=8000)
            page.wait_for_selector(".quick-view-drawer .qv-desc.md", timeout=8000)

            desc = page.locator(".quick-view-drawer .qv-desc.md")
            # 渲染结构断言
            assert desc.locator("h1").count() == 1, "should render <h1>"
            assert desc.locator("h1").inner_text() == "标题一", "h1 text mismatch"
            assert desc.locator("strong").count() == 1, "should render <strong>"
            assert "加粗" in desc.locator("strong").inner_text(), "strong text mismatch"
            assert desc.locator("em").count() == 1, "should render <em>"
            assert "斜体" in desc.locator("em").inner_text(), "em text mismatch"
            assert desc.locator("ul li").count() >= 2, "should render <ul><li>"
            assert desc.locator("code").count() >= 1, "should render <code>"
            assert "code" in desc.locator("code").inner_text(), "code text mismatch"
            assert desc.locator("a").count() == 1, "should render <a>"
            assert "example.com" in desc.locator("a").get_attribute("href"), "link href mismatch"

            # 一致性：不再出现原始 markdown 标记
            inner = desc.inner_text()
            assert "**" not in inner, "raw '**' markers should be rendered away"
            assert "*斜体*" not in inner, "raw '*斜体*' should be rendered away"
            print("markdown rendered:", repr(inner[:60]))

            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            assert page.locator(".quick-view-drawer").count() == 0, "drawer should close on Escape"

            browser.close()
    finally:
        for tid in created:
            api("DELETE", f"/api/tasks/{tid}", token=token)

    real_errors = [e for e in errors if "ERR_ABORTED" not in e and "ABORTED" not in e]
    if real_errors:
        print("ERRORS:", real_errors)
        sys.exit(1)
    print("E2E PASSED: 0 pageerror/console/.js+.css 404")


if __name__ == "__main__":
    main()
