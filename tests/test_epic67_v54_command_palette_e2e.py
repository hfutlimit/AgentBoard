"""
Epic 67 (v5.4) 命令面板 (Ctrl/Cmd+K) —— 端到端验证
- 登录 admin -> epic 56 (project 53) 下建种子 story + 3 种子任务（自清理）
- 进入该 story 任务视图
- 断言：
  1) 顶栏 #command-palette-toggle 存在；点击打开 .command-palette，命令项 >= 10
  2) 输入「密度」过滤：列表收敛（<=4 项）且首项含「切换行密度」
  3) 在过滤态按 Enter -> 执行密度切换（.entity-list.density-compact 状态改变）+ 面板关闭
  4) 重开 + Esc -> 面板关闭
  5) Ctrl+K -> 面板打开（全局快捷键）
  6) 输入「项目」-> 点击「项目列表」-> URL 跳转到 /projects + 面板关闭
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
EPIC_ID = 56
PROJECT_ID = 53
USER = "admin"
PASS = "admin123"
SEED = "__E2E_CMD_PALETTE__" + str(random.randint(100000, 999999))


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
        # 种子 story + 任务（自清理）
        st, story = api("POST", f"/api/epics/{EPIC_ID}/stories", token=token,
                        body={"title": SEED + "-story", "description": "E2E 种子 story"})
        assert st == 201, f"create story {st} {story}"
        sid = story["id"]
        created.append(("story", sid))
        for i in range(3):
            st, task = api("POST", f"/api/stories/{sid}/tasks", token=token,
                           body={"project_id": PROJECT_ID, "story_id": sid,
                                 "title": SEED + f"-task-{i}", "type": "task", "priority": "medium"})
            assert st == 201, f"create task {st} {task}"
            created.append(("task", task["id"]))

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-proxy-server"])
            page = browser.new_page()
            init = (
                "localStorage.setItem('agentboard_token','%s');"
                "localStorage.setItem('agentboard_user','admin');"
                "localStorage.setItem('agentboard_list_density','comfortable');" % token
            )
            page.add_init_script(init)
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: (
                js_css_fail.append(r.url) if (r.url.endswith(".js") or r.url.endswith(".css")) else None
            ))

            page.goto(WEB + "/story/" + str(sid), wait_until="domcontentloaded")
            page.wait_for_selector("#command-palette-toggle", timeout=20000)
            page.wait_for_selector(".entity-list", timeout=20000)

            # 1) 打开面板
            page.click("#command-palette-toggle")
            page.wait_for_selector(".command-palette", state="visible", timeout=5000)
            items = page.locator(".palette-item")
            assert items.count() >= 10, f"期望 >=10 条命令，实际 {items.count()}"
            print(f"[OK] 命令面板打开，命令数={items.count()}")

            # 2) 过滤
            page.fill("#paletteInput", "密度")
            page.wait_for_timeout(250)
            filtered = page.locator(".palette-item")
            assert 1 <= filtered.count() <= 4, f"过滤后命令数异常: {filtered.count()}"
            first_text = filtered.first.inner_text()
            assert "切换行密度" in first_text, f"过滤首项非密度命令: {first_text}"
            print(f"[OK] 输入「密度」过滤收敛至 {filtered.count()} 项，首项={first_text.strip()}")

            # 3) Enter 执行密度切换
            compact_before = page.locator(".entity-list.density-compact").count()
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            assert page.locator(".command-palette").count() == 0, "Enter 后面板未关闭"
            compact_after = page.locator(".entity-list.density-compact").count()
            assert compact_before != compact_after, "Enter 未切换行密度"
            print(f"[OK] Enter 执行密度切换（compact {compact_before} -> {compact_after}），面板已关闭")

            # 4) 重开 + Esc 关闭
            page.click("#command-palette-toggle")
            page.wait_for_selector(".command-palette", state="visible", timeout=5000)
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            assert page.locator(".command-palette").count() == 0, "Esc 未关闭面板"
            print("[OK] Esc 关闭面板")

            # 5) Ctrl+K 打开
            page.keyboard.press("Control+k")
            page.wait_for_selector(".command-palette", state="visible", timeout=5000)
            print("[OK] Ctrl+K 打开面板")

            # 6) 导航：输入「项目」-> 点击「项目列表」
            page.fill("#paletteInput", "项目")
            page.wait_for_timeout(200)
            page.locator(".palette-item", has_text="项目列表").click()
            page.wait_for_timeout(600)
            assert "/projects" in page.url, f"导航未跳转到 /projects，当前 {page.url}"
            assert page.locator(".command-palette").count() == 0, "导航后面板未关闭"
            print(f"[OK] 命令面板导航到项目列表，URL={page.url}")

            # 7) 错误检查
            assert not errors, "存在 JS/控制台错误:\n" + "\n".join(errors)
            assert not js_css_fail, "存在 .js/.css 加载失败:\n" + "\n".join(js_css_fail)
            print("[OK] 0 pageerror / console error / .js+.css 404")

            browser.close()
        print("ALL PASS")
    finally:
        # 清理种子
        for kind, _id in created:
            if kind == "task":
                api("DELETE", f"/api/tasks/{_id}", token=token)
            else:
                api("DELETE", f"/api/stories/{_id}", token=token)
        if errors or js_css_fail:
            print("ERRORS:", errors, js_css_fail)


if __name__ == "__main__":
    main()
