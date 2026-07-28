"""
Epic 71 (v5.8) 任务列表看板视图渲染 —— 端到端验证
- 登录 admin -> project 59 (AUTODEV70) epic 60 下建种子 story + 3 个种子任务（状态 backlog/todo/in_progress，自清理）
- 断言：
  1) 列表视图下存在 #boardToggle 切换按钮；点击后渲染 .kanban，含 7 个 .kanban-col（按 statuses 分桶）
  2) 各状态列正确渲染对应种子卡片（按标题定位，规避 tasks() 全项目竞态）
  3) 点击卡片打开快速查看抽屉（.quick-view-drawer），Esc 关闭
  4) 拖拽 backlog 卡片到 done 列 -> API 复核该任务 status 变为 done（拖拽改状态）
  5) 再次点击 #boardToggle -> 回到列表视图（.kanban 消失，.entity-list 出现）
  6) 0 pageerror / console error / .js+.css 404
- 测试末清理种子，不污染追踪实体（task 1126 / story 110 / epic 61 / project 60）
"""
import json
import random
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
EPIC_ID = 60          # project 59 (AUTODEV70)
USER = "admin"
PASS = "admin123"
SEED = "__E2E_V58_" + str(random.randint(100000, 999999))


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
    token, _ = login()
    created = []
    errors = []
    js_css_fail = []
    a_id = b_id = c_id = None
    try:
        # 种子 story（epic 60）
        st, story = api("POST", f"/api/epics/{EPIC_ID}/stories", token=token,
                        body={"title": SEED + "-story", "description": "E2E 种子 story"})
        assert st == 201, f"create story {st} {story}"
        sid = story["id"]
        created.append(("story", sid))
        # 种子任务（默认 backlog）
        st, ta = api("POST", f"/api/stories/{sid}/tasks", token=token,
                     body={"project_id": 59, "title": SEED + "-A backlog", "type": "task", "priority": "medium"})
        assert st == 201, f"create A {st}"
        a_id = ta["id"]; created.append(("task", a_id))
        st, tb = api("POST", f"/api/stories/{sid}/tasks", token=token,
                     body={"project_id": 59, "title": SEED + "-B todo", "type": "bug", "priority": "high"})
        assert st == 201, f"create B {st}"
        b_id = tb["id"]; created.append(("task", b_id))
        st, tc = api("POST", f"/api/stories/{sid}/tasks", token=token,
                     body={"project_id": 59, "title": SEED + "-C in_progress", "type": "task", "priority": "low"})
        assert st == 201, f"create C {st}"
        c_id = tc["id"]; created.append(("task", c_id))
        # 设置状态：B backlog->todo；C backlog->todo->in_progress
        api("PUT", f"/api/tasks/{b_id}/status", token=token, body={"status": "todo"})
        api("PUT", f"/api/tasks/{c_id}/status", token=token, body={"status": "todo"})
        api("PUT", f"/api/tasks/{c_id}/status", token=token, body={"status": "in_progress"})
        print(f"[seed] story={sid} tasks A={a_id} B={b_id} C={c_id}")

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-proxy-server"])
            page = browser.new_page()
            init = (
                "localStorage.setItem('agentboard_token','%s');"
                "localStorage.setItem('agentboard_user','admin');"
                "localStorage.setItem('agentboard_story_view','list');" % token
            )
            page.add_init_script(init)
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: (
                js_css_fail.append(r.url) if (r.url.endswith(".js") or r.url.endswith(".css")) else None
            ))

            # 进入种子 story（列表视图）
            page.goto(WEB + f"/story/{sid}", wait_until="domcontentloaded")
            page.wait_for_selector("#boardToggle", timeout=20000)
            page.wait_for_selector(".entity-list", timeout=10000)
            print("[OK] 列表视图渲染")

            # 1) 切到看板视图
            page.click("#boardToggle")
            page.wait_for_selector(".kanban", state="visible", timeout=8000)
            cols = page.locator(".kanban-col")
            assert cols.count() == 7, f"看板列数应为 7，实际 {cols.count()}"
            print(f"[OK] 看板视图渲染，列数={cols.count()}")

            # 2) 各状态列渲染对应种子卡片
            blk = page.locator(".kanban-col.status--backlog")
            todo = page.locator(".kanban-col.status--todo")
            inp = page.locator(".kanban-col.status--in_progress")
            assert blk.locator(".kanban-card", has_text=SEED + "-A").count() == 1, "backlog 列缺少种子 A"
            assert todo.locator(".kanban-card", has_text=SEED + "-B").count() == 1, "todo 列缺少种子 B"
            assert inp.locator(".kanban-card", has_text=SEED + "-C").count() == 1, "in_progress 列缺少种子 C"
            # 列头计数徽章存在
            assert blk.locator(".kanban-col-count").count() == 1, "backlog 列缺计数徽章"
            print("[OK] 各状态列正确渲染种子卡片 + 计数徽章")

            # 3) 点击卡片打开快速查看抽屉
            blk.locator(".kanban-card", has_text=SEED + "-A").click()
            page.wait_for_selector(".quick-view-drawer", state="visible", timeout=5000)
            assert SEED + "-A" in page.locator(".quick-view-drawer").inner_text(), "抽屉未显示任务标题"
            page.keyboard.press("Escape")
            page.wait_for_selector(".quick-view-drawer", state="detached", timeout=5000)
            print("[OK] 点击卡片打开快速查看抽屉 + Esc 关闭")

            # 4) 拖拽 backlog 卡片到 todo 列（合法迁移 BACKLOG->TODO）-> 状态变更（API 复核）
            src_sel = f'.kanban-col.status--backlog .kanban-card:has-text("{SEED}-A")'
            dst_sel = ".kanban-col.status--todo .kanban-col-body"
            page.drag_and_drop(src_sel, dst_sel)
            page.wait_for_timeout(800)
            st2, task_a = api("GET", f"/api/tasks/{a_id}", token=token)
            assert st2 == 200 and task_a.get("status") == "todo", f"拖拽后 A 状态应为 todo，实际 {task_a.get('status')}"
            print(f"[OK] 拖拽 backlog->todo，API 复核 status={task_a.get('status')}")

            # 5) 切回列表视图
            page.click("#boardToggle")
            page.wait_for_selector(".entity-list", state="visible", timeout=8000)
            assert page.locator(".kanban").count() == 0, "切回列表后看板未消失"
            print("[OK] 切回列表视图")

            # 6) 错误检查
            assert not errors, "存在 JS/控制台错误:\n" + "\n".join(errors)
            assert not js_css_fail, "存在 .js/.css 加载失败:\n" + "\n".join(js_css_fail)
            print("[OK] 0 pageerror / console error / .js+.css 404")

            browser.close()
        print("ALL PASS")
    finally:
        # 还原视图为列表，避免影响其它测试
        for kind, _id in created:
            if kind == "task":
                api("DELETE", f"/api/tasks/{_id}", token=token)
            else:
                api("DELETE", f"/api/stories/{_id}", token=token)
        if errors or js_css_fail:
            print("ERRORS:", errors, js_css_fail)


if __name__ == "__main__":
    main()
