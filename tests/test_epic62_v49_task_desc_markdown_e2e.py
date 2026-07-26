"""
Epic 62 (v4.9) 任务详情页 Description/Spec Markdown 渲染 —— 端到端验证
- 登录 admin -> 进入 story 101（AUTO62 项目，确定性种子任务）
- 创建 2 个种子任务：
  * seed1: 带 Markdown 描述的任务（标题/加粗/斜体/列表/行内代码/链接）+ Markdown Spec
  * seed2: 无描述（空 description）的任务 -> 验证空态（空）
- 打开 seed1 任务详情页 (/task/{id})
- 断言 Description 卡 .task-md 已渲染：<h1>/<strong>/<em>/<ul><li>/<code>/<a>，且无原始 ** 标记
- 断言 Spec 卡 .task-md 已渲染 Markdown（<h2>）
- 打开 seed2 任务详情页 -> 断言 .task-md-empty 显示「（空）」
- 断言：0 pageerror / console error / .js+.css 404
- 测试末删除种子任务，不污染数据（保留 task 1117 追踪任务）
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
STORY_ID = 101
PROJECT_ID = 48
USER = "admin"
PASS = "admin123"

SEED1 = "__E2E_TASKMD_DESC__" + str(1786000000)
SEED2 = "__E2E_TASKMD_EMPTY__" + str(1786000001)

MD_DESC = (
    "# 标题一\n"
    "这是 **加粗** 文本与 *斜体*。\n"
    "\n"
    "- 列表项一\n"
    "- 列表项二\n"
    "\n"
    "行内 `code` 与 [示例链接](https://example.com)。\n"
)
MD_SPEC = "## 规范要点\n- 项 A\n- 项 B\n"


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
        # seed1: markdown desc + spec
        st, t1 = api(
            "POST",
            f"/api/stories/{STORY_ID}/tasks",
            token=token,
            body={
                "project_id": PROJECT_ID,
                "story_id": STORY_ID,
                "title": SEED1,
                "type": "task",
                "priority": "medium",
                "status": "backlog",
                "description": MD_DESC,
                "spec": MD_SPEC,
            },
        )
        assert st == 201, f"create seed1 failed {st} {t1}"
        seed1_id = t1["id"]
        created.append(seed1_id)
        print("created seed1 task:", seed1_id)

        # seed2: empty description
        st, t2 = api(
            "POST",
            f"/api/stories/{STORY_ID}/tasks",
            token=token,
            body={
                "project_id": PROJECT_ID,
                "story_id": STORY_ID,
                "title": SEED2,
                "type": "task",
                "priority": "low",
                "status": "backlog",
            },
        )
        assert st == 201, f"create seed2 failed {st} {t2}"
        seed2_id = t2["id"]
        created.append(seed2_id)
        print("created seed2 task:", seed2_id)

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

            # ---- seed1: markdown rendering ----
            page.goto(WEB + f"/task/{seed1_id}", wait_until="networkidle")
            page.wait_for_selector(".task-md", timeout=15000)

            desc = page.locator(".task-md").nth(0)  # Description card
            spec = page.locator(".task-md").nth(1)   # Spec card

            assert desc.locator("h1").count() == 1, "desc should render <h1>"
            assert desc.locator("h1").inner_text() == "标题一", "h1 text mismatch"
            assert desc.locator("strong").count() == 1, "desc should render <strong>"
            assert "加粗" in desc.locator("strong").inner_text(), "strong text mismatch"
            assert desc.locator("em").count() == 1, "desc should render <em>"
            assert "斜体" in desc.locator("em").inner_text(), "em text mismatch"
            assert desc.locator("ul li").count() >= 2, "desc should render <ul><li>"
            assert desc.locator("code").count() >= 1, "desc should render <code>"
            assert "code" in desc.locator("code").inner_text(), "code text mismatch"
            assert desc.locator("a").count() == 1, "desc should render <a>"
            assert "example.com" in desc.locator("a").get_attribute("href"), "link href mismatch"

            # Spec card renders markdown
            assert spec.locator("h2").count() == 1, "spec should render <h2>"
            assert "规范要点" in spec.locator("h2").inner_text(), "spec h2 text mismatch"
            assert spec.locator("ul li").count() >= 2, "spec should render <ul><li>"

            # consistency: raw markdown markers rendered away
            inner = desc.inner_text()
            assert "**" not in inner, "raw '**' markers should be rendered away"
            assert "*斜体*" not in inner, "raw '*斜体*' should be rendered away"
            print("desc markdown rendered:", repr(inner[:60]))

            # ---- seed2: empty state ----
            page.goto(WEB + f"/task/{seed2_id}", wait_until="networkidle")
            page.wait_for_selector(".task-md-empty", timeout=15000)
            empty_text = page.locator(".task-md-empty").first.inner_text()
            assert "（空）" in empty_text, f"empty state should show （空）, got {empty_text!r}"
            print("empty state ok:", repr(empty_text))

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
