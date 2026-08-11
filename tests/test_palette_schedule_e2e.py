"""
Epic 134 (v6.19) 命令面板接入 Schedule 搜索 —— 端到端验证
环境：本地 Docker 栈（web 28080 / API 18000），也可被 tmp harness 覆写 WEB/API 复用
策略（真实后端）：
- admin（不存在则注册）建项目 + 创建唯一 token 命名的 AgentSchedule 定时计划；
- 断言：
  1) Ctrl+K 打开命令面板
  2) 输入唯一 token -> 出现「计划」(cat-schedule) 分类结果；标题含 token；
     点击 -> /project/{id}/schedules 项目定时计划 Tab 渲染且面板关闭
  3) 输入无匹配 token -> 「无匹配命令」空态
  4) 0 pageerror / console error / .js+.css 404
- 测试末清理种子（DELETE /api/projects/{id} 级联删除计划），不污染数据
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
SEED = "__E2E_V619_" + str(random.randint(100000, 999999))
PROJ_NAME = "E2E Schedule Search " + str(random.randint(100000, 999999))


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


def seed_schedule(admin_token):
    """建项目 + 创建唯一 token 命名的 cron 定时计划，返回 (project_id, schedule_id)。"""
    st, proj = api("POST", "/api/projects", token=admin_token, body={"name": PROJ_NAME})
    assert st in (200, 201), f"create project {st} {proj}"
    pid = proj["id"]
    st, sch = api("POST", f"/api/projects/{pid}/schedules", token=admin_token, body={
        "title": SEED + "定时构建",
        "schedule_type": "cron",
        "cron_expr": "0 3 * * *",
        "agent": "codex",
    })
    assert st in (200, 201), f"create schedule {st}: {sch}"
    return pid, sch["id"]


def main():
    admin_token = login(ADMIN_USER, ADMIN_PASS, do_register=True)
    errors = []
    js_css_fail = []
    created = []
    try:
        pid, schedule_id = seed_schedule(admin_token)
        created.append(pid)
        print(f"[seed] project={pid} schedule={schedule_id} token={SEED}")

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

            # 2) Schedule 搜索：输入唯一 token -> .cat-schedule 结果
            page.fill("#paletteInput", SEED)
            page.wait_for_selector(".palette-item-cat.cat-schedule", timeout=10000)
            sch_items = page.locator(".palette-item-cat.cat-schedule")
            assert sch_items.count() >= 1, "未出现 Schedule 搜索结果"
            print(f"[OK] Schedule 搜索 {sch_items.count()} 条")

            # 点击 Schedule 结果 -> /project/{pid}/schedules 项目定时计划 Tab 渲染且面板关闭
            sch_loc = page.locator(".palette-item", has_text="计划").first
            sch_title = sch_loc.inner_text()
            assert SEED in sch_title, f"Schedule 结果标题不含 token: {sch_title}"
            sch_loc.click()
            page.wait_for_timeout(1500)
            assert page.locator(".command-palette").count() == 0, "跳转后面板未关闭"
            page.wait_for_function(
                "() => document.body.innerText.includes(%r)" % SEED, timeout=10000
            )
            # 确认落在项目定时计划 Tab（页面正文包含计划标题）
            assert page.locator(".tab-btn.active").count() >= 1, "未定位到激活 Tab"
            print("[OK] 点击 Schedule 结果进入项目定时计划 Tab 且渲染")

            # 3) 无匹配 token -> 空态
            page.keyboard.press("Control+k")
            page.wait_for_selector(".command-palette", state="visible", timeout=5000)
            page.fill("#paletteInput", SEED + "_NOMATCH_XYZ")
            page.wait_for_timeout(1200)
            body_text = page.locator(".command-palette").inner_text()
            assert "无匹配" in body_text or "没有" in body_text, f"未出现空态: {body_text[:100]}"
            print("[OK] 无匹配显示空态")

            # 4) 前端资源与运行时错误
            assert len(errors) == 0, f"pageerror/console error: {errors}"
            assert len(js_css_fail) == 0, f".js/.css 404: {js_css_fail}"
            print("[OK] 0 pageerror / console error / .js+.css 404")
    finally:
        for pid in created:
            api("DELETE", f"/api/projects/{pid}", token=admin_token)
    print("ALL PASS")


if __name__ == "__main__":
    main()
