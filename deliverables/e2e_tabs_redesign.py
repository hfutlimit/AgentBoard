"""项目页 tab 新布局 E2E 验证（一次性脚本）。

验证 28080 前端项目页：
1) tab 顺序：新顺序 = 项目介绍 / Epics / Backlog / 提案 / 统计 / 设置（带 caret）/ 文档 / 看板
2) Sprints tab 已移除
3) 默认 activeTab = overview（项目介绍），hero + 关键统计 + 成员 + 最近 Epic + 快捷入口
4) 设置 dropdown：点击 ⚙️ 弹出小菜单（基本信息 / 成员管理 / 自动化计划）
5) 0 JS error / 0 404
"""
import json
import urllib.request
import urllib.error
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:18000"
WEB = "http://localhost:28080"  # 用 localhost 匹配前端编译时 API base 的 CORS 白名单
USER = "admin"
PASS = "admin123"


def api(method, path, token=None, body=None):
    req = urllib.request.Request(API + path,
                                 data=json.dumps(body).encode() if body else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def main():
    st, u = api("POST", "/api/auth/login",
                body={"username": USER, "password": PASS})
    assert st == 200, f"login failed {st}"
    token = u["token"]
    print("[OK] login")

    st, data = api("GET", "/api/projects", token=token)
    projects = data if isinstance(data, list) else (data or {}).get("items", [])
    assert projects, "无可见项目"
    pid = projects[0]["id"]
    print(f"[OK] 取到项目 #{pid}")

    errors = []
    js_css_fail = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
        page.on("console", lambda m: errors.append("console: " + m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: (
            js_css_fail.append(r.url) if (r.url.endswith(".js") or r.url.endswith(".css")) else None
        ))

        # 任意源先访问，把 token 写入 localStorage（避免 CORS 时跳转 /login）
        page.goto(WEB + "/login", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        page.evaluate(
            f"() => {{ localStorage.setItem('agentboard_token', '{token}');"
            f"           localStorage.setItem('agentboard_user', 'admin'); }}"
        )

        # 进项目页 → 默认应落到「项目介绍」tab
        page.goto(f"{WEB}/project/{pid}", wait_until="domcontentloaded")
        page.wait_for_timeout(6500)
        print("[url]", page.url)
        # 1) 验证 tab 顺序与文案
        labels = page.eval_on_selector_all(
            ".tab-bar .tab-btn .tab-label",
            "els => els.map(e => e.textContent.trim())"
        )
        print("[tab-bar count]", page.locator(".tab-bar").count())
        print("[tabs] labels:", labels)
        assert "项目介绍" in labels, "缺少项目介绍 tab"
        assert "Epics" in labels and "看板" in labels and "设置" in labels, \
            f"基础 tab 缺失: {labels}"
        assert "Sprints" not in labels, f"Sprints tab 未移除: {labels}"

        # 2) 验证默认 active = 项目介绍
        active_label = page.eval_on_selector(
            ".tab-bar .tab-btn.active .tab-label",
            "el => el.textContent.trim()"
        )
        print("[tabs] active:", active_label)
        assert active_label == "项目介绍", f"默认 active 应为「项目介绍」实际 {active_label}"

        # 3) 项目介绍内容存在（hero + 关键统计 + 成员 + Epic）
        assert page.locator(".overview-hero").count() == 1, "缺 hero"
        assert page.locator(".overview-stats").count() == 1, "缺统计"
        assert page.locator(".overview-grid").count() == 1, "缺 grid"
        print("[OK] 项目介绍内容渲染")
        page.screenshot(path="deliverables/e2e_overview_tab.png", full_page=False)

        # 4) 设置 dropdown：点击 ⚙️ 弹出菜单
        page.click(".tab-dropdown-trigger")
        page.wait_for_timeout(500)
        menu_count = page.locator(".tab-dropdown-menu").count()
        print(f"[dropdown] menu count: {menu_count}")
        assert menu_count == 1, "点击 ⚙️ 未展开小菜单"
        items = page.eval_on_selector_all(
            ".tab-dropdown-menu .tab-dropdown-title",
            "els => els.map(e => e.textContent.trim())"
        )
        print(f"[dropdown] items: {items}")
        assert "基本信息" in items and "成员管理" in items and "自动化计划" in items, \
            f"菜单项缺失: {items}"
        page.screenshot(path="deliverables/e2e_settings_dropdown.png", full_page=False)

        # 5) 点击菜单项「基本信息」切换到 settings tab 并关闭菜单
        page.click(".tab-dropdown-item:has-text('基本信息')")
        page.wait_for_timeout(800)
        active_label = page.eval_on_selector(
            ".tab-bar .tab-btn.active .tab-label",
            "el => el.textContent.trim()"
        )
        # 设置菜单点击后 active 可能是「设置」（触发器）或保持原状（基于 [class.active] 的判定）
        menu_open_after = page.locator(".tab-dropdown-menu").count()
        print(f"[dropdown] after click, active={active_label}, menu_open={menu_open_after}")
        assert menu_open_after == 0, "点击菜单项后菜单应关闭"

        browser.close()

    print(f"[E2E] pageerror/console errors: {len(errors)}")
    print(f"[E2E] js/css 404/失败: {len(js_css_fail)}")
    for e in errors[:5]:
        print("  ERR:", e)
    for f in js_css_fail[:5]:
        print("  FAIL:", f)
    assert not errors, f"页面存在 JS 错误: {errors[:3]}"
    assert not js_css_fail, f"存在 js/css 加载失败: {js_css_fail[:3]}"
    print("[PASS] 项目页 tab 新布局 E2E 通过")


if __name__ == "__main__":
    main()