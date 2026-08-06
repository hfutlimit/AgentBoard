"""
Epic 119 (v6.13) 命令面板接入 Epic 后端搜索 —— 端到端验证
环境：本地 Docker 栈（web 28080 / API 18000）
- 登录 admin（不存在则注册 admin/admin123）-> 本地 API 建种子 project + epic（唯一 token，自清理）
- 断言：
  1) Ctrl+K 打开命令面板
  2) 输入唯一 token -> 出现「Epic」(cat-epic) 分类结果；标题含 token；点击 -> 跳转 /epic/{id} 且面板关闭
  3) 输入无匹配 token -> 「无匹配命令」空态
  4) 0 pageerror / console error / .js+.css 404
- 测试末清理种子 epic + project，不污染数据
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
SEED = "__E2E_V613_" + str(random.randint(100000, 999999))


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
    # 备用：注册 admin 再登录
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
        # 种子 project + epic（标题含唯一 token，验证后端 /api/search/epics 按标题匹配）
        st, proj = api("POST", "/api/projects", token=token,
                       body={"name": SEED + "-proj", "description": "E2E 种子项目", "key": SEED[:12]})
        assert st in (200, 201), f"create project {st} {proj}"
        pid = proj["id"] if "id" in proj else proj.get("project", {}).get("id")
        created.append(("project", pid))
        st, epic = api("POST", f"/api/projects/{pid}/epics", token=token,
                       body={"title": SEED + "-epic", "description": "E2E 种子 epic，用于验证命令面板 Epic 搜索"})
        assert st in (200, 201), f"create epic {st} {epic}"
        eid = epic["id"]
        created.append(("epic", eid))
        print(f"[seed] project={pid} epic={eid} token={SEED}")

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

            # 2) Epic 搜索
            page.fill("#paletteInput", SEED)
            page.wait_for_selector(".palette-item-cat.cat-epic", timeout=10000)
            epic_items = page.locator(".palette-item-cat.cat-epic")
            assert epic_items.count() >= 1, "未出现 Epic 搜索结果"
            print(f"[OK] Epic 搜索 {epic_items.count()} 条")

            # 点击 Epic 结果 -> /epic/{id} 且面板关闭
            epic_loc = page.locator(".palette-item", has_text=SEED + "-epic").first
            epic_title = epic_loc.inner_text()
            assert SEED in epic_title, f"Epic 结果标题不含 token: {epic_title}"
            epic_loc.click()
            page.wait_for_timeout(800)
            assert f"/epic/{eid}" in page.url, f"未跳转到 /epic/{eid}，当前 {page.url}"
            assert page.locator(".command-palette").count() == 0, "跳转后面板未关闭"
            print(f"[OK] 点击 Epic 结果跳转到 {page.url}")

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
            if kind == "epic":
                api("DELETE", f"/api/epics/{_id}", token=token)
            else:
                api("DELETE", f"/api/projects/{_id}", token=token)
        if errors or js_css_fail:
            print("ERRORS:", errors, js_css_fail)


if __name__ == "__main__":
    main()
