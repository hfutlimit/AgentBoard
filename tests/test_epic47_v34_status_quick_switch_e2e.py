"""
Epic 47 (v3.4) 任务列表行内快速状态切换 —— 端到端验证
- 登录 admin -> 进入 story 25（任务列表）
- 行内状态徽章(.status-pill)可点击，弹出 fixed 浮层(.status-menu)
- 浮层仅展示合法目标状态（前端镜像后端状态机）：
  * backlog -> 1 项（待办）
  * 切换为 todo 后 -> 3 项（进行中/待规划/完成）
- 点击目标项后状态即时更新（调用既有 setTaskStatus），徽章文案变更
- 点击背景遮罩(.status-menu-backdrop)可关闭浮层
- 测试末还原自建任务状态并删除，不污染数据
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

SEED_A = "__E2E_QSS_A__" + str(1784900000)
SEED_B = "__E2E_QSS_B__" + str(1784900001)


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
        # 自建两个受控 backlog 任务
        for title in (SEED_A, SEED_B):
            st, t = api(
                "POST",
                f"/api/stories/{STORY_ID}/tasks",
                token=token,
                body={
                    "project_id": PROJECT_ID,
                    "story_id": STORY_ID,
                    "title": title,
                    "type": "task",
                    "priority": "low",
                    "status": "backlog",
                },
            )
            assert st == 201, f"create task failed {st} {t}"
            created.append(t["id"])
        print("created test tasks:", created)

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

            # ---------- 任务 A：backlog -> 浮层 1 项（待办）----------
            ra = row_of(page, SEED_A)
            assert ra is not None, "seed task A row not found"
            ra.locator(".status-pill").click()
            page.wait_for_selector(".status-menu", timeout=8000)
            items = page.locator(".status-menu-item")
            n = items.count()
            print("A menu item count (backlog):", n)
            assert n == 1, f"backlog should show exactly 1 next status, got {n}"
            assert "待办" in items.nth(0).inner_text(), "backlog next should be 待办"
            items.nth(0).click()
            page.wait_for_timeout(600)
            ra = row_of(page, SEED_A)
            assert "待办" in ra.locator(".status-pill").inner_text(), "A should now be 待办"

            # ---------- A 切换为 todo 后：浮层 3 项（进行中/待规划/完成）----------
            ra.locator(".status-pill").click()
            page.wait_for_selector(".status-menu", timeout=8000)
            n2 = page.locator(".status-menu-item").count()
            print("A menu item count (todo):", n2)
            assert n2 == 3, f"todo should show 3 next statuses, got {n2}"
            # 还原为 backlog（待规划）
            labels = [page.locator(".status-menu-item").nth(i).inner_text() for i in range(n2)]
            print("A todo menu labels:", labels)
            for i in range(n2):
                if "待规划" in labels[i]:
                    page.locator(".status-menu-item").nth(i).click()
                    break
            page.wait_for_timeout(600)
            ra = row_of(page, SEED_A)
            assert "待规划" in ra.locator(".status-pill").inner_text(), "A should be restored to 待规划(backlog)"

            # ---------- 背景遮罩关闭浮层 ----------
            rb = row_of(page, SEED_B)
            rb.locator(".status-pill").click()
            page.wait_for_selector(".status-menu", timeout=8000)
            assert page.locator(".status-menu-backdrop").count() == 1, "backdrop should appear"
            page.locator(".status-menu-backdrop").click()
            page.wait_for_timeout(300)
            assert page.locator(".status-menu").count() == 0, "menu should close after backdrop click"

            # ---------- 任务 B：再验证一次 backlog -> 待办 ----------
            rb = row_of(page, SEED_B)
            rb.locator(".status-pill").click()
            page.wait_for_selector(".status-menu", timeout=8000)
            assert page.locator(".status-menu-item").count() == 1, "B backlog should show 1 item"
            page.locator(".status-menu-item").nth(0).click()
            page.wait_for_timeout(600)
            rb = row_of(page, SEED_B)
            assert "待办" in rb.locator(".status-pill").inner_text(), "B should now be 待办"
            # 还原 B 到 backlog
            rb.locator(".status-pill").click()
            page.wait_for_selector(".status-menu", timeout=8000)
            nb = page.locator(".status-menu-item").count()
            blabels = [page.locator(".status-menu-item").nth(i).inner_text() for i in range(nb)]
            for i in range(nb):
                if "待规划" in blabels[i]:
                    page.locator(".status-menu-item").nth(i).click()
                    break
            page.wait_for_timeout(600)
            rb = row_of(page, SEED_B)
            assert "待规划" in rb.locator(".status-pill").inner_text(), "B should be restored to backlog"

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
