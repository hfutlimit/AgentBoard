"""
Epic 120 (v6.14) 命令面板接入 Sprint 后端搜索 —— 端到端验证
环境：本地 Docker 栈（web 28080 / API 18000）
- 登录 admin（不存在则注册 admin/admin123）-> 本地 API 建种子 project + sprint（唯一 token，自清理）
- 断言：
  1) Ctrl+K 打开命令面板
  2) 输入唯一 token -> 出现「Sprint」(cat-sprint) 分类结果；标题含 token；点击 -> 跳转 /sprint/{id} 且面板关闭、Sprint 详情渲染
  3) 输入无匹配 token -> 「无匹配命令」空态
  4) 0 pageerror / console error / .js+.css 404
- 测试末清理种子 sprint + project，不污染数据
"""
import json
import random
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:28080"
API = "http://127.0.0.1:18000"
USER = "admin"
PASS = "admin123"
SEED = "__E2E_V614_" + str(random.randint(100000, 999999))


def api(method, path, token=None, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def login():
    st, u = api("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    if st == 200:
        return u["token"]
    st2, _ = api("POST", "/api/auth/register", body={"username": USER, "password": PASS})
    assert st2 in (200, 201), f"register failed {st2}"
    st3, u3 = api("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    assert st3 == 200, f"relogin failed {st3}"
    return u3["token"]


def main():
    token = login()
    created = []
    errors = []
    js_css_fail = []
    try:
        # 种子 project + sprint（标题含唯一 token，验证后端 /api/search/sprints 按 title 匹配）
        st, proj = api("POST", "/api/projects", token=token,
                       body={"name": SEED + "-proj", "description": "E2E 种子项目", "key": SEED[:12]})
        assert st in (200, 201), f"create project {st} {proj}"
        pid = proj["id"] if "id" in proj else proj.get("project", {}).get("id")
        created.append(("project", pid))
        st, sprint = api("POST", f"/api/projects/{pid}/sprints", token=token,
                         body={"title": SEED + "-sprint", "goal": "E2E 种子 sprint，用于验证命令面板 Sprint 搜索"})
        assert st in (200, 201), f"create sprint {st} {sprint}"
        sid = sprint["id"]
        created.append(("sprint", sid))
        print(f"[seed] project={pid} sprint={sid} token={SEED}")

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

            page.goto(WEB + "/projects", wait_until="domcontentloaded")
            page.wait_for_selector("#sidebar", timeout=30000)

            # 1) Ctrl+K 打开面板
            page.keyboard.press("Control+k")
            page.wait_for_selector(".command-palette", state="visible", timeout=5000)
            print("[OK] Ctrl+K 打开命令面板")

            # 2) Sprint 搜索
            page.fill("#paletteInput", SEED)
            page.wait_for_selector(".palette-item-cat.cat-sprint", timeout=10000)
            sprint_items = page.locator(".palette-item-cat.cat-sprint")
            assert sprint_items.count() >= 1, "未出现 Sprint 搜索结果"
            print(f"[OK] Sprint 搜索 {sprint_items.count()} 条")

            # 点击 Sprint 结果 -> /sprint/{id} 且面板关闭、详情渲染
            sprint_loc = page.locator(".palette-item", has_text=SEED + "-sprint").first
            sprint_title = sprint_loc.inner_text()
            assert SEED in sprint_title, f"Sprint 结果标题不含 token: {sprint_title}"
            sprint_loc.click()
            page.wait_for_timeout(1000)
            assert f"/sprint/{sid}" in page.url, f"未跳转到 /sprint/{sid}，当前 {page.url}"
            assert page.locator(".command-palette").count() == 0, "跳转后面板未关闭"
            # Sprint 详情渲染：crumb-bar / 页头含种子标题（sprint 视图无专用容器类）
            page.wait_for_function(
                "() => document.body.innerText.includes(%r)" % SEED, timeout=8000
            )
            assert SEED in page.inner_text("body"), "Sprint 详情未渲染种子标题"
            print(f"[OK] 点击 Sprint 结果跳转到 {page.url} 且详情渲染")

            # 3) 无匹配空态（等后端搜索完成，文案由「搜索中…」切换为「无匹配命令」）
            page.keyboard.press("Control+k")
            page.wait_for_selector(".command-palette", state="visible", timeout=5000)
            page.fill("#paletteInput", "zzzqqq_nomatch_xyz")
            page.wait_for_function(
                "() => { const el = document.querySelector('.command-palette-empty');"
                " return el && el.innerText.includes('无匹配'); }",
                timeout=8000,
            )
            empty = page.locator(".command-palette-empty")
            assert empty.count() == 1, "无匹配时未显示空态"
            assert "无匹配" in empty.first.inner_text(), "空态文案异常"
            print("[OK] 无匹配显示空态")

            # 4) 错误检查
            assert not errors, "存在 JS/控制台错误:\n" + "\n".join(errors)
            assert not js_css_fail, "存在 .js/.css 加载失败:\n" + "\n".join(js_css_fail)
            print("[OK] 0 pageerror / console error / .js+.css 404")

            browser.close()
        print("ALL PASS")
    finally:
        for kind, _id in created:
            if kind == "sprint":
                api("DELETE", f"/api/sprints/{_id}", token=token)
            else:
                api("DELETE", f"/api/projects/{_id}", token=token)
        if errors or js_css_fail:
            print("ERRORS:", errors, js_css_fail)


if __name__ == "__main__":
    main()
