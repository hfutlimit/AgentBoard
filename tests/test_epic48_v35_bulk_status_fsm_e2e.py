"""
Epic 48 (v3.5) 批量状态变更状态机感知 —— 端到端验证
- 登录 admin -> 进入 story 186（任务列表，隔离 fixture）
- 自建 4 个受控状态任务（2×todo / 1×in_progress / 1×backlog）
- 批量选择行 -> 点击「批量修改状态」-> 弹层仅展示「所选任务状态机交集」内的合法目标：
    * 选 todo+todo+in_progress -> 交集 = [done] -> 仅 1 个按钮「完成」
    * 选 backlog+todo        -> 交集 = []     -> 显示「无共同可流转目标」提示，0 个状态按钮
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
STORY_ID = 186
PROJECT_ID = 111
USER = "admin"
PASS = "admin123"

# 前端镜像的后端状态机（必须与 service.TRANSITIONS 一致）
TRANSITIONS = {
    "backlog": ["todo"],
    "todo": ["in_progress", "backlog", "done"],
    "in_progress": ["in_review", "verifying", "todo", "done"],
    "in_review": ["done", "in_progress"],
    "verifying": ["done", "in_progress"],
    "done": ["in_progress", "todo"],
}
STATUS_LABEL = {
    "backlog": "待规划", "todo": "待办", "in_progress": "进行中",
    "in_review": "评审中", "verifying": "验证中", "done": "完成",
}


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


def walk_status(tid, target, token):
    """沿 TRANSITIONS BFS 将任务走到 target（避免非法跳变 400）。"""
    st, t = api("GET", f"/api/tasks/{tid}", token=token)
    cur = t["status"]
    if cur == target:
        return
    # BFS
    from collections import deque
    q = deque([cur]); prev = {cur: None}
    while q:
        n = q.popleft()
        if n == target:
            break
        for nx in TRANSITIONS.get(n, []):
            if nx not in prev:
                prev[nx] = n; q.append(nx)
    path = []
    x = target
    while x != cur:
        path.append(x); x = prev[x]
    path.reverse()
    for s in path:
        api("PUT", f"/api/tasks/{tid}/status", token=token, body={"status": s})


def row_of(page, title):
    rows = page.locator(".entity-item--rich")
    for i in range(rows.count()):
        r = rows.nth(i)
        if (r.locator(".entity-item-title").inner_text().strip() or "") == title:
            return r
    return None


def select_row(page, title):
    r = row_of(page, title)
    assert r is not None, f"row not found: {title}"
    r.locator(".task-checkbox").check()
    return r


def bulk_status_panel_buttons(page):
    return page.locator(".bulk-panel .status-btn")


def main():
    token, username = login()
    seeds = {
        "V35A__todo": "todo",
        "V35B__todo": "todo",
        "V35C__inprog": "in_progress",
        "V35D__backlog": "backlog",
    }
    created = []
    errors = []
    try:
        # 自建受控状态任务
        for title, target in seeds.items():
            st, t = api(
                "POST", f"/api/stories/{STORY_ID}/tasks", token=token,
                body={"project_id": PROJECT_ID, "title": title, "type": "task", "priority": "medium"},
            )
            assert st == 201, f"create failed {st} {t}"
            tid = t["id"]; created.append(tid)
            walk_status(tid, target, token)
        print("created:", created)

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

            # ---------- 场景 A：todo + todo + in_progress -> 交集 [done] ----------
            select_row(page, "V35A__todo")
            select_row(page, "V35B__todo")
            select_row(page, "V35C__inprog")
            page.wait_for_selector(".bulk-action-bar", timeout=8000)
            page.locator("button:has-text('批量修改状态')").first.click()
            page.wait_for_selector(".bulk-panel", timeout=8000)
            page.wait_for_timeout(200)
            btns = bulk_status_panel_buttons(page)
            labels = [btns.nth(i).inner_text().strip() for i in range(btns.count())]
            print("A status buttons:", labels)
            assert btns.count() == 1, f"todo+todo+in_progress should yield exactly 1 legal status, got {labels}"
            assert labels[0] == STATUS_LABEL["done"], f"expected 完成, got {labels}"
            # 交集非空时不应出现空提示
            assert page.locator(".bulk-panel .muted").count() == 0, "empty-hint must NOT show when intersection non-empty"

            # 关闭弹层（取消），清除选择
            page.locator(".bulk-panel button:has-text('取消')").first.click()
            page.wait_for_timeout(150)
            page.locator(".bulk-action-bar button:has-text('清除')").first.click()
            page.wait_for_timeout(150)

            # ---------- 场景 B：backlog + todo -> 交集 [] ----------
            select_row(page, "V35D__backlog")
            select_row(page, "V35A__todo")
            page.wait_for_selector(".bulk-action-bar", timeout=8000)
            page.locator("button:has-text('批量修改状态')").first.click()
            page.wait_for_selector(".bulk-panel", timeout=8000)
            page.wait_for_timeout(200)
            assert bulk_status_panel_buttons(page).count() == 0, "backlog+todo intersection must be empty (0 buttons)"
            muted = page.locator(".bulk-panel .muted")
            assert muted.count() == 1, "empty-hint must show when intersection empty"
            assert "无共同可流转目标" in muted.first.inner_text(), "empty hint text mismatch"
            print("B empty-hint OK")

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
