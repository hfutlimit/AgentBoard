"""
Epic 69 (v5.6) 命令面板接入后端搜索 —— 端到端验证
- 登录 admin -> project 57 (AUTODEV69) epic 59 下建种子 story + 唯一标题任务（自清理）
- 进入 /projects 确保 projects() 已装载
- 断言：
  1) Ctrl+K 打开命令面板
  2) 输入唯一任务 token -> 后端搜索返回「任务」分类结果（cat-task），标题含 token；点击 -> 跳转到 /task/{id} 且面板关闭
  3) 重开面板 -> 输入「AUTODEV69」-> 出现「项目」分类结果（cat-project）；点击 -> 跳转到 /project/57
  4) 输入无匹配 token -> 显示「无匹配命令」空态（搜索完成后）
  5) 0 pageerror / console error / .js+.css 404
- 测试末清理种子，不污染追踪实体
"""
import json
import random
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
PROJECT_ID = 57
EPIC_ID = 59
USER = "admin"
PASS = "admin123"
SEED = "__E2E_PSEARCH__" + str(random.randint(100000, 999999))


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
        # 种子 story + 唯一标题任务（project 57 在 projects() 池中，便于项目搜索也覆盖）
        st, story = api("POST", f"/api/epics/{EPIC_ID}/stories", token=token,
                        body={"title": SEED + "-story", "description": "E2E 种子 story"})
        assert st == 201, f"create story {st} {story}"
        sid = story["id"]
        created.append(("story", sid))
        st, task = api("POST", f"/api/stories/{sid}/tasks", token=token,
                       body={"project_id": PROJECT_ID, "story_id": sid,
                             "title": SEED + "-task", "type": "task", "priority": "medium"})
        assert st == 201, f"create task {st} {task}"
        tid = task["id"]
        created.append(("task", tid))
        print(f"[seed] story={sid} task={tid} token={SEED}")

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-proxy-server"])
            page = browser.new_page()
            init = (
                "localStorage.setItem('agentboard_token','%s');"
                "localStorage.setItem('agentboard_user','admin');" % token
            )
            page.add_init_script(init)
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: (
                js_css_fail.append(r.url) if (r.url.endswith(".js") or r.url.endswith(".css")) else None
            ))

            # 装载 projects()
            page.goto(WEB + "/projects", wait_until="domcontentloaded")
            page.wait_for_selector("#command-palette-toggle", timeout=20000)

            # 1) Ctrl+K 打开面板
            page.keyboard.press("Control+k")
            page.wait_for_selector(".command-palette", state="visible", timeout=5000)
            print("[OK] Ctrl+K 打开命令面板")

            # 2) 后端任务搜索：输入唯一 token
            page.fill("#paletteInput", SEED)
            page.wait_for_selector(".palette-item-cat.cat-task", timeout=8000)
            task_items = page.locator(".palette-item-cat.cat-task")
            assert task_items.count() >= 1, "未出现任务搜索结果"
            first_title = page.locator(".palette-item", has_text=SEED).first.inner_text()
            assert SEED in first_title, f"任务结果标题不含 token: {first_title}"
            print(f"[OK] 任务搜索出现 {task_items.count()} 条结果，首项={first_title.strip()}")

            # 点击任务结果 -> 跳转到 /task/{id}
            page.locator(".palette-item", has_text=SEED).first.click()
            page.wait_for_timeout(600)
            assert f"/task/{tid}" in page.url, f"未跳转到 /task/{tid}，当前 {page.url}"
            assert page.locator(".command-palette").count() == 0, "跳转后面板未关闭"
            print(f"[OK] 点击任务结果跳转到 {page.url}")

            # 3) 重开面板 -> 项目搜索
            page.keyboard.press("Control+k")
            page.wait_for_selector(".command-palette", state="visible", timeout=5000)
            page.fill("#paletteInput", "AUTODEV69")
            page.wait_for_selector(".palette-item-cat.cat-project", timeout=8000)
            proj_items = page.locator(".palette-item-cat.cat-project")
            assert proj_items.count() >= 1, "未出现项目搜索结果"
            proj_title = page.locator(".palette-item", has_text="AUTODEV69").first.inner_text()
            print(f"[OK] 项目搜索出现 {proj_items.count()} 条结果，首项={proj_title.strip()}")
            page.locator(".palette-item", has_text="AUTODEV69").first.click()
            page.wait_for_timeout(600)
            assert f"/project/{PROJECT_ID}" in page.url, f"未跳转到 /project/{PROJECT_ID}，当前 {page.url}"
            assert page.locator(".command-palette").count() == 0, "跳转后面板未关闭"
            print(f"[OK] 点击项目结果跳转到 {page.url}")

            # 4) 无匹配空态（等待后端搜索结算，确认显示「无匹配命令」而非「搜索中…」）
            page.keyboard.press("Control+k")
            page.wait_for_selector(".command-palette", state="visible", timeout=5000)
            page.fill("#paletteInput", "zzzqqq_nomatch_xyz")
            # 等待搜索指示器消失（paletteSearching 置 false），最长 5s
            try:
                page.wait_for_function("!document.querySelector('.command-palette-spinner')", timeout=5000)
            except Exception:
                pass
            page.wait_for_timeout(150)
            empty = page.locator(".command-palette-empty")
            assert empty.count() == 1, "无匹配时未显示空态"
            empty_text = empty.first.inner_text()
            assert "无匹配" in empty_text, f"空态文案异常: {empty_text}"
            print(f"[OK] 无匹配显示空态: {empty_text}")

            # 5) 错误检查
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
