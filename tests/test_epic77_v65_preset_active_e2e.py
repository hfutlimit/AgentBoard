"""
Epic 77 (v6.5): 筛选预设「当前激活」高亮 —— 端到端验证
- 登录 admin -> 进入含任务的 Story 视图（/story/116，对应本次 tracked task 1139）
- 断言任务列表筛选栏渲染「预设」面板入口
- 1) 打开预设面板，保存一个「空预设」P1（当前无任何筛选）-> 因当前筛选维度与 P1 完全一致，P1 应被高亮为「当前」
- 2) 勾选「只看我」(filterMineOnly=true) -> 当前筛选不再匹配空的 P1 -> 活跃高亮消失
- 3) 取消「只看我」 -> 无筛选 -> P1 重新高亮
- 4) 勾选「只看我」后再保存预设 P2（捕获 mineOnly=true）-> 当前筛选匹配 P2 -> 高亮切到 P2（名称断言）
- 5) 取消「只看我」 -> 无筛选 -> 高亮回到 P1
- 6) 全过程 0 pageerror / console error / .js+.css 404
- 纯前端验证；预设存于浏览器 localStorage，无服务端实体清理负担。
"""
import json
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8080"
API = "http://127.0.0.1:58125"
USER = "admin"
PASS = "admin123"
STORY_ID = 116


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
    errors = []
    js_css_fail = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-proxy-server"])
            page = browser.new_page()
            page.route("**://127.0.0.1:58124/**",
                        lambda r: r.continue_(url=r.request.url.replace("58124", "58125")))
            init = (
                "localStorage.setItem('agentboard_token','%s');"
                "localStorage.setItem('agentboard_user','admin');"
                "localStorage.removeItem('agentboard_filter_presets');"  # 干净起点
                "localStorage.setItem('agentboard_story_view','list');"
                "localStorage.setItem('agentboard_story_group','none');" % token
            )
            page.add_init_script(init)
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: (
                js_css_fail.append(r.url) if (r.url.endswith(".js") or r.url.endswith(".css")) else None
            ))

            page.goto(WEB + "/story/" + str(STORY_ID), wait_until="domcontentloaded")
            page.wait_for_selector(".preset-wrap button.dropdown", timeout=20000)
            page.wait_for_selector(".filterbar__right", timeout=10000)
            print("[ok] 筛选栏与预设面板入口渲染")

            # 打开预设面板
            page.click(".preset-wrap button.dropdown")
            page.wait_for_selector(".preset-panel", timeout=5000)
            print("[ok] 预设面板已展开")

            # 1) 保存空预设 P1
            page.fill(".preset-name-input", "P1")
            page.click(".preset-save button.btn--primary")
            page.wait_for_selector(".preset-item", timeout=5000)
            # 当前无任何筛选 -> 空预设应被高亮为「当前」
            page.wait_for_selector(".preset-item.active", timeout=5000)
            assert page.locator(".preset-item.active").count() == 1, "空预设 P1 未被高亮"
            assert page.locator(".preset-item.active .preset-active-tag").count() == 1, "缺少「当前」标记"
            assert "当前" in page.locator(".preset-item.active .preset-active-tag").inner_text(), "当前标记文案错误"
            print("[ok] 1) 空预设 P1 自动高亮为「当前」")

            # 2) 勾选「只看我」-> 高亮应消失
            mine = page.locator("label.toggle").filter(has_text="只看我")
            mine.click()
            page.wait_for_timeout(300)
            assert page.locator(".preset-item.active").count() == 0, "应用筛选后空预设仍被高亮"
            print("[ok] 2) 应用「只看我」后高亮消失")

            # 3) 取消「只看我」-> 高亮回到 P1
            mine.click()
            page.wait_for_selector(".preset-item.active", timeout=5000)
            assert page.locator(".preset-item.active").count() == 1, "清除筛选后 P1 未重新高亮"
            print("[ok] 3) 清除筛选后高亮回到 P1")

            # 4) 勾选「只看我」后保存 P2（捕获 mineOnly）-> 高亮切到 P2
            mine.click()
            page.wait_for_timeout(300)
            page.fill(".preset-name-input", "P2")
            page.click(".preset-save button.btn--primary")
            page.wait_for_timeout(300)
            assert page.locator(".preset-item.active").count() == 1, "P2 未被高亮"
            active_name = page.locator(".preset-item.active .preset-apply").inner_text()
            assert active_name.strip() == "P2", f"高亮未切到 P2，实际={active_name}"
            print("[ok] 4) 保存 P2 后高亮切到 P2")

            # 5) 取消「只看我」-> 高亮回到 P1
            mine.click()
            page.wait_for_selector(".preset-item.active", timeout=5000)
            active_name = page.locator(".preset-item.active .preset-apply").inner_text()
            assert active_name.strip() == "P1", f"高亮未回到 P1，实际={active_name}"
            print("[ok] 5) 清除筛选后高亮回到 P1")

            page.screenshot(path="tests/_epic77_preset_active.png")
            print("[shot] tests/_epic77_preset_active.png")

            assert not errors, "控制台/页面错误: " + str(errors)
            assert not js_css_fail, "JS/CSS 加载失败: " + str(js_css_fail)
            print("[ok] 0 pageerror / console error / js+css 404")
            print("RESULT: PASS")
    except Exception as e:
        print("RESULT: FAIL ->", repr(e))
        if errors:
            print("ERRORS:", errors)
        if js_css_fail:
            print("JS_CSS_FAIL:", js_css_fail)
        raise


if __name__ == "__main__":
    main()


def test_epic77_v65_preset_active():
    main()
