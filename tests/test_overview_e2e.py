"""Epic 117 (Task 995) — Dashboard 首页两阶段渲染 E2E 验证。

验证点：
1. 登录后首页正常渲染（hero / 统计卡 / 图表 / 项目空间），无骨架屏残留；
2. 统计卡数值来自 overview（statTasks > 0 且与后端 /api/overview 一致）；
3. 首页首屏不等待整树（骨架屏提前消失），仍渲染 dashboard-analytics；
4. 0 console error / 0 pageerror / 0 js·css 失败资源。

运行：
    <venv-python> tests/test_overview_e2e.py
"""
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from playwright.sync_api import sync_playwright  # noqa: E402

WEB_URL = os.getenv("AGENTBOARD_WEB_URL", "http://localhost:28080")
API_URL = os.getenv("AGENTBOARD_API_URL", "http://localhost:18000")
USERNAME = os.getenv("AGENTBOARD_E2E_USER", "admin")
PASSWORD = os.getenv("AGENTBOARD_E2E_PASS", "admin123")

OUT_DIR = os.path.join(_ROOT, "tmp")
os.makedirs(OUT_DIR, exist_ok=True)


def _login_token() -> str:
    import json
    import urllib.request

    req = urllib.request.Request(
        f"{API_URL}/api/auth/login",
        data=json.dumps({"username": USERNAME, "password": PASSWORD}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)["token"]


def _api_overview(token: str) -> dict:
    import json
    import urllib.request

    req = urllib.request.Request(
        f"{API_URL}/api/overview",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def main() -> int:
    token = _login_token()
    overview = _api_overview(token)
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        console_errors: list[str] = []
        page_errors: list[str] = []
        failed_requests: list[str] = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        # 只计 js/css 失败；/api/* ERR_ABORTED 为导航竞态良性（项目既有规则）
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.method} {req.url} {req.failure}") if (req.url.endswith(".js") or req.url.endswith(".css")) else None)
        page.on("response", lambda resp: failed_requests.append(f"HTTP {resp.status} {resp.url}") if resp.status >= 400 and (resp.url.endswith(".js") or resp.url.endswith(".css")) else None)

        page.add_init_script(f"localStorage.setItem('agentboard_token', '{token}');")
        page.goto(f"{WEB_URL}/", wait_until="domcontentloaded", timeout=30000)
        # 骨架屏应较快消失（两阶段：overview 驱动首屏）
        try:
            page.wait_for_selector(".skeleton", state="detached", timeout=15000)
        except Exception:
            failures.append("首页骨架屏 15s 内未消失")
        page.wait_for_selector(".dashboard-analytics", timeout=15000)
        page.wait_for_selector(".stats-row", timeout=15000)

        # 统计卡数值与 overview 对齐（statTasks = counts.tasks）
        hero_text = page.inner_text("section.hero")
        expected_tasks = overview["counts"]["tasks"]
        if f"{expected_tasks} 项任务" not in hero_text:
            failures.append(f"hero 未显示 overview 任务数 {expected_tasks}: {hero_text[:80]}")
        stat_numbers = page.locator(".stats-row .stat-number").all_inner_texts()
        if len(stat_numbers) < 4:
            failures.append(f"统计卡数量异常: {len(stat_numbers)}")
        if overview["counts"]["tasks"] > 0:
            chart_visible = page.locator(".status-donut, .activity-chart, .project-progress-row").count() > 0
            if not chart_visible:
                failures.append("有任务数据但图表区未渲染")

        page.screenshot(path=os.path.join(OUT_DIR, "overview_home.png"), full_page=True)

        # 跳转项目页验证整树信号仍正常（回归）
        page.goto(f"{WEB_URL}/project/3", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(".entity-list, .tab-bar", timeout=15000)
        page.wait_for_timeout(2500)
        page.screenshot(path=os.path.join(OUT_DIR, "overview_project.png"), full_page=True)

        browser.close()

    if console_errors:
        failures.append(f"console errors: {console_errors[:5]}")
    if page_errors:
        failures.append(f"page errors: {page_errors[:5]}")
    if failed_requests:
        failures.append(f"failed reqs: {failed_requests[:5]}")

    print(f"overview counts: {overview['counts']}")
    print(f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
