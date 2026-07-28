"""
Task 708 (Epic 27 / Story 45): 性能指标显示（加载时间 / API 响应时间）—— 端到端验证
- 登录 admin -> 访问 /documents（触发若干 API 调用，perfTracker 采集延迟）
- 断言：
  1) 顶部栏渲染 #perf-toggle 开关
  2) 常驻性能徽标 .perf-badge 默认可见，含「加载」「API」两项指标
  3) 点击 #perf-toggle -> 徽标隐藏；再次点击 -> 徽标恢复（显隐持久化）
  4) 点击 .perf-badge -> 系统状态弹层 .health-popover 展开，内含 .perf-section 性能指标区
  5) 等待 ~3.5s 实时刷新（2s 轮询）后 .perf-metrics 渲染出最近 API 请求（API 延迟已采集）
  6) 0 pageerror / console error / .js+.css 404
- 纯前端验证，不创建/修改任何追踪实体，无清理负担。
"""
import json
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8080"
API = "http://127.0.0.1:58125"
USER = "admin"
PASS = "admin123"


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
            # API 注入端口 58124 -> 58125 重定向（web_app 默认 API_URL=58124）
            page.route("**://127.0.0.1:58124/**",
                        lambda r: r.continue_(url=r.request.url.replace("58124", "58125")))
            init = (
                "localStorage.setItem('agentboard_token','%s');"
                "localStorage.setItem('agentboard_user','admin');"
                "localStorage.setItem('agentboard_story_view','list');"
                "localStorage.setItem('agentboard_story_group','none');" % token
            )
            page.add_init_script(init)
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: (
                js_css_fail.append(r.url) if (r.url.endswith(".js") or r.url.endswith(".css")) else None
            ))

            page.goto(WEB + "/documents", wait_until="domcontentloaded")
            page.wait_for_selector("#perf-toggle", timeout=20000)
            page.wait_for_selector(".entity-list, .documents-view, main", timeout=10000)

            # 1) 常驻性能徽标默认可见
            page.wait_for_selector(".perf-badge", timeout=10000)
            badge = page.locator(".perf-badge")
            assert badge.is_visible(), "性能徽标未默认显示"
            badge_text = badge.inner_text()
            assert "加载" in badge_text, "徽标缺少加载指标"
            assert "API" in badge_text, "徽标缺少 API 指标"
            print(f"[ok] 徽标渲染: {badge_text.replace(chr(10),' ')}")

            # 2) 显隐切换（持久化）
            page.click("#perf-toggle")
            page.wait_for_selector(".perf-badge", state="detached", timeout=5000)
            assert page.locator(".perf-badge").count() == 0, "点击开关后徽标未隐藏"
            page.click("#perf-toggle")
            page.wait_for_selector(".perf-badge", timeout=5000)
            assert page.locator(".perf-badge").count() == 1, "再次点击开关后徽标未恢复"
            print("[ok] 徽标显隐切换生效")

            # 3) 点击徽标展开系统状态弹层 + 性能指标区
            page.click(".perf-badge")
            page.wait_for_selector(".health-popover", timeout=5000)
            page.wait_for_selector(".perf-section", timeout=5000)
            assert page.locator(".perf-section").is_visible(), "性能指标区未渲染"
            print("[ok] 点击徽标展开性能指标区")

            # 4) 等待实时刷新后渲染最近 API 请求（API 延迟已采集）
            page.wait_for_timeout(3500)
            assert page.locator(".perf-metrics").count() >= 1, "实时刷新后未渲染最近 API 请求"
            rows = page.locator(".perf-metric-row").count()
            assert rows >= 1, "最近 API 请求列表为空"
            print(f"[ok] 实时采集到 {rows} 条 API 请求延迟记录")

            page.screenshot(path="tests/_task708_perf.png")
            print("[shot] tests/_task708_perf.png")

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


def test_task708_perf_badge():
    main()
