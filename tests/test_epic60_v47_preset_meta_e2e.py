"""
Epic 60 (v4.7) 筛选预设可视化标签 —— 端到端验证
- 登录 admin -> 在 Epic 60 (epic 50 / project 46) 下建种子 story + 1 个种子任务
- 进入该 story 任务视图，将分组维度设为「按状态」
- 打开预设面板，保存当前筛选为命名预设
- 断言：预设项渲染分组维度标签（📂 按状态）+ 排序维度标签（↕ ... ↓/↑），即 meta chips 生效
- 断言：0 pageerror / console error / .js+.css 404
- 测试末清理种子任务与种子 story，不污染追踪实体（task 1110 / story 99 / epic 50 保留）
"""
import json
import random
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
EPIC_ID = 50
PROJECT_ID = 46
USER = "admin"
PASS = "admin123"
SEED = "__E2E_PRESET_META__" + str(random.randint(100000, 999999))


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
    try:
        # 种子 story + 任务（自清理）
        st, story = api("POST", f"/api/epics/{EPIC_ID}/stories", token=token,
                        body={"title": SEED + "-story", "description": "E2E 种子 story"})
        assert st == 201, f"create story {st} {story}"
        sid = story["id"]
        created.append(("story", sid))
        st, task = api("POST", f"/api/stories/{sid}/tasks", token=token,
                       body={"project_id": PROJECT_ID, "story_id": sid, "title": SEED + "-task",
                             "type": "task", "priority": "medium"})
        assert st == 201, f"create task {st} {task}"
        created.append(("task", task["id"]))

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console: " + m.type + ": " + m.text)
                    if m.type in ("error",) else None)
            page.on("response", lambda r: errors.append("http4xx: " + str(r.status) + " " + r.url)
                    if r.status >= 400 and (r.url.endswith(".js") or r.url.endswith(".css")) else None)

            page.add_init_script("localStorage.setItem('agentboard_token', '" + token + "');")
            page.goto(WEB + "/story/" + str(sid), wait_until="networkidle")
            page.wait_for_selector("button.dropdown:has-text('预设')", timeout=15000)

            # 设置分组维度为「按状态」
            group_sel = page.locator('select:has(option[value="none"])')
            group_sel.wait_for(timeout=5000)
            group_sel.select_option(value="status")
            page.wait_for_timeout(400)

            # 打开预设面板并保存当前筛选
            page.click("button.dropdown:has-text('预设')")
            page.wait_for_selector(".preset-panel", timeout=5000)
            name = SEED + "-preset"
            page.fill(".preset-name-input", name)
            page.click(".preset-save button:has-text('保存当前')")
            page.wait_for_timeout(500)

            # 定位该预设项并校验 meta chips
            item = page.locator(".preset-item", has_text=name)
            item.wait_for(timeout=5000)
            item.locator(".preset-meta").wait_for(timeout=5000)
            chips = item.locator(".preset-meta-chip")
            n = chips.count()
            assert n >= 2, f"预期 >=2 个 meta chip，实际 {n}"
            texts = [chips.nth(i).inner_text() for i in range(n)]
            print("meta chips:", texts)
            assert any("按状态" in t for t in texts), f"分组维度标签缺失: {texts}"
            assert any(("↑" in t or "↓" in t) for t in texts), f"排序方向箭头缺失: {texts}"

            # 清理：删除该预设
            item.locator(".preset-del").click()
            page.wait_for_timeout(300)
            browser.close()

        # 硬性断言：仅 pageerror / js+css 4xx / console error 视为失败
        hard = [e for e in errors if e.startswith("pageerror") or e.startswith("http4xx")
                or e.startswith("console: error")]
        assert not hard, "运行期错误:\n" + "\n".join(hard)
        print("E2E PASS: 预设可视化标签 (Epic 60 v4.7) 验证通过，0 错误")
    finally:
        # 清理种子数据
        for kind, kid in reversed(created):
            if kind == "task":
                api("DELETE", f"/api/tasks/{kid}", token=token)
            elif kind == "story":
                api("DELETE", f"/api/stories/{kid}", token=token)
        print("cleaned seed:", created)


if __name__ == "__main__":
    main()
