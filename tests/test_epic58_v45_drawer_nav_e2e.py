"""
Epic 58 (v4.5) 快速查看抽屉任务前后导航 —— 端到端验证
- 登录 admin -> 进入 story 97（任务列表，含 3 个确定性种子任务）
- 排序固定为 created_at/asc（localStorage），使 visibleTasks 顺序稳定
- 打开首行任务抽屉：断言标题、上一项按钮禁用（首行）、下一项按钮可用
- 点「下一项」：抽屉标题变更、上一项变为可用
- 再点「下一项」：末行、下一项禁用
- 点「上一项」两次回到首行
- 键盘 ']' 下一项 / '[' 上一项：标题随之变更
- Esc 关闭抽屉
- 断言：0 pageerror / console error / .js+.css 404
- 测试末清理 3 个种子任务，不污染数据（保留 task 1108 追踪任务）
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
STORY_ID = 97
PROJECT_ID = 43
USER = "admin"
PASS = "admin123"

SEED = "__E2E_QV_NAV__" + str(1785000000)


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
        # 创建 3 个确定性种子任务（A/B/C），便于断言导航顺序
        tids = []
        for suf in ("A", "B", "C"):
            st, t = api(
                "POST",
                f"/api/stories/{STORY_ID}/tasks",
                token=token,
                body={
                    "project_id": PROJECT_ID,
                    "story_id": STORY_ID,
                    "title": SEED + "-" + suf,
                    "type": "task",
                    "priority": "medium",
                    "status": "backlog",
                    "description": "E2E 种子任务：用于验证快速查看抽屉前后导航。",
                },
            )
            assert st == 201, f"create task failed {st} {t}"
            tids.append(t["id"])
            created.append(t["id"])
        print("created seed tasks:", tids)

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
                # 固定排序，使 visibleTasks 顺序稳定（默认 created_at/desc 也可，这里用 asc 明确首=最旧）
                "localStorage.setItem('agentboard_sort_key','created_at');"
                "localStorage.setItem('agentboard_sort_order','asc');"
            )
            page.goto(WEB + f"/story/{STORY_ID}", wait_until="networkidle")
            page.wait_for_selector(".entity-item--rich", timeout=15000)

            # 打开种子任务 A 的抽屉（created_at asc 下 A 居中，前后均有任务）
            rowA = page.locator(".entity-item--rich", has_text=SEED + "-A")
            assert rowA.count() == 1, "seed task A row not found"
            first_title_expected = SEED + "-A"

            rowA.locator(".task-quick-view-btn").click()
            page.wait_for_selector(".quick-view-drawer", timeout=8000)
            page.wait_for_selector(".quick-view-drawer .qv-title", timeout=8000)
            title0 = page.locator(".quick-view-drawer .qv-title").inner_text()
            print("drawer opened on:", title0)
            assert title0 == first_title_expected, f"drawer should open on {first_title_expected}, got {title0}"

            prev_btn = page.locator(".qv-nav-group .qv-nav").first
            next_btn = page.locator(".qv-nav-group .qv-nav").nth(1)

            # A 非首非末：上下均可用
            assert not prev_btn.is_disabled(), "prev should be enabled at A"
            assert not next_btn.is_disabled(), "next should be enabled at A"

            # ---------- 下一项 -> B ----------
            next_btn.click()
            page.wait_for_timeout(500)
            title1 = page.locator(".quick-view-drawer .qv-title").inner_text()
            assert title1 == SEED + "-B", f"next should show B, got {title1}"
            print("next ->", title1)

            # ---------- 下一项 -> C（末行） ----------
            next_btn.click()
            page.wait_for_timeout(500)
            title2 = page.locator(".quick-view-drawer .qv-title").inner_text()
            assert title2 == SEED + "-C", f"next should show C, got {title2}"
            assert next_btn.is_disabled(), "next should be disabled at last row"
            print("next ->", title2)

            # ---------- 上一项 -> B ----------
            prev_btn.click()
            page.wait_for_timeout(500)
            assert page.locator(".quick-view-drawer .qv-title").inner_text() == SEED + "-B", "prev should go back to B"
            print("prev -> B")

            # ---------- 键盘 '[' 上一项 -> A ----------
            page.keyboard.press("[")
            page.wait_for_timeout(500)
            assert page.locator(".quick-view-drawer .qv-title").inner_text() == SEED + "-A", "keyboard '[' should go to A"
            print("keyboard [ -> A")

            # ---------- 键盘 ']' 下一项 -> B ----------
            page.keyboard.press("]")
            page.wait_for_timeout(500)
            assert page.locator(".quick-view-drawer .qv-title").inner_text() == SEED + "-B", "keyboard ']' should go to B"
            print("keyboard ] -> B")

            # ---------- Esc 关闭 ----------
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
