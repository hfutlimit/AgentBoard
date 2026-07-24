"""
Epic 51 (v3.8) 任务列表行内快速指派 —— 端到端验证
- 登录 admin -> 进入 story 25（任务列表）
- 行内指派人头像(.assignee-pill)可点击，弹出 fixed 浮层(.assign-menu)
- 浮层列出当前项目成员（复用 members()）+ 末尾「未指派」项
- 点击成员即调用既有 updateTask(assignee_id) 并即时更新行内指派人（经 API 复核）
- 点击「未指派」可取消指派（assignee_id -> null，经 API 复核）
- 点击背景遮罩(.status-menu-backdrop)可关闭浮层
- 测试末还原并删除自建任务，不污染数据
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

SEED = "__E2E_IA__" + str(1784950000)


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


def ensure_member(token):
    """project 3 (story 25) 初始无成员 -> 临时把 admin 加为成员以便测试指派，返回 admin user_id。"""
    _, me = api("GET", "/api/auth/me", token=token)
    my_id = me["id"]
    st, _ = api("POST", f"/api/projects/{PROJECT_ID}/members", token=token, body={"user_id": my_id})
    assert st in (201, 200, 409), f"add member failed {st}"
    _, members = api("GET", f"/api/projects/{PROJECT_ID}/members", token=token)
    items = members.get("items", members) if isinstance(members, dict) else members
    assert len(items) >= 1, f"project {PROJECT_ID} still has no members"
    return my_id


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
    added_member = False
    errors = []
    try:
        # project 3 (story 25) 初始无成员 -> 临时加 admin 为成员
        my_id = ensure_member(token)
        added_member = True

        # 自建一个受控 backlog 任务（初始未指派）
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

        # 取当前项目成员，挑 admin 用于指派
        target = my_id
        print("target assignee user_id:", target)

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

            # ---------- 打开行内指派浮层 ----------
            r = row_of(page, SEED)
            assert r is not None, "seed task row not found"
            r.locator(".assignee-pill").click()
            page.wait_for_selector(".assign-menu", timeout=8000)
            member_items = page.locator(".assign-menu-item:not(.assign-menu-clear)")
            nm = member_items.count()
            print("assign menu member item count:", nm)
            assert nm >= 1, f"assign menu should list >=1 member, got {nm}"
            assert page.locator(".assign-menu-clear").count() == 1, "未指派 item should appear"

            # ---------- 指派给第一个成员 ----------
            member_items.nth(0).click()
            page.wait_for_timeout(700)
            stt, task = api("GET", f"/api/tasks/{tid}", token=token)
            assert stt == 200, f"get task failed {stt}"
            assert task.get("assignee_id") == target, (
                f"assignee_id should be {target}, got {task.get('assignee_id')}"
            )
            # UI 同步：行内头像显示指派人首字母（非 ?）
            r2 = row_of(page, SEED)
            avatar = r2.locator(".assignee-pill .assignee-avatar-sm")
            assert avatar.inner_text().strip() != "?", \
                "assignee avatar should show initials, not ?"

            # ---------- 取消指派（未指派）----------
            r2.locator(".assignee-pill").click()
            page.wait_for_selector(".assign-menu", timeout=8000)
            page.locator(".assign-menu-clear").click()
            page.wait_for_timeout(700)
            stt2, task2 = api("GET", f"/api/tasks/{tid}", token=token)
            assert task2.get("assignee_id") is None, (
                f"assignee_id should be null after 未指派, got {task2.get('assignee_id')}"
            )

            # ---------- 背景遮罩关闭浮层 ----------
            r3 = row_of(page, SEED)
            r3.locator(".assignee-pill").click()
            page.wait_for_selector(".assign-menu", timeout=8000)
            assert page.locator(".status-menu-backdrop").count() == 1, "backdrop should appear"
            page.locator(".status-menu-backdrop").click()
            page.wait_for_timeout(300)
            assert page.locator(".assign-menu").count() == 0, "assign menu should close after backdrop click"

            browser.close()
    finally:
        for tid in created:
            api("DELETE", f"/api/tasks/{tid}", token=token)
        if added_member:
            api("DELETE", f"/api/projects/{PROJECT_ID}/members/{my_id}", token=token)

    real_errors = [e for e in errors if "ERR_ABORTED" not in e and "ABORTED" not in e]
    if real_errors:
        print("ERRORS:", real_errors)
        sys.exit(1)
    print("E2E PASSED: 0 pageerror/console/.js+.css 404")


if __name__ == "__main__":
    main()
