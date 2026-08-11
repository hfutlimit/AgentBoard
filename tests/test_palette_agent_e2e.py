"""
Epic 131 (v6.16) 命令面板接入 Agent 搜索 —— 端到端验证
环境：本地 Docker 栈（web 28080 / API 18000）
策略（真实后端）：
- admin（不存在则注册）注册 2 个 Agent（agent_id 含唯一 token，一个在线态由 heartbeat 触发）
- 断言：
  1) Ctrl+K 打开命令面板
  2) 输入唯一 token -> 出现「Agent」(cat-agent) 分类结果；标题含 token；点击 -> Agent 池视图渲染且面板关闭
  3) 输入无匹配 token -> 「无匹配命令」空态
  4) 0 pageerror / console error / .js+.css 404
- 测试末清理种子 Agent（delete /api/agents/{agent_id}），不污染数据
"""
import json
import random
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://localhost:28080"
API = "http://localhost:18000"
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"
SEED = "__E2E_V616_" + str(random.randint(100000, 999999))
AGENT_ID_A = "e2e-agent-a" + str(random.randint(100000, 999999))
AGENT_ID_B = "e2e-agent-b" + str(random.randint(100000, 999999))


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
    created_agents = []
    try:
        # 种子 1/2：admin 注册 2 个 Agent，其中一个 name/roles 含唯一 token
        st, a1 = api("POST", "/api/agents/register", token=admin_token,
                     body={"agent_id": AGENT_ID_A, "name": SEED + "-Worker",
                           "roles": '["developer"]', "capabilities": '["python"]',
                           "cli_command": "echo e2e"})
        assert st in (200, 201), f"register agent A {st} {a1}"
        created_agents.append(AGENT_ID_A)
        st, a2 = api("POST", "/api/agents/register", token=admin_token,
                     body={"agent_id": AGENT_ID_B, "name": "Plain Agent",
                           "roles": '["reviewer"]', "capabilities": "[]",
                           "cli_command": "echo e2e"})
        assert st in (200, 201), f"register agent B {st} {a2}"
        created_agents.append(AGENT_ID_B)
        print(f"[seed] agents={created_agents} token={SEED}")

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

            # 2) Agent 搜索：输入唯一 token -> .cat-agent 结果
            page.fill("#paletteInput", SEED)
            page.wait_for_selector(".palette-item-cat.cat-agent", timeout=10000)
            agent_items = page.locator(".palette-item-cat.cat-agent")
            assert agent_items.count() >= 1, "未出现 Agent 搜索结果"
            print(f"[OK] Agent 搜索 {agent_items.count()} 条")

            # 点击 Agent 结果 -> Agent 池视图渲染且面板关闭
            agent_loc = page.locator(".palette-item", has_text="Agent").first
            agent_title = agent_loc.inner_text()
            assert AGENT_ID_A in agent_title, f"Agent 结果标题不含 agent_id: {agent_title}"
            agent_loc.click()
            page.wait_for_timeout(1200)
            assert page.locator(".command-palette").count() == 0, "跳转后面板未关闭"
            page.wait_for_function(
                "() => document.body.innerText.includes(%r)" % AGENT_ID_A, timeout=10000
            )
            assert AGENT_ID_A in page.inner_text("body"), "Agent 池视图未渲染种子 Agent"
            print("[OK] 点击 Agent 结果进入 Agent 池视图且渲染")

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
        for aid in created_agents:
            api("DELETE", f"/api/agents/{aid}", token=admin_token)
        if errors or js_css_fail:
            print("ERRORS:", errors, js_css_fail)


if __name__ == "__main__":
    main()
