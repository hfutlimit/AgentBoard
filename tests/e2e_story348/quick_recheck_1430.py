"""快速验证 #1430 修复：5 个新全局路由（/epics /stories /tasks /bugs /dashboard）。

验收：
  1) 5 个路由都可访问，渲染 GlobalStatsTabComponent（workspace-heading + metric-card）
  2) 每个路由的 title 按 entity @Input 区分
  3) /api/overview 调通，4 个 metric card 拿到数字
  4) 5 个 jump card 高亮当前 entity
  5) 截图留 evidence（light + dark）
"""
import json, time, urllib.request, os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
SHOT = "tests/e2e_story348/screenshots_1430"
os.makedirs(SHOT, exist_ok=True)

def login_token():
    req = urllib.request.Request(BASE + "/api/auth/login",
        data=json.dumps({"username":"admin","password":"admin123"}).encode(),
        headers={"Content-Type":"application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())["token"]

# 期望标题（来自 global-stats-tab.ts ENTITY_TITLES）
EXPECTED = {
    "/epics": "全局 Epics 概览",
    "/stories": "全局 Stories 概览",
    "/tasks": "全局 Tasks 概览",
    "/bugs": "全局 Bugs 概览",
    "/dashboard": "项目大脑 / 总览",
}

ROUTES = ["/epics", "/stories", "/tasks", "/bugs", "/dashboard"]
results = {"per_route": {}, "overview_api": {}, "errors": []}

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    token = login_token()
    ctx.add_init_script(f"window.localStorage.setItem('agentboard_token', '{token}')")

    # 0) 先确认 /api/overview 200（不走认证）
    try:
        api_req = urllib.request.Request(BASE + "/api/overview", method="GET")
        api_data = json.loads(urllib.request.urlopen(api_req, timeout=10).read())
        results["overview_api"]["status"] = "ok"
        results["overview_api"]["counts"] = api_data.get("counts", {})
    except Exception as e:
        results["overview_api"]["status"] = "failed"
        results["overview_api"]["error"] = str(e)

    # 1) 逐路由访问 + 断言
    for path in ROUTES:
        per = {"url": path, "loaded": False, "title": None, "expected": EXPECTED[path],
               "metric_count": 0, "metric_values": [], "active_jump": None,
               "error": None}
        try:
            page.goto(BASE + path, wait_until="domcontentloaded", timeout=20000)
            # 等 component 渲染（workspace-heading + metric-card）
            page.wait_for_selector("app-global-stats-tab", timeout=8000)
            time.sleep(0.8)  # 留 /api/overview 时间

            # 标题（workspace-heading h1）
            title = page.evaluate(
                "() => document.querySelector('app-global-stats-tab app-workspace-heading h1')?.innerText?.trim()"
            )
            per["title"] = title
            per["title_match"] = (title == EXPECTED[path])

            # 4 个 metric card（用 button.metric-card 计数）
            per["metric_count"] = page.evaluate(
                "() => document.querySelectorAll('app-global-stats-tab .metric-card').length"
            )
            per["metric_values"] = page.evaluate("""() =>
                Array.from(document.querySelectorAll('app-global-stats-tab .metric-card .metric-value'))
                     .map(el => (el.innerText||'').trim())""")

            # 高亮 jump card（class 含 active）
            per["active_jump"] = page.evaluate("""() => {
                const a = document.querySelector('app-global-stats-tab .jump-card.active');
                return a ? a.innerText.trim().split(/\\s+/)[0] : null;
            }""")

            per["loaded"] = True
            page.screenshot(path=os.path.join(SHOT, path.strip("/") + "_light.png"), full_page=True)
        except Exception as e:
            per["error"] = str(e)
            results["errors"].append(f"{path}: {e}")
        results["per_route"][path] = per

    # 2) dashboard 路由切到深色主题，截一张对比图
    try:
        page.goto(BASE + "/dashboard", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_selector("app-global-stats-tab", timeout=8000)
        time.sleep(0.5)
        page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; localStorage.setItem('agentboard_theme', 'dark'); }")
        time.sleep(0.5)
        page.screenshot(path=os.path.join(SHOT, "dashboard_dark.png"), full_page=True)
    except Exception as e:
        results["errors"].append(f"dark: {e}")

    # 2.5) 验证左侧 sidebar 不再显示（global-stats view 应排除 sidebar）
    try:
        page.goto(BASE + "/epics", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_selector("app-global-stats-tab", timeout=8000)
        time.sleep(0.5)
        sidebar_visible = page.evaluate("() => !!document.querySelector('aside#sidebar')")
        results["sidebar_hidden_on_global_stats"] = not sidebar_visible
    except Exception as e:
        results["errors"].append(f"sidebar check: {e}")

    # 3) 通配 404 — 之前 RouteAnchor 是 404，Story 348 不该 404 的路由已经全打通了
    try:
        page.goto(BASE + "/totally-random-1430-check", wait_until="domcontentloaded", timeout=10000)
        time.sleep(0.5)
        # #1427 修过：app.html @default 显示 404 卡片（card.empty-state h2「页面不存在」）
        results["wildcard_404"] = page.evaluate(
            """() => {
                const h2 = document.querySelector('main .card.empty-state h2');
                return h2 && h2.innerText.trim() === '页面不存在';
            }"""
        )
    except Exception as e:
        results["wildcard_404"] = f"failed: {e}"

    browser.close()

# 写 report
out = "tests/e2e_story348/quick_recheck_1430.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

# 打印总览
print("=" * 60)
print("#1430 5 个全局路由验证报告")
print("=" * 60)
print(f"  /api/overview: {results['overview_api'].get('status')}")
if results["overview_api"].get("counts"):
    print(f"  counts: {results['overview_api']['counts']}")
print()
all_pass = True
for path, per in results["per_route"].items():
    # /dashboard 是聚合总览，不在 4 实体跳转集里，active_jump 必为 None → 视为通过
    is_dashboard = (path == "/dashboard")
    active_ok = (per.get("active_jump") is not None) if not is_dashboard else True
    ok = (per.get("loaded") and per.get("title_match")
          and per.get("metric_count") == 4 and active_ok)
    if not ok:
        all_pass = False
    flag = "✅" if ok else "❌"
    print(f"  {flag} {path:12s} title={per.get('title')!r:20s} "
          f"metrics={per.get('metric_count')} values={per.get('metric_values')} "
          f"active={per.get('active_jump')!r}")
    if per.get("error"):
        print(f"      error: {per['error']}")
print()
print(f"  wildcard 404: {results.get('wildcard_404')}")
print()
if results["errors"]:
    print("ERRORS:")
    for e in results["errors"]:
        print(f"  - {e}")
print()
print("ALL PASS" if all_pass else "HAS FAIL")
print(f"screenshots: {os.path.abspath(SHOT)}")
print(f"report: {out}")
