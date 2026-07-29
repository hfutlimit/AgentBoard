"""
Epic 78 (v6.6): 任务视图「手动刷新 + 刷新中加载态」 —— 端到端验证
- 登录 admin -> 进入含任务的任务视图（/story/117，对应本次 tracked task 1140）
- 断言筛选工具栏渲染「刷新」按钮（#refreshBtn），初始可点击、显示「刷新」、无 spinner
- 1) 点击刷新 -> 刷新期间按钮变为 disabled 且出现 .refresh-spinner、文案「刷新中」
- 2) 刷新完成后按钮恢复 enabled、文案「刷新」、任务列表内容无丢失（task 1140 仍在）
- 3) 全程 0 pageerror / console error / .js+.css 404
- 纯前端验证；通过拦截 story tasks API 增加 ~800ms 延迟，使「刷新中」加载态可被稳定观测。
"""
import json
import time
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8080"
API = "http://127.0.0.1:58125"
USER = "admin"
PASS = "admin123"
STORY_ID = 117


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


def _delay_tasks_route(route):
    """拦截 story tasks API，增加 ~900ms 延迟，使「刷新中」按钮加载态可被稳定观测；其余请求转发到 58125"""
    url = route.request.url
    if "/api/stories/117/tasks" in url:
        resp = route.fetch(url=url.replace("58124", "58125"))
        time.sleep(0.9)
        route.fulfill(response=resp)
    else:
        route.continue_(url=url.replace("58124", "58125"))


def main():
    token, _ = login()
    errors = []
    js_css_fail = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-proxy-server"])
            page = browser.new_page()
            page.route("**://127.0.0.1:58124/**", _delay_tasks_route)
            init = (
                "localStorage.setItem('agentboard_token','%s');"
                "localStorage.setItem('agentboard_user','admin');"
                "localStorage.removeItem('agentboard_filter_presets');"
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
            # 等待首屏初始加载完成（骨架屏消失）再定位刷新按钮：侧栏预加载全部项目树，数据集较大时首屏渲染可能接近 20s
            page.wait_for_function("!document.querySelector('.skeleton')", timeout=60000)
            page.wait_for_selector("#refreshBtn", timeout=15000)
            page.wait_for_selector(".entity-item, .kanban-card", timeout=15000)
            print("[ok] 任务视图与刷新按钮渲染；任务列表已加载")

            # 初始态：可点击、文案「刷新」、无 spinner
            btn = page.locator("#refreshBtn")
            assert btn.is_enabled(), "刷新按钮初始应可点击"
            assert "刷新" in btn.inner_text(), "初始文案应为「刷新」"
            assert page.locator("#refreshBtn .refresh-spinner").count() == 0, "初始不应有 spinner"
            print("[ok] 初始态：刷新按钮可点击、文案「刷新」、无 spinner")

            # 断言 tracked task 1140 存在
            body_text = page.locator("body").inner_text()
            assert "v6.6" in body_text, "任务 1140(v6.6) 未渲染"
            print("[ok] 任务 1140 (v6.6) 已渲染")

            # 点击刷新，观测「刷新中」加载态（story tasks API 被拦截延迟 ~900ms）
            btn.click()
            # 刷新期间：按钮 disabled 且出现 spinner（在延迟窗口内必须可见）
            page.wait_for_function(
                "document.querySelector('#refreshBtn') && "
                "(document.querySelector('#refreshBtn').disabled && "
                "document.querySelector('#refreshBtn .refresh-spinner'))",
                timeout=5000,
            )
            assert "刷新中" in btn.inner_text(), "刷新中文案应为「刷新中」"
            print("[ok] 刷新中：按钮 disabled + spinner 加载态 + 文案「刷新中」")

            # 刷新完成：按钮恢复 enabled、文案「刷新」、任务仍在
            page.wait_for_function(
                "document.querySelector('#refreshBtn') && "
                "!document.querySelector('#refreshBtn').disabled && "
                "document.querySelector('#refreshBtn').innerText.includes('刷新')",
                timeout=15000,
            )
            assert btn.is_enabled(), "刷新完成后按钮应恢复可点击"
            assert "刷新" in btn.inner_text(), "完成后文案应为「刷新」"
            assert page.locator("#refreshBtn .refresh-spinner").count() == 0, "完成后不应有 spinner"
            body_text2 = page.locator("body").inner_text()
            assert "v6.6" in body_text2, "刷新后任务 1140(v6.6) 丢失"
            print("[ok] 刷新完成：按钮恢复、任务列表内容无丢失")

            page.screenshot(path="tests/_epic78_refresh.png")
            print("[shot] tests/_epic78_refresh.png")

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


def test_epic78_v66_refresh():
    main()
