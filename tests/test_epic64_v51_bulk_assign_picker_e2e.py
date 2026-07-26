"""
E2E: Epic 64 v5.1 — 任务列表批量指派面板增强（成员头像/姓名 chip 选择器 + 搜索）
验证点:
  1. 勾选任务 -> 批量指派 -> 面板以头像+姓名 chip 列表呈现成员（含「未指派」）
  2. 搜索过滤：输入 "qa1" 仅保留 qa1 chip；输入无匹配关键字显示「无匹配成员」
  3. 点击成员 chip 即时批量指派；点击「未指派」chip 即时清除指派
  4. 经 API 复核 assignee_id 变更；0 pageerror / console error / .js+.css 404
"""
import json
import os
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
IDS = "/tmp/v51_ids.json"


def api_req(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def load_ids():
    return json.load(open(IDS))


def select_all_via_checkboxes(page):
    """勾选当前可见的全部任务复选框（批量栏仅在已选后才出现，故不能直接点『全选』）"""
    page.wait_for_selector(".task-checkbox", timeout=5000)
    boxes = page.locator(".task-checkbox")
    n = boxes.count()
    for i in range(n):
        boxes.nth(i).check()
    page.wait_for_timeout(400)


def main():
    ids = load_ids()
    token = ids["token"]
    story_id = ids["story"]
    project_id = ids["project"]

    # 创建第二个成员，使成员列表 > 1，验证头像/搜索过滤
    st, qa = api_req("POST", "/api/auth/register",
                     body={"username": "qa1_ep64", "password": "qa123456", "email": "qa1@local"})
    if st == 409:  # 上一次崩溃残留，复用已有账号
        st, qa = api_req("POST", "/api/auth/login",
                         body={"username": "qa1_ep64", "password": "qa123456"})
    assert st in (200, 201), f"setup qa1 failed {st} {qa}"
    qa_id = qa.get("id")
    st2, _ = api_req("POST", f"/api/projects/{project_id}/members", token,
                     {"user_id": qa_id, "role": "member"})
    assert st2 in (200, 201, 409), f"add member failed {st2}"

    # 清理上一次崩溃残留的 E2E 种子任务，保证幂等
    _, existing = api_req("GET", f"/api/stories/{story_id}/tasks?limit=200", token)
    for t in (existing.get("items", []) if isinstance(existing, dict) else existing):
        if (t.get("title") or "").startswith("E2E-BULK"):
            api_req("DELETE", f"/api/tasks/{t['id']}", token)

    # 种子任务
    task_ids = []
    for i in range(3):
        st3, t = api_req("POST", f"/api/stories/{story_id}/tasks", token,
                         {"project_id": project_id, "title": f"E2E-BULK-{i}",
                          "type": "task", "priority": "medium"})
        assert st3 in (200, 201), f"create task failed {st3} {t}"
        task_ids.append(t["id"])
    print(f"[seed] qa1={qa_id} tasks={task_ids}")

    page_errors, console_errors, failed = [], [], []

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        ctx = browser.new_context()
        ctx.add_init_script("localStorage.setItem('agentboard_token', %r);" % token)
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed", lambda r: failed.append(r.url)
                if r.url.endswith(".js") or r.url.endswith(".css") else None)

        page.goto(BASE + f"/story/{story_id}", wait_until="domcontentloaded")
        page.wait_for_selector(".entity-item--rich", timeout=15000)

        # 勾选全部任务 -> 批量指派
        select_all_via_checkboxes(page)
        page.wait_for_selector("button:has-text('批量指派')", timeout=5000)
        page.click("button:has-text('批量指派')")
        page.wait_for_selector(".bulk-panel--assignee", timeout=5000)

        chips = page.locator(".bulk-member-chip")
        n = chips.count()
        assert n >= 3, f"期望 >=3 个成员 chip，实际 {n}"
        av = page.locator(".bulk-member-chip .assignee-avatar-sm").count()
        assert av == n, f"头像数 {av} != chip 数 {n}"
        print(f"[ui] bulk member chips={n}, avatars={av}")

        # 搜索过滤：qa1
        page.fill(".bulk-assign-search", "qa1")
        page.wait_for_timeout(250)
        qa_chips = page.locator(".bulk-member-chip:has-text('qa1')").count()
        assert qa_chips == 1, f"搜索 'qa1' 应仅 1 个 chip，实际 {qa_chips}"

        # 搜索无匹配
        page.fill(".bulk-assign-search", "zzzz")
        page.wait_for_timeout(250)
        assert page.locator(".bulk-member-empty").count() == 1, "无匹配时应显示「无匹配成员」"
        print("[ui] search filter OK (qa1->1, zzzz->empty)")

        # 清空搜索
        page.fill(".bulk-assign-search", "")
        page.wait_for_timeout(200)

        # 点击 qa1 chip -> 批量指派
        page.locator(".bulk-member-chip:has-text('qa1')").click()
        page.wait_for_timeout(900)

        st4, tasks = api_req("GET", f"/api/stories/{story_id}/tasks?limit=200", token)
        items = tasks.get("items", tasks if isinstance(tasks, list) else [])
        by_id = {t["id"]: t for t in items}
        for tid in task_ids:
            assert by_id[tid]["assignee_id"] == qa_id, \
                f"task {tid} assignee={by_id[tid]['assignee_id']} != {qa_id}"
        print(f"[verify] bulk assign to qa1 OK for {len(task_ids)} tasks")

        # 重新勾选 -> 点击「未指派」chip -> 清除
        select_all_via_checkboxes(page)
        page.wait_for_selector("button:has-text('批量指派')", timeout=5000)
        page.click("button:has-text('批量指派')")
        page.wait_for_selector(".bulk-panel--assignee", timeout=5000)
        page.locator(".bulk-member-chip.unassigned").click()
        page.wait_for_timeout(900)

        st5, tasks2 = api_req("GET", f"/api/stories/{story_id}/tasks?limit=200", token)
        items2 = tasks2.get("items", tasks2 if isinstance(tasks2, list) else [])
        by_id2 = {t["id"]: t for t in items2}
        for tid in task_ids:
            assert by_id2[tid]["assignee_id"] is None, \
                f"task {tid} assignee 未清除: {by_id2[tid]['assignee_id']}"
        print("[verify] bulk clear (未指派) OK")

        # 错误检查
        js_css_fail = [u for u in failed if u.endswith(".js") or u.endswith(".css")]
        real_console = [c for c in console_errors if "favicon" not in c.lower()]
        assert not page_errors, f"page errors: {page_errors}"
        assert not js_css_fail, f"js/css request failed: {js_css_fail}"
        assert not real_console, f"console errors: {real_console}"
        print("[verify] 0 pageerror / console error / js-css 404")
        browser.close()

    # 清理：删除全部 E2E-BULK 种子任务 + 移除成员
    _, left = api_req("GET", f"/api/stories/{story_id}/tasks?limit=200", token)
    for t in (left.get("items", []) if isinstance(left, dict) else left):
        if (t.get("title") or "").startswith("E2E-BULK"):
            api_req("DELETE", f"/api/tasks/{t['id']}", token)
    api_req("DELETE", f"/api/projects/{project_id}/members/{qa_id}", token)
    print("[cleanup] done")


if __name__ == "__main__":
    main()
    print("ALL PASS")
