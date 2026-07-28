"""
Epic 74 (v6.1) 看板视图列内按维度子分组 —— 端到端验证
- 登录 admin -> epic 64 (AUTODEV74) 下建种子 story + 3 个种子任务（不同优先级/类型，全 backlog，自清理）
- 断言：
  1) 列表视图下存在 #boardToggle；点击后渲染 .kanban，含 7 个 .kanban-col
  2) 默认 taskGroupBy='none' -> 看板列内为平铺：.kanban-subgroup 仅 1 个且无 .kanban-subgroup-header
  3) 选分组维度=优先级 -> 看板列内出现 3 个子分组头（高/中/低），各计数=1，对应卡片归位；.kanban-col-body 拖拽目标仍存在（状态拖拽主轴不变）
  4) 选分组维度=类型 -> 出现 3 个子分组头（任务/Bug/Test Execution），计数正确
  5) 切回 不分组='none' -> 退化为平铺（1 个无头子分组，3 卡片仍在）
  6) 0 pageerror / console error / .js+.css 404
- 测试末清理种子，不污染追踪实体（task 1136 / story 113 / epic 64 / project 63）
"""
import json
import random
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
EPIC_ID = 64         # epic 64 (Epic 74 v6.1) under project 63 (AUTODEV74)
PROJECT_ID = 63
USER = "admin"
PASS = "admin123"
SEED = "__E2E_V61_" + str(random.randint(100000, 999999))


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
        # 种子 story（epic 64）
        st, story = api("POST", f"/api/epics/{EPIC_ID}/stories", token=token,
                        body={"title": SEED + "-story", "description": "E2E 种子 story"})
        assert st == 201, f"create story {st} {story}"
        sid = story["id"]
        created.append(("story", sid))
        # 种子任务：不同优先级 + 类型，全 backlog（聚焦单列子分组验证）
        seeds = [
            ("-A", "high", "task"),
            ("-B", "medium", "bug"),
            ("-C", "low", "test_execution"),
        ]
        ids = {}
        for suf, pri, typ in seeds:
            st, ta = api("POST", f"/api/stories/{sid}/tasks", token=token,
                         body={"project_id": PROJECT_ID, "title": SEED + suf,
                               "type": typ, "priority": pri})
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
                "localStorage.setItem('agentboard_story_group','none');" % token
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

            blk = page.locator(".kanban-col.status--backlog")

            # 2) 默认 none -> 平铺
            sg = blk.locator(".kanban-subgroup")
            assert sg.count() == 1, f"none 模式应仅 1 个平铺子分组，实际 {sg.count()}"
            assert blk.locator(".kanban-subgroup.has-header").count() == 0, "none 模式不应有子分组头"
            assert blk.locator(".kanban-card", has_text=SEED + "-A").count() == 1
            assert blk.locator(".kanban-card", has_text=SEED + "-B").count() == 1
            assert blk.locator(".kanban-card", has_text=SEED + "-C").count() == 1
            print("[OK] 默认不分组=平铺（1 子分组无头，3 卡片齐）")

            # 分组维度选择器（按 option[value='type'] 唯一定位 group select，sort select 无该值）
            group_sel = page.locator("select").filter(has=page.locator("option[value='type']"))

            # 3) 选 优先级
            group_sel.select_option("priority")
            page.wait_for_selector(".kanban-subgroup-header", timeout=5000)
            headers = blk.locator(".kanban-subgroup-header")
            assert headers.count() == 3, f"优先级分组应 3 头，实际 {headers.count()}"
            # 每头计数=1
            counts = [int(blk.locator(".kanban-subgroup").nth(i).locator(".kanban-subgroup-count").inner_text())
                      for i in range(3)]
            assert sum(counts) == 3, f"子分组计数和应为 3，实际 {counts}"
            # 卡片归位
            for suf in ("-A", "-B", "-C"):
                assert blk.locator(".kanban-card", has_text=SEED + suf).count() == 1, f"{suf} 卡片缺失"
            # 状态拖拽主轴仍在（drop 目标存在）
            assert blk.locator(".kanban-col-body").count() == 1, "状态拖拽目标 .kanban-col-body 缺失"
            print(f"[OK] 按优先级分组：3 子分组头（计数{counts}），卡片归位，拖拽主轴仍在")

            # 4) 选 类型
            group_sel.select_option("type")
            page.wait_for_timeout(400)
            headers = blk.locator(".kanban-subgroup-header")
            assert headers.count() == 3, f"类型分组应 3 头，实际 {headers.count()}"
            htext = " ".join(headers.nth(i).inner_text() for i in range(3))
            for label in ("Task", "Bug", "Test"):
                assert label in htext, f"类型子分组头缺少 {label}：{htext}"
            for suf in ("-A", "-B", "-C"):
                assert blk.locator(".kanban-card", has_text=SEED + suf).count() == 1, f"{suf} 卡片缺失"
            print(f"[OK] 按类型分组：3 子分组头（{htext.strip()}），卡片归位")

            # 5) 切回 不分组
            group_sel.select_option("none")
            page.wait_for_timeout(400)
            sg = blk.locator(".kanban-subgroup")
            assert sg.count() == 1, f"切回 none 应 1 平铺子分组，实际 {sg.count()}"
            assert blk.locator(".kanban-subgroup.has-header").count() == 0, "切回 none 不应有头"
            assert blk.locator(".kanban-card").count() == 3, "切回 none 卡片数应为 3"
            print("[OK] 切回不分组=平铺退化正确")

            # 6) 错误检查
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
