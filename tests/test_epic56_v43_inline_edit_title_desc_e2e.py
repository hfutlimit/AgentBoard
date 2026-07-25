"""
Epic 56 (v4.3) 快速查看抽屉内联编辑标题与描述 —— 端到端验证
- 登录 admin -> story 25（任务列表）
- 打开快速查看抽屉，标题旁出现编辑按钮（✎），点击进入输入框，保存经 API 复核 + 列表同步 + 抽屉标题更新
- 描述区出现编辑按钮（✎），点击进入 textarea，保存经 API 复核 + 抽屉描述更新
- 取消不生效（描述编辑中途点「取消」，API 不变）
- 断言：0 pageerror / console error / .js+.css 404
- 测试末删除自建任务，不污染数据
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

SEED = "__E2E_QV43__" + str(1784958800)
NEW_TITLE = SEED + "_EDITED_TITLE"
NEW_DESC = "E2E 描述（已编辑）：用于验证抽屉内联编辑描述。"
CANCEL_DESC = "E2E 描述（取消测试，不应入库）"


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
                "description": "E2E 种子任务：用于验证抽屉内联编辑标题与描述。",
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

            # ---------- 1) 标题内联编辑 ----------
            # 验收标准 #1：标题旁出现编辑按钮
            title_edit_btn = page.locator(".quick-view-drawer .qv-title-row .qv-edit-btn")
            assert title_edit_btn.count() == 1, "title edit button should appear"
            title_edit_btn.click()
            page.wait_for_selector(".quick-view-drawer .qv-title-edit", timeout=5000)

            page.locator(".quick-view-drawer .qv-title-input").fill(NEW_TITLE)
            page.locator(".quick-view-drawer .qv-title-edit .btn--primary").click()  # 保存
            page.wait_for_timeout(800)

            # 验收标准 #3：API 复核 title 更新
            stt, task = api("GET", f"/api/tasks/{tid}", token=token)
            assert stt == 200 and task.get("title") == NEW_TITLE, (
                f"title should be updated, got {task.get('title')!r}"
            )
            # 抽屉标题同步
            assert page.locator(".quick-view-drawer .qv-title").inner_text().strip() == NEW_TITLE, \
                "drawer title should reflect edited value"
            # 列表同步：行标题更新
            assert page.locator(".entity-item--rich", has_text=NEW_TITLE).count() >= 1, \
                "list row should sync edited title"
            print("title inline edit OK")

            # ---------- 2) 描述内联编辑 ----------
            # 验收标准 #2：描述区出现编辑按钮
            desc_edit_btn = page.locator(".quick-view-drawer .qv-desc-head .qv-edit-btn")
            assert desc_edit_btn.count() == 1, "description edit button should appear"
            desc_edit_btn.click()
            page.wait_for_selector(".quick-view-drawer .qv-desc-edit", timeout=5000)

            page.locator(".quick-view-drawer .qv-desc-input").fill(NEW_DESC)
            page.locator(".quick-view-drawer .qv-desc-edit .btn--primary").click()  # 保存
            page.wait_for_timeout(800)

            # 验收标准 #3：API 复核 description 更新
            stt, task = api("GET", f"/api/tasks/{tid}", token=token)
            assert stt == 200 and task.get("description") == NEW_DESC, (
                f"description should be updated, got {task.get('description')!r}"
            )
            # 抽屉描述同步（回到展示态）
            assert page.locator(".quick-view-drawer .qv-desc-edit").count() == 0, \
                "desc should return to display mode after save"
            assert NEW_DESC in page.locator(".quick-view-drawer .qv-desc").inner_text(), \
                "drawer desc should reflect edited value"
            print("description inline edit OK")

            # ---------- 4) 取消不生效 ----------
            # 重新进入描述编辑，输入不同文本后点取消
            page.locator(".quick-view-drawer .qv-desc-head .qv-edit-btn").click()
            page.wait_for_selector(".quick-view-drawer .qv-desc-edit", timeout=5000)
            page.locator(".quick-view-drawer .qv-desc-input").fill(CANCEL_DESC)
            page.locator(".quick-view-drawer .qv-desc-edit").get_by_role("button", name="取消").click()  # 取消
            page.wait_for_timeout(500)

            stt, task = api("GET", f"/api/tasks/{tid}", token=token)
            assert stt == 200 and task.get("description") == NEW_DESC, (
                f"cancel should not change description, got {task.get('description')!r}"
            )
            assert page.locator(".quick-view-drawer .qv-desc-edit").count() == 0, \
                "desc should return to display mode after cancel"
            print("cancel no-op OK")

            # ---------- Esc 关闭抽屉 ----------
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
    print("E2E PASSED: v4.3 inline edit title+desc (0 pageerror/console/.js+.css 404)")


if __name__ == "__main__":
    main()
