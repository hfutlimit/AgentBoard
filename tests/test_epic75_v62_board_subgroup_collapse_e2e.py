"""
Epic 75 (v6.2) 看板视图子分组头折叠/展开 —— 端到端验证
- 登录 admin -> epic 65 (AUTODEV75) 下建种子 story + 3 个种子任务（不同优先级，全 backlog，自清理）
- 断言：
  1) 列表视图下存在 #boardToggle；点击后渲染 .kanban，含 7 个 .kanban-col
  2) 分组维度=优先级 -> 看板 backlog 列内出现 3 个子分组头（高/中/低），各计数=1
  3) 列头出现「折叠/展开全部子分组」按钮（hasSubgroups 为真）；状态拖拽主轴 .kanban-col-body 仍在
  4) 点击某个子分组头 -> 该子分组折叠（header 加 .collapsed、body 隐藏、可见卡片 -1）
  5) 再次点击同子分组头 -> 展开（body 恢复、卡片恢复）
  6) 点击列头「全部折叠」-> 三子分组全折叠（无 body、无卡片）；再点「全部展开」-> 全恢复
  7) 折叠一个子分组后 reload -> 仍折叠（localStorage agentboard_collapsed_subgroups 持久化）
  8) 0 pageerror / console error / .js+.css 404
- 测试末清理种子，不污染追踪实体（task 1137 / story 114 / epic 65 / project 64）
"""
import json
import random
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
EPIC_ID = 65         # epic 65 (Epic 75 v6.2) under project 64 (AUTODEV75)
PROJECT_ID = 64
USER = "admin"
PASS = "admin123"
SEED = "__E2E_V62_" + str(random.randint(100000, 999999))


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
    try:
        st, story = api("POST", f"/api/epics/{EPIC_ID}/stories", token=token,
                        body={"title": SEED + "-story", "description": "E2E 种子 story"})
        assert st == 201, f"create story {st} {story}"
        sid = story["id"]
        created.append(("story", sid))
        seeds = [("-A", "high"), ("-B", "medium"), ("-C", "low")]
        ids = {}
        for suf, pri in seeds:
            st, ta = api("POST", f"/api/stories/{sid}/tasks", token=token,
                         body={"project_id": PROJECT_ID, "title": SEED + suf, "priority": pri})
            assert st == 201, f"create {suf} {st} {ta}"
            ids[suf] = ta["id"]
            created.append(("task", ta["id"]))
        print(f"[seed] story={sid} tasks={ids}")

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-proxy-server"])
            page = browser.new_page()
            init = (
                "localStorage.setItem('agentboard_token','%s');"
                "localStorage.setItem('agentboard_user','admin');"
                "localStorage.setItem('agentboard_story_view','list');"
                "localStorage.setItem('agentboard_story_group','priority');" % token
            )
            page.add_init_script(init)
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: (
                js_css_fail.append(r.url) if (r.url.endswith(".js") or r.url.endswith(".css")) else None
            ))

            page.goto(WEB + f"/story/{sid}", wait_until="domcontentloaded")
            page.wait_for_selector("#boardToggle", timeout=20000)
            page.wait_for_selector(".entity-list", timeout=10000)
            print("[OK] 列表视图渲染")

            # 1) 看板视图
            page.click("#boardToggle")
            page.wait_for_selector(".kanban", state="visible", timeout=8000)
            cols = page.locator(".kanban-col")
            assert cols.count() == 7, f"看板列数应为 7，实际 {cols.count()}"
            print(f"[OK] 看板视图渲染，列数={cols.count()}")

            blk = page.locator(".kanban-col.status--backlog")

            # 2) 优先级分组 -> 3 子分组头
            headers = blk.locator(".kanban-subgroup-header")
            assert headers.count() == 3, f"优先级分组应 3 头，实际 {headers.count()}"
            counts = [int(blk.locator(".kanban-subgroup").nth(i).locator(".kanban-subgroup-count").inner_text())
                      for i in range(3)]
            assert sum(counts) == 3, f"子分组计数和应为 3，实际 {counts}"
            for suf in ("-A", "-B", "-C"):
                assert blk.locator(".kanban-card", has_text=SEED + suf).count() == 1, f"{suf} 卡片缺失"
            print(f"[OK] 按优先级分组：3 子分组头（计数{counts}），卡片归位")

            # 3) 列头出现「折叠/展开全部」按钮 + 拖拽主轴仍在
            assert blk.locator(".kanban-col-subgroups-toggle").count() == 1, "列头缺少子分组折叠按钮"
            assert blk.locator(".kanban-col-body").count() == 1, "状态拖拽目标 .kanban-col-body 缺失"
            print("[OK] 列头子分组折叠按钮存在；状态拖拽主轴仍在")

            h0 = headers.nth(0)

            # 4) 折叠第一个子分组
            h0.click()
            page.wait_for_timeout(200)
            assert h0.evaluate("el => el.classList.contains('collapsed')"), "点击后子分组头应带 collapsed"
            assert blk.locator(".kanban-subgroup-body").count() == 2, f"折叠后 body 应为 2，实际 {blk.locator('.kanban-subgroup-body').count()}"
            assert blk.locator(".kanban-card").count() == 2, f"折叠后可见卡片应为 2，实际 {blk.locator('.kanban-card').count()}"
            print("[OK] 单子分组折叠：header.collapsed + body 隐藏 + 可见卡片减 1")

            # 5) 再次点击展开
            h0.click()
            page.wait_for_timeout(200)
            assert not h0.evaluate("el => el.classList.contains('collapsed')"), "再次点击应展开"
            assert blk.locator(".kanban-subgroup-body").count() == 3, "展开后 body 应恢复 3"
            assert blk.locator(".kanban-card").count() == 3, "展开后卡片应恢复 3"
            print("[OK] 单子分组再次点击展开：全部恢复")

            # 6) 列头「全部折叠」->「全部展开」
            toggle = blk.locator(".kanban-col-subgroups-toggle")
            toggle.click()
            page.wait_for_timeout(200)
            assert blk.locator(".kanban-subgroup-header.collapsed").count() == 3, "全部折叠后应有 3 个 collapsed 头"
            assert blk.locator(".kanban-subgroup-body").count() == 0, "全部折叠后 body 应为 0"
            assert blk.locator(".kanban-card").count() == 0, "全部折叠后卡片应为 0"
            toggle.click()
            page.wait_for_timeout(200)
            assert blk.locator(".kanban-subgroup-header.collapsed").count() == 0, "全部展开后 collapsed 头应为 0"
            assert blk.locator(".kanban-card").count() == 3, "全部展开后卡片应为 3"
            print("[OK] 列头全部折叠/全部展开 切换正确")

            # 7) 持久化：折叠一个后 reload
            h0.click()
            page.wait_for_timeout(150)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#boardToggle", timeout=20000)
            page.click("#boardToggle")
            page.wait_for_selector(".kanban-subgroup-header", timeout=8000)
            blk2 = page.locator(".kanban-col.status--backlog")
            h0b = blk2.locator(".kanban-subgroup-header").nth(0)
            assert h0b.evaluate("el => el.classList.contains('collapsed')"), "reload 后子分组应仍折叠（持久化）"
            assert blk2.locator(".kanban-subgroup-body").count() == 2, "reload 后 body 应为 2（持久化折叠）"
            print("[OK] 折叠状态 reload 后持久化（localStorage agentboard_collapsed_subgroups）")

            # 8) 错误检查
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
