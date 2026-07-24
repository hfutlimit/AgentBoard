"""
Epic 54 (v4.1) 任务列表行内快速修改优先级 —— 端到端验证
- 登录 admin -> 进入 story 25（任务列表）
- 行内优先级徽章(.priority-pill)可点击，弹出 fixed 浮层(.priority-menu)
- 浮层列出全部 5 档优先级（复用 priorities）+ 当前优先级 active 高亮
- 点击某档即调用既有 updateTask(priority) 并即时更新行内优先级（经 API 复核）
- 点击背景遮罩(.status-menu-backdrop)可关闭浮层
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

SEED = "__E2E_IP__" + str(1784951000)


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


def row_of(page, title):
    rows = page.locator(".entity-item--rich")
    for i in range(rows.count()):
        r = rows.nth(i)
        if (r.locator(".entity-item-title").inner_text().strip() or "") == title:
            return r
    return None


def main():
    token, username = login()
    created = []
    errors = []
    try:
        # 自建一个受控 backlog 任务（初始优先级 low）
        st, t = api(
            "POST",
            f"/api/stories/{STORY_ID}/tasks",
            token=token,
            body={
                "project_id": PROJECT_ID,
                "story_id": STORY_ID,
                "title": SEED,
                "type": "task",
                "priority": "low",
                "status": "backlog",
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

            # ---------- 打开行内优先级浮层 ----------
            r = row_of(page, SEED)
            assert r is not None, "seed task row not found"
            r.locator(".priority-pill").click()
            page.wait_for_selector(".priority-menu", timeout=8000)
            items = page.locator(".priority-menu-item")
            n = items.count()
            print("priority menu item count:", n)
            assert n == 5, f"priority menu should list 5 priorities, got {n}"
            # 当前优先级 low 应 active 高亮
            assert page.locator(".priority-menu-item.priority--low.active").count() == 1, \
                "current priority (low) should be active-highlighted"

            # ---------- 改为 highest（第一项）----------
            page.locator(".priority-menu-item.priority--highest").click()
            page.wait_for_timeout(700)
            stt, task = api("GET", f"/api/tasks/{tid}", token=token)
            assert stt == 200, f"get task failed {stt}"
            assert task.get("priority") == "highest", (
                f"priority should be highest, got {task.get('priority')}"
            )
            # UI 同步：行内徽章变为 priority--highest
            r2 = row_of(page, SEED)
            assert r2.locator(".priority-pill.priority--highest").count() == 1, \
                "row priority badge should reflect highest"

            # ---------- 再改为 medium（中间档）并复核 ----------
            r2.locator(".priority-pill").click()
            page.wait_for_selector(".priority-menu", timeout=8000)
            page.locator(".priority-menu-item.priority--medium").click()
            page.wait_for_timeout(700)
            stt2, task2 = api("GET", f"/api/tasks/{tid}", token=token)
            assert task2.get("priority") == "medium", (
                f"priority should be medium, got {task2.get('priority')}"
            )

            # ---------- 背景遮罩关闭浮层 ----------
            r3 = row_of(page, SEED)
            r3.locator(".priority-pill").click()
            page.wait_for_selector(".priority-menu", timeout=8000)
            assert page.locator(".status-menu-backdrop").count() == 1, "backdrop should appear"
            page.locator(".status-menu-backdrop").click()
            page.wait_for_timeout(300)
            assert page.locator(".priority-menu").count() == 0, "priority menu should close after backdrop click"

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
