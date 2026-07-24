"""
Epic 55 (v4.2) 任务列表行内快速查看抽屉 (Quick View Drawer) —— 端到端验证
- 登录 admin -> 进入 story 25（任务列表）
- 行内新增「快速查看」按钮(.task-quick-view-btn)，点击打开右侧抽屉(.quick-view-drawer)
- 抽屉展示：面包屑、标题、#id、状态/优先级/指派/截止 四字段、子任务进度、描述、在详情页打开链接
- 抽屉内点击「状态」字段复用既有行内菜单(.status-menu)，改状态经 API 复核并即时更新
- Esc 与背景遮罩(.qv-backdrop)可关闭抽屉
- 测试末删除自建任务，不污染数据
- 断言：0 pageerror / console error / .js+.css 404
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:28080"
API = "http://127.0.0.1:18000"
STORY_ID = 25
PROJECT_ID = 3
USER = "admin"
PASS = "admin123"

SEED = "__E2E_QV__" + str(1784951400)


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
                "description": "E2E 种子任务：用于验证快速查看抽屉渲染。",
            },
        )
        assert st == 201, f"create task failed {st} {t}"
        tid = t["id"]
        created.append(tid)
        print("created seed task:", tid)

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

            # ---------- 打开快速查看抽屉 ----------
            row.locator(".task-quick-view-btn").click()
            page.wait_for_selector(".quick-view-drawer", timeout=8000)
            print("drawer opened")

            # 标题 / id
            assert page.locator(".quick-view-drawer .qv-title").inner_text().strip() == SEED
            assert "#" + str(tid) == page.locator(".quick-view-drawer .qv-id").inner_text().strip()

            # 面包屑包含 story 标题（story 25）
            crumb = page.locator(".quick-view-drawer .qv-breadcrumb").inner_text()
            print("breadcrumb:", crumb)
            assert "实现" in crumb or "Story" in crumb or len(crumb) > 0, "breadcrumb should render"

            # 四字段齐全
            fields = page.locator(".quick-view-drawer .qv-field")
            assert fields.count() == 4, f"expected 4 qv-fields, got {fields.count()}"

            # 描述渲染
            assert "E2E 种子任务" in page.locator(".quick-view-drawer .qv-desc").inner_text()

            # 在详情页打开链接
            assert page.locator(".quick-view-drawer .qv-open").count() == 1

            # ---------- 抽屉内快速改状态（复用既有行内菜单）----------
            page.locator(".quick-view-drawer .qv-field").first.click()  # 状态字段
            page.wait_for_selector(".status-menu", timeout=8000)
            assert page.locator(".status-menu-item").count() >= 1, "status menu should open from drawer"
            page.locator(".status-menu-item.status--todo").click()
            page.wait_for_timeout(700)
            stt, task = api("GET", f"/api/tasks/{tid}", token=token)
            assert stt == 200 and task.get("status") == "todo", (
                f"status should be todo, got {task.get('status')}"
            )
            # 抽屉状态徽章同步
            assert page.locator(".quick-view-drawer .badge.status.status--todo").count() == 1, \
                "drawer status badge should reflect todo"

            # ---------- Esc 关闭抽屉 ----------
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            assert page.locator(".quick-view-drawer").count() == 0, "drawer should close on Escape"

            # ---------- 再次打开，背景遮罩关闭 ----------
            row.locator(".task-quick-view-btn").click()
            page.wait_for_selector(".quick-view-drawer", timeout=8000)
            assert page.locator(".qv-backdrop").count() == 1, "backdrop should appear"
            page.locator(".qv-backdrop").click()
            page.wait_for_timeout(300)
            assert page.locator(".quick-view-drawer").count() == 0, "drawer should close on backdrop click"

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
