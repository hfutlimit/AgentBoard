"""
Epic 121 (v6.15) 命令面板接入通知搜索 —— 端到端验证
环境：本地 Docker 栈（web 28080 / API 18000）
策略（真实后端 + 用户隔离验证）：
- admin（不存在则注册）建唯一 token 项目 -> 注册 E2E 专属用户 -> admin 邀请其加入项目
  -> 触发 project_invite 通知（title 含唯一 token）-> 以 E2E 用户登录
- 断言：
  1) Ctrl+K 打开命令面板
  2) 输入唯一 token -> 出现「通知」(cat-notification) 分类结果；标题含 token；点击 -> 跳转 /project/{pid} 且面板关闭
  3) 输入无匹配 token -> 「无匹配命令」空态
  4) 0 pageerror / console error / .js+.css 404
- 测试末清理种子项目（级联删通知），不污染数据
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
SEED = "__E2E_V615_" + str(random.randint(100000, 999999))
E2E_USER = "e2e_notif_" + str(random.randint(100000, 999999))
E2E_PASS = "e2e_notif_pw_2026"


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
    created_projects = []
    try:
        # 种子 1：admin 建唯一 token 项目
        st, proj = api("POST", "/api/projects", token=admin_token,
                       body={"name": SEED + "-proj", "description": "E2E 通知种子项目", "key": SEED[:12]})
        assert st in (200, 201), f"create project {st} {proj}"
        pid = proj["id"] if "id" in proj else proj.get("project", {}).get("id")
        created_projects.append(pid)

        # 种子 2：注册 E2E 用户，admin 邀请其加入项目（触发 project_invite 通知，title 含唯一 token）
        e2e_token = login(E2E_USER, E2E_PASS, do_register=True)
        st, me = api("GET", "/api/auth/me", token=e2e_token)
        assert st == 200, f"auth/me {st}"
        e2e_uid = me["id"]
        st, inv = api("POST", f"/api/projects/{pid}/members", token=admin_token,
                      body={"user_id": e2e_uid, "role": "member"})
        assert st in (200, 201), f"invite member {st} {inv}"

        # 种子 3：确认 E2E 用户通知列表含 project_invite（title 含 token）
        st, notifs = api("GET", "/api/notifications?limit=20", token=e2e_token)
        assert st == 200, f"list notifications {st}"
        items = notifs.get("items", [])
        hit = [n for n in items if SEED in (n.get("title", "") + n.get("content", ""))]
        assert hit, f"E2E 用户未收到含 token 的通知: {items}"
        nid = hit[0]["id"]
        print(f"[seed] project={pid} user={e2e_uid} notif={nid} token={SEED}")

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-proxy-server"])
            page = browser.new_page()
            init = (
                "localStorage.setItem('agentboard_token','%s');"
                "localStorage.setItem('agentboard_user','%s');" % (e2e_token, E2E_USER)
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

            # 2) 通知搜索：输入唯一 token -> .cat-notification 结果
            page.fill("#paletteInput", SEED)
            page.wait_for_selector(".palette-item-cat.cat-notification", timeout=10000)
            notif_items = page.locator(".palette-item-cat.cat-notification")
            assert notif_items.count() >= 1, "未出现通知搜索结果"
            print(f"[OK] 通知搜索 {notif_items.count()} 条")

            # 点击通知结果 -> /project/{pid} 且面板关闭、项目页渲染
            notif_loc = page.locator(".palette-item", has_text="通知").first
            notif_title = notif_loc.inner_text()
            assert SEED in notif_title, f"通知结果标题不含 token: {notif_title}"
            notif_loc.click()
            page.wait_for_timeout(1000)
            assert f"/project/{pid}" in page.url, f"未跳转到 /project/{pid}，当前 {page.url}"
            assert page.locator(".command-palette").count() == 0, "跳转后面板未关闭"
            page.wait_for_function(
                "() => document.body.innerText.includes(%r)" % SEED, timeout=8000
            )
            assert SEED in page.inner_text("body"), "项目页未渲染种子标题"
            print(f"[OK] 点击通知结果跳转到 {page.url} 且页面渲染")

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
        for pid in created_projects:
            api("DELETE", f"/api/projects/{pid}", token=admin_token)
        if errors or js_css_fail:
            print("ERRORS:", errors, js_css_fail)


if __name__ == "__main__":
    main()
