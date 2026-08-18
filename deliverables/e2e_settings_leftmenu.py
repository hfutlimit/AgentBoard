"""设置页「左侧菜单」布局 E2E 验证（替代原 dropdown）。

本地启动后运行：
    AGENTBOARD_API=http://127.0.0.1:18000 AGENTBOARD_WEB=http://localhost:28080 \
        python deliverables/e2e_settings_leftmenu.py

验证点：
1) tab 栏含「设置」，不含「Sprints」
2) 点击「设置」进入后，存在左侧 .settings-nav 菜单，含 4 项：基本信息 / 成员管理 / 自动化计划 / 数据导出
3) 旧 dropdown 残留（.tab-dropdown-trigger / .tab-dropdown-menu）数量为 0
4) 点击各子菜单项，右侧 .settings-main 内容正确切换
5) 0 个 JS error / console error，0 个 js/css 资源加载失败
"""
import json
import os
import sys
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

API = os.getenv("AGENTBOARD_API", "http://127.0.0.1:18000")
WEB = os.getenv("AGENTBOARD_WEB", "http://localhost:28080")
USER = "admin"
PASS = "admin123"


def api(method, path, token=None, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode() or "{}"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw}


def ensure_token_and_project():
    # 注册（若不存在），随后登录取 token
    st, _ = api("POST", "/api/auth/register", body={"username": USER, "password": PASS})
    print(f"[auth] register -> {st}")
    st, data = api("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    assert st == 200, f"login failed {st}: {data}"
    token = data.get("token") or (data.get("data") or {}).get("token")
    assert token, f"no token in login response: {data}"
    print("[OK] login, token acquired")

    st, projects = api("GET", "/api/projects", token=token)
    if isinstance(projects, dict):
        projects = projects.get("items", [])
    if projects:
        pid = projects[0]["id"]
        print(f"[OK] 复用项目 #{pid} ({projects[0].get('name')})")
    else:
        st, p = api(
            "POST",
            "/api/projects",
            token=token,
            body={"name": "E2E 设置菜单验证", "key": "E2E", "description": "Playwright 验证设置页左侧菜单"},
        )
        assert st in (200, 201), f"create project failed {st}: {p}"
        pid = p["id"]
        print(f"[OK] 创建项目 #{pid}")
    return token, pid


def main():
    token, pid = ensure_token_and_project()

    errors = []
    js_css_fail = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
        page.on(
            "console",
            lambda m: errors.append("console: " + m.text) if m.type == "error" else None,
        )
        page.on(
            "requestfailed",
            lambda r: (
                js_css_fail.append(r.url)
                if (r.url.endswith(".js") or r.url.endswith(".css"))
                else None
            ),
        )

        # 注入 token（先到 /login 使 localStorage 归属正确 origin）
        page.goto(WEB + "/login", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        page.evaluate(
            f"() => {{ localStorage.setItem('agentboard_token', '{token}');"
            f"           localStorage.setItem('agentboard_user', '{USER}'); }}"
        )

        # 进入项目页
        page.goto(f"{WEB}/project/{pid}", wait_until="domcontentloaded")
        page.wait_for_timeout(6000)

        # 1) tab 文案
        labels = page.eval_on_selector_all(
            ".tab-bar .tab-btn .tab-label",
            "els => els.map(e => e.textContent.trim())",
        )
        print("[tabs]", labels)
        assert "设置" in labels, f"缺少「设置」tab: {labels}"
        assert "Sprints" not in labels, f"残留 Sprints tab: {labels}"

        # 2) 旧 dropdown 必须消失
        dd_trigger = page.locator(".tab-dropdown-trigger").count()
        dd_menu = page.locator(".tab-dropdown-menu").count()
        print(f"[dropdown] trigger={dd_trigger} menu={dd_menu}")
        assert dd_trigger == 0, "旧 dropdown 触发器仍存在"
        assert dd_menu == 0, "旧 dropdown 菜单仍存在"

        # 3) 点击「设置」tab
        page.locator(".tab-bar .tab-btn").filter(has_text="设置").click()
        page.wait_for_selector(".settings-nav", timeout=8000)
        page.wait_for_timeout(800)
        nav_count = page.locator(".settings-nav-item").count()
        nav_labels = page.eval_on_selector_all(
            ".settings-nav-item .settings-nav-label",
            "els => els.map(e => e.textContent.trim())",
        )
        print(f"[settings-nav] count={nav_count} labels={nav_labels}")
        assert nav_count == 4, f"左侧菜单应为 4 项，实际 {nav_count}: {nav_labels}"
        assert nav_labels == ["基本信息", "成员管理", "自动化计划", "数据导出"], \
            f"菜单项不符: {nav_labels}"

        # 左侧栏结构
        assert page.locator(".settings-sidebar").count() == 1, "缺少 .settings-sidebar"
        assert page.locator(".settings-layout").count() == 1, "缺少 .settings-layout"
        page.screenshot(path="deliverables/e2e_settings_leftmenu.png", full_page=False)

        # 4) 逐个切换子页，验证右侧内容切换
        def click_nav(name):
            page.locator(".settings-nav-item").filter(has_text=name).click()
            page.wait_for_timeout(500)

        click_nav("基本信息")
        main_text = page.locator(".settings-main").inner_text()
        assert ("项目设置" in main_text) or ("当前设置" in main_text), \
            f"基本信息子页内容异常: {main_text[:80]}"
        print("[OK] 基本信息子页")

        click_nav("成员管理")
        main_text = page.locator(".settings-main").inner_text()
        assert "项目成员" in main_text, f"成员管理子页异常: {main_text[:80]}"
        print("[OK] 成员管理子页")

        click_nav("自动化计划")
        main_text = page.locator(".settings-main").inner_text()
        assert "定时 Agent 计划" in main_text, f"自动化计划子页异常: {main_text[:80]}"
        print("[OK] 自动化计划子页")

        click_nav("数据导出")
        main_text = page.locator(".settings-main").inner_text()
        assert "数据导出" in main_text, f"数据导出子页异常: {main_text[:80]}"
        print("[OK] 数据导出子页")

        # active 高亮跟随
        active_label = page.eval_on_selector(
            ".settings-nav-item.active .settings-nav-label",
            "el => el.textContent.trim()",
        )
        print(f"[settings-nav] active after 数据导出 = {active_label}")
        assert active_label == "数据导出", f"active 高亮未跟随: {active_label}"

        page.screenshot(path="deliverables/e2e_settings_subtabs.png", full_page=False)
        browser.close()

    print(f"[E2E] pageerror/console errors: {len(errors)}")
    print(f"[E2E] js/css 失败: {len(js_css_fail)}")
    for e in errors[:8]:
        print("  ERR:", e)
    for f in js_css_fail[:8]:
        print("  FAIL:", f)
    assert not errors, f"存在 JS 错误: {errors[:3]}"
    assert not js_css_fail, f"存在 js/css 加载失败: {js_css_fail[:3]}"
    print("[PASS] 设置页左侧菜单 E2E 通过")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("[FAIL] " + str(e))
        sys.exit(1)
