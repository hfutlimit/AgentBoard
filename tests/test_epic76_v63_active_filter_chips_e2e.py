"""
Epic 76 (v6.3) 看板/列表视图「激活筛选条件」可视化 chips 条 —— 端到端验证
- 登录 admin -> epic 66 (AUTODEV76) 下建种子 story + 3 个种子任务（全 backlog，自清理）
- 断言：
  1) 初始无筛选 -> .active-filter-bar 不渲染
  2) 点状态快速筛选 chip -> .active-filter-bar 出现，含「状态 ·」chip；列表仍渲染种子任务
  3) 点该 chip（✕）移除 -> .active-filter-bar 消失（单条筛选移除生效）
  4) 再次点状态 chip -> 切到看板视图后 .active-filter-bar 仍在（列表/看板共用，筛选联动可视化）
  5) 点「全部清除」-> 看板视图下 .active-filter-bar 也消失
  6) 列表视图：搜索输入 -> 「搜索 ·」chip；再点状态 chip -> 2 chip；点「搜索」chip 移除 -> 剩 1 chip；全部清除 -> 消失
  7) 0 pageerror / console error / .js+.css 404
- 测试末清理种子，不污染追踪实体（task 1138 / story 115 / epic 66 / project 65）
"""
import json
import random
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8080"
API = "http://127.0.0.1:58125"
EPIC_ID = 66         # epic 66 (Epic 76 v6.3) under project 65 (AUTODEV76)
PROJECT_ID = 65
USER = "admin"
PASS = "admin123"
SEED = "__E2E_V63_" + str(random.randint(100000, 999999))


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
        # 种子 story（epic 66）
        st, story = api("POST", f"/api/epics/{EPIC_ID}/stories", token=token,
                        body={"title": SEED + "-story", "description": "E2E 种子 story"})
        assert st == 201, f"create story {st} {story}"
        sid = story["id"]
        created.append(("story", sid))
        seeds = [("-A", "high", "task"), ("-B", "medium", "bug"), ("-C", "low", "test_execution")]
        for suf, pri, typ in seeds:
            st, ta = api("POST", f"/api/stories/{sid}/tasks", token=token,
                         body={"project_id": PROJECT_ID, "title": SEED + suf,
                               "type": typ, "priority": pri})
            assert st == 201, f"create {suf} {st} {ta}"
            created.append(("task", ta["id"]))
        print(f"[seed] story={sid}")

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-proxy-server"])
            page = browser.new_page()
            # API 注入端口 58124 -> 58125 重定向（web_app 默认 API_URL=58124）
            page.route("**://127.0.0.1:58124/**",
                        lambda r: r.continue_(url=r.request.url.replace("58124", "58125")))
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

            page.goto(WEB + f"/story/{sid}", wait_until="domcontentloaded")
            page.wait_for_selector("#boardToggle", timeout=20000)
            page.wait_for_selector(".entity-list", timeout=10000)
            print("[OK] 列表视图渲染")

            # 1) 初始无筛选 -> bar 不渲染
            assert page.locator(".active-filter-bar").count() == 0, "初始不应渲染筛选 chips 条"
            print("[OK] 初始无筛选 -> chips 条隐藏")

            # 2) 点状态快速筛选 chip（第一个真实状态=backlog）
            page.locator(".chips .chip").nth(1).click()
            page.wait_for_selector(".active-filter-bar", state="visible", timeout=5000)
            chips = page.locator(".active-filter-chip")
            assert chips.count() >= 1, "应用状态筛选后应出现 >=1 chip"
            assert chips.filter(has_text="状态").count() == 1, "应含「状态 ·」chip"
            assert page.locator(".entity-item", has_text=SEED).count() >= 1, "列表仍应渲染种子任务"
            print(f"[OK] 状态筛选 -> chips 条出现（{chips.count()} chip），列表任务仍在")

            # 3) 点该 chip 移除 -> bar 消失
            chips.filter(has_text="状态").click()
            page.wait_for_timeout(300)
            assert page.locator(".active-filter-bar").count() == 0, "移除状态 chip 后 chips 条应消失"
            print("[OK] 点 chip ✕ 移除单条筛选 -> chips 条消失")

            # 4) 再次应用状态筛选 -> 切看板视图后 bar 仍在（列表/看板共用）
            page.locator(".chips .chip").nth(1).click()
            page.wait_for_selector(".active-filter-bar", state="visible", timeout=5000)
            page.click("#boardToggle")
            page.wait_for_selector(".kanban", state="visible", timeout=8000)
            assert page.locator(".kanban-col").count() == 7, "看板应 7 列"
            assert page.locator(".active-filter-bar").count() == 1, "看板视图下筛选 chips 条应仍可见"
            assert page.locator(".active-filter-chip").filter(has_text="状态").count() == 1
            print("[OK] 看板视图下筛选 chips 条联动可见（共 7 列）")

            # 5) 看板视图下「全部清除」-> bar 消失
            page.locator(".active-filter-bar__clear").click()
            page.wait_for_timeout(300)
            assert page.locator(".active-filter-bar").count() == 0, "看板视图下全部清除后 chips 条应消失"
            print("[OK] 看板视图「全部清除」-> chips 条消失")

            # 6) 列表视图：搜索 + 状态多 chip + 单条移除
            page.click("#boardToggle")  # 切回列表
            page.wait_for_selector(".entity-list", timeout=8000)
            page.locator('input[aria-label="搜索任务"]').fill(SEED)
            page.wait_for_selector(".active-filter-bar", state="visible", timeout=5000)
            assert page.locator(".active-filter-chip").filter(has_text="搜索").count() == 1, "搜索 chip 应出现"
            page.locator(".chips .chip").nth(1).click()  # 再加状态筛选
            page.wait_for_timeout(300)
            assert page.locator(".active-filter-chip").count() == 2, "应同时有搜索+状态 2 个 chip"
            page.locator(".active-filter-chip").filter(has_text="搜索").click()
            page.wait_for_timeout(300)
            assert page.locator(".active-filter-chip").count() == 1, "移除搜索 chip 后应剩 1 个"
            assert page.locator(".active-filter-chip").filter(has_text="状态").count() == 1
            page.locator(".active-filter-bar__clear").click()
            page.wait_for_timeout(300)
            assert page.locator(".active-filter-bar").count() == 0, "全部清除后 chips 条应消失"
            print("[OK] 列表视图：搜索+状态多 chip、单条移除、全部清除 均正常")

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
