"""
Epic 52 (v3.9) 任务列表行内快速编辑截止日期 —— 端到端验证
- 登录 admin -> 进入 story 193（任务列表，含追踪任务 999）
- 行内截止日期徽章(.due-pill)始终可点击（有日期显示日期 / 无日期显示「设截止」）
- 点击弹出 fixed 浮层(.due-menu)，日期输入框(.due-menu-input)预填当前 due_date
- 填入新日期点「应用」-> 调用 updateTask(due_date) 并即时更新（经 API 复核）
- 再次打开改为另一日期 -> 复核修改生效（覆盖「从空设置」与「修改已有」两条路径）
- 点「清除」-> due_date 置 null（经 API 复核，徽章回到「设截止」）
- 点击背景遮罩(.status-menu-backdrop)可关闭浮层
- 断言：0 pageerror / console error / .js+.css 404
- 测试末将任务 999 的 due_date 还原为空，不改其状态（保持 backlog，供追踪置 in_review）
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:28080"
API = "http://127.0.0.1:18000"
STORY_ID = 193
TASK_ID = 999
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


def get_due(tid, token):
    st, t = api("GET", f"/api/tasks/{tid}", token=token)
    assert st == 200, f"get task {tid} failed {st}"
    return t.get("due_date")


def main():
    token, username = login()
    errors = []
    try:
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

            row = page.locator(f".entity-item:has(a[href='/task/{TASK_ID}'])")
            assert row.count() == 1, f"task {TASK_ID} row should exist exactly once, got {row.count()}"

            # 初始：无 due_date -> 显示「设截止」空态
            due_pill = row.locator(".due-pill")
            assert due_pill.count() == 1, "due-pill should be present"
            assert "设截止" in due_pill.inner_text(), "empty due pill should show 设截止"

            # ---------- 1) 打开浮层，预填应为空 ----------
            due_pill.click()
            page.wait_for_selector(".due-menu", timeout=8000)
            assert page.locator(".due-menu-input").count() == 1, "date input should appear"
            assert page.locator(".due-menu-clear").count() == 1, "清除 button should appear"
            assert page.locator(".status-menu-backdrop").count() == 1, "backdrop should appear"

            # ---------- 2) 设置 due_date = 2026-08-01 ----------
            page.locator(".due-menu-input").fill("2026-08-01")
            page.locator(".due-menu-apply").click()
            page.wait_for_timeout(700)
            assert get_due(TASK_ID, token) == "2026-08-01", \
                f"due_date should be 2026-08-01, got {get_due(TASK_ID, token)}"
            # UI 同步：徽章应离开「设截止」空态（formatDueDate 渲染为 MM/DD，故不以 ISO 字符串断言）
            pill_text = row.locator(".due-pill").inner_text()
            assert "设截止" not in pill_text, "UI should reflect new due_date (left empty state)"
            assert "has-due" in row.locator(".due-pill").get_attribute("class"), "due-pill should have has-due class"

            # ---------- 3) 重新打开，修改为 2026-08-15（覆盖「修改已有」路径）----------
            row.locator(".due-pill").click()
            page.wait_for_selector(".due-menu", timeout=8000)
            # 预填应等于当前 due_date
            assert page.locator(".due-menu-input").input_value() == "2026-08-01", \
                "date input should prefill current due_date"
            page.locator(".due-menu-input").fill("2026-08-15")
            page.locator(".due-menu-apply").click()
            page.wait_for_timeout(700)
            assert get_due(TASK_ID, token) == "2026-08-15", \
                f"due_date should be updated to 2026-08-15, got {get_due(TASK_ID, token)}"

            # ---------- 4) 清除 due_date ----------
            row.locator(".due-pill").click()
            page.wait_for_selector(".due-menu", timeout=8000)
            page.locator(".due-menu-clear").click()
            page.wait_for_timeout(700)
            assert get_due(TASK_ID, token) is None, \
                f"due_date should be null after clear, got {get_due(TASK_ID, token)}"
            assert "设截止" in row.locator(".due-pill").inner_text(), "UI should revert to 设截止"

            # ---------- 5) 背景遮罩关闭 ----------
            row.locator(".due-pill").click()
            page.wait_for_selector(".due-menu", timeout=8000)
            page.locator(".status-menu-backdrop").click()
            page.wait_for_timeout(300)
            assert page.locator(".due-menu").count() == 0, "due menu should close after backdrop click"

            browser.close()
    finally:
        # 还原任务 999 的 due_date 为 null（不改状态；status 端点仅合法迁移，backlog->backlog 安全）
        api("PATCH", f"/api/tasks/{TASK_ID}", token=token, body={"due_date": None})

    real_errors = [e for e in errors if "ERR_ABORTED" not in e and "ABORTED" not in e]
    if real_errors:
        print("ERRORS:", real_errors)
        sys.exit(1)
    print("E2E PASSED: 0 pageerror/console/.js+.css 404")


if __name__ == "__main__":
    main()
