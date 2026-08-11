"""
Epic 132 (v6.17) 命令面板接入 Proposal 搜索 —— 端到端验证
环境：本地 Docker 栈（web 28080 / API 18000）
策略（真实后端）：
- admin（不存在则注册）建 1 个唯一 token 命名的 Proposal
- 断言：
  1) Ctrl+K 打开命令面板
  2) 输入唯一 token -> 出现「Proposal」(cat-proposal) 分类结果；标题含 token；点击 -> /proposals/{id} 详情渲染且面板关闭
  3) 输入无匹配 token -> 「无匹配命令」空态
  4) 0 pageerror / console error / .js+.css 404
- 测试末清理种子 Proposal（DELETE /api/proposals/{id}），不污染数据
"""
import json
import random
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:28080"
API = "http://127.0.0.1:18000"
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"
SEED = "__E2E_V617_" + str(random.randint(100000, 999999))
PROJ_NAME = "E2E Proposal Search " + str(random.randint(100000, 999999))


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


def login(username, password, do_register=False):
    st, u = api("POST", "/api/auth/login", body={"username": username, "password": password})
    if st == 200:
        return u["token"]
    if do_register:
        st2, _ = api("POST", "/api/auth/register", body={"username": username, "password": password})
        assert st2 in (200, 201), f"register failed {st2}: {_}"
        st3, u3 = api("POST", "/api/auth/login", body={"username": username, "password": password})
        assert st3 == 200, f"relogin failed {st3}"
        return u3["token"]
    raise AssertionError(f"login failed {st}: {u}")


def main():
    admin_token = login(ADMIN_USER, ADMIN_PASS, do_register=True)
    errors = []
    js_css_fail = []
    created = []
    try:
        # 种子：建项目 + 唯一 token 命名 Proposal
        st, proj = api("POST", "/api/projects", token=admin_token, body={"name": PROJ_NAME})
        assert st in (200, 201), f"create project {st} {proj}"
        pid = proj["id"]
        st, prop = api("POST", "/api/proposals", token=admin_token,
                       body={"project_id": pid, "title": SEED + "-需求提案",
                             "content": "e2e proposal search 专用提案正文"})
        assert st in (200, 201), f"create proposal {st} {prop}"
        pid_prop = prop["id"]
        created.append(pid_prop)
        print(f"[seed] project={pid} proposal={pid_prop} token={SEED}")

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-proxy-server"])
            page = browser.new_page()
            init = (
                "localStorage.setItem('agentboard_token','%s');"
                "localStorage.setItem('agentboard_user','%s');" % (admin_token, ADMIN_USER)
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

            # 2) Proposal 搜索：输入唯一 token -> .cat-proposal 结果
            page.fill("#paletteInput", SEED)
            page.wait_for_selector(".palette-item-cat.cat-proposal", timeout=10000)
            prop_items = page.locator(".palette-item-cat.cat-proposal")
            assert prop_items.count() >= 1, "未出现 Proposal 搜索结果"
            print(f"[OK] Proposal 搜索 {prop_items.count()} 条")

            # 点击 Proposal 结果 -> /proposals/{id} 详情渲染且面板关闭
            prop_loc = page.locator(".palette-item", has_text="Proposal").first
            prop_title = prop_loc.inner_text()
            assert SEED in prop_title, f"Proposal 结果标题不含 token: {prop_title}"
            prop_loc.click()
            page.wait_for_timeout(1200)
            assert page.locator(".command-palette").count() == 0, "跳转后面板未关闭"
            page.wait_for_function(
                "() => document.body.innerText.includes(%r)" % SEED, timeout=10000
            )
            assert SEED in page.inner_text("body"), "Proposal 详情页未渲染种子提案"
            print("[OK] 点击 Proposal 结果进入详情页且渲染")

            # 3) 无匹配空态
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
        for pid in created:
            api("DELETE", f"/api/proposals/{pid}", token=admin_token)
        if errors or js_css_fail:
            print("ERRORS:", errors, js_css_fail)


if __name__ == "__main__":
    main()
