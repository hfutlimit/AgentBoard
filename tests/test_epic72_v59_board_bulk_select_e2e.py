"""
Epic 72 (v5.9) 看板视图批量操作（卡片多选 + 复用批量工具栏）—— 端到端验证
- 登录 admin -> 在 epic 60 (project 59) 下建种子 story + 3 个种子任务（状态 backlog/todo/in_progress，自清理）
- 断言：
  1) 列表视图下 #boardToggle 存在；点击后渲染 .kanban（7 列）
  2) 每张看板卡片含 .kanban-card-check 选择框；点击选择框 -> 卡片加 .selected，且不触发快速查看抽屉
  3) 勾选 2 张卡片 -> 共享 .bulk-action-bar 出现并显示「2 项已选」（看板视图复用列表批量工具栏）
  4) 点「批量修改状态」-> 合法状态面板出现；点「完成」-> API 复核两张任务 status 均变为 done
  5) Esc 清除选择 -> .bulk-action-bar 消失
  6) 切回列表视图正常
  7) 0 pageerror / console error / .js+.css 404
- 测试末清理种子，不污染追踪实体
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
SEED = "__E2E_V59_" + str(random.randint(100000, 999999))


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
        st, story = api("POST", f"/api/epics/{EPIC_ID}/stories", token=token,
                        body={"title": SEED + "-story", "description": "E2E 种子 story"})
        assert st == 201, f"create story {st} {story}"
        sid = story["id"]
        created.append(("story", sid))
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

            # 2) 每张卡片含选择框；点击选择框 -> .selected 且不开抽屉
            card_a = page.locator(".kanban-col.status--backlog .kanban-card", has_text=SEED + "-A")
            assert card_a.locator(".kanban-card-check").count() == 1, "卡片 A 缺选择框"
            card_b = page.locator(".kanban-col.status--todo .kanban-card", has_text=SEED + "-B")
            assert card_b.locator(".kanban-card-check").count() == 1, "卡片 B 缺选择框"
            assert page.locator(".bulk-action-bar").count() == 0, "选择前不应出现批量工具栏"
            card_a.locator(".kanban-card-check").click()
            page.wait_for_timeout(300)
            assert card_a.evaluate("el => el.classList.contains('selected')"), "卡片 A 未进入选中态"
            assert page.locator(".quick-view-drawer").count() == 0, "点击选择框不应打开快速查看抽屉"
            assert page.locator(".bulk-action-bar").count() == 1, "选中后批量工具栏未出现"
            assert "1 项已选" in page.locator(".bulk-action-bar").inner_text(), "批量计数应为 1"
            print("[OK] 卡片多选框可勾选 -> 选中态 + 共用批量工具栏，且不触发抽屉")

            # 取消 A 的选中（再次点击），回到 0
            card_a.locator(".kanban-card-check").click()
            page.wait_for_timeout(200)
            assert page.locator(".bulk-action-bar").count() == 0, "取消选中后批量工具栏应消失"
            print("[OK] 再次点击取消选中 -> 批量工具栏消失")

            # 3) 勾选 B(todo) + C(in_progress) -> 两任务共同可流转目标含「完成」
            card_b = page.locator(".kanban-col.status--todo .kanban-card", has_text=SEED + "-B")
            card_c = page.locator(".kanban-col.status--in_progress .kanban-card", has_text=SEED + "-C")
            card_b.locator(".kanban-card-check").click()
            page.wait_for_timeout(200)
            card_c.locator(".kanban-card-check").click()
            page.wait_for_timeout(200)
            assert "2 项已选" in page.locator(".bulk-action-bar").inner_text(), "批量计数应为 2"
            print("[OK] 勾选 2 张卡片 -> 批量工具栏显示「2 项已选」")

            # 4) 批量修改状态 -> 合法状态面板 -> 选「完成」-> API 复核两张均 done
            page.locator(".bulk-action-bar button:has-text('批量修改状态')").click()
            page.wait_for_selector(".bulk-panel .status-btn", timeout=5000)
            done_btn = page.locator(".bulk-panel .status-btn.status--done")
            assert done_btn.count() == 1, "批量状态面板缺「完成」按钮（受状态机限制）"
            done_btn.click()
            page.wait_for_timeout(1000)
            st_b, tb_sk = api("GET", f"/api/tasks/{b_id}", token=token)
            st_c, tc_sk = api("GET", f"/api/tasks/{c_id}", token=token)
            assert st_b == 200 and tb_sk.get("status") == "done", f"B 状态应为 done，实际 {tb_sk.get('status')}"
            assert st_c == 200 and tc_sk.get("status") == "done", f"C 状态应为 done，实际 {tc_sk.get('status')}"
            print(f"[OK] 批量修改状态 -> 两张任务 API 复核 status=done")

            # 5) Esc 清除选择 -> 批量工具栏消失
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            assert page.locator(".bulk-action-bar").count() == 0, "Esc 后批量工具栏应消失"
            print("[OK] Esc 清除选择 -> 批量工具栏消失")

            # 6) 切回列表视图
            page.click("#boardToggle")
            page.wait_for_selector(".entity-list", state="visible", timeout=8000)
            assert page.locator(".kanban").count() == 0, "切回列表后看板未消失"
            print("[OK] 切回列表视图")

            # 7) 错误检查
            assert not errors, "存在 JS/控制台错误:\n" + "\n".join(errors)
            assert not js_css_fail, "存在 .js/.css 加载失败:\n" + "\n".join(js_css_fail)
            print("[OK] 0 pageerror / console error / .js+.css 404")

            browser.close()
        print("ALL PASS")
    finally:
        for kind, _id in created:
            if kind == "task":
                api("DELETE", f"/api/tasks/{_id}", token=token)
            else:
                api("DELETE", f"/api/stories/{_id}", token=token)
        if errors or js_css_fail:
            print("ERRORS:", errors, js_css_fail)


if __name__ == "__main__":
    main()
