"""Epic 117 S2 (Task 996) — 首页整树加载请求风暴治理 E2E 验证。

验证点：
1. 登录后首页正常渲染（hero / 统计卡 / 图表），无骨架屏残留；
2. 核心：overview 成功时，首页加载不再发起 /api/stories/{id}/tasks 请求（请求风暴治理核心验收）；
3. 统计卡数值与 /api/overview 对齐；
4. 项目页回归：Epic 列表正常渲染（独立加载路径不受影响）；
5. Story 页回归：任务列表正常渲染（loadStoryTasks 独立加载路径不受影响）；
6. 0 console error / 0 pageerror / 0 js·css 失败资源。

运行：
    <venv-python> tests/test_epic117_s2_tree_storm_e2e.py
"""
import os
import sys

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


def _api_first_story(token: str, epic_id: int) -> int | None:
    """从本机 API 取 Epic 下第一个 Story id（本机 DB 与远程生产不同，不可硬编码 story id）。"""
    import json
    import urllib.request

    req = urllib.request.Request(
        f"{API_URL}/api/epics/{epic_id}/stories",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        stories = json.load(resp)
    return stories[0]["id"] if stories else None


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
        story_task_requests: list[str] = []
        home_phase = {"active": False}  # 仅统计首页加载阶段的 Task 级请求

        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.method} {req.url} {req.failure}") if (req.url.endswith(".js") or req.url.endswith(".css")) else None)
        page.on("response", lambda resp: failed_requests.append(f"HTTP {resp.status} {resp.url}") if resp.status >= 400 and (resp.url.endswith(".js") or resp.url.endswith(".css")) else None)
        # 统计首页加载期间发起的 /api/stories/{id}/tasks 请求（Task 级全量加载标志）；
        # 项目页/Story 页独立加载路径也会发 Task 请求，故用 home_phase 限定统计窗口
        def _track(req) -> None:
            if home_phase["active"] and "/api/stories/" in req.url and req.url.endswith("/tasks"):
                story_task_requests.append(req.url)
        page.on("request", _track)

        home_phase["active"] = True
        page.add_init_script(f"localStorage.setItem('agentboard_token', '{token}');")
        page.goto(f"{WEB_URL}/", wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector(".skeleton", state="detached", timeout=15000)
        except Exception:
            failures.append("首页骨架屏 15s 内未消失")
        page.wait_for_selector(".dashboard-analytics", timeout=15000)
        page.wait_for_selector(".stats-row", timeout=15000)
        # 留出后台整树加载时间，再断言 Task 级请求
        page.wait_for_timeout(4000)
        home_phase["active"] = False

        # 核心断言：overview 成功时首页不应发起 /api/stories/{id}/tasks（请求风暴治理）
        if story_task_requests:
            failures.append(f"首页仍发起 Task 级全量加载 {len(story_task_requests)} 次: {story_task_requests[:3]}")

        # 统计卡数值与 overview 对齐
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

        page.screenshot(path=os.path.join(OUT_DIR, "ep117s2_home.png"), full_page=True)

        # 项目页回归：Epic 列表正常渲染（项目页独立加载路径不受影响）
        page.goto(f"{WEB_URL}/project/3", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(".entity-list, .tab-bar", timeout=15000)
        page.wait_for_timeout(2500)
        page.screenshot(path=os.path.join(OUT_DIR, "ep117s2_project.png"), full_page=True)

        # Story 页回归：任务列表正常渲染（loadStoryTasks 独立加载路径不受影响）
        # 本机 DB 与远程不同，动态取 Epic 117 下第一个 Story；无 Story 则跳过该回归项
        story_id = _api_first_story(token, 117)
        if story_id is None:
            failures.append("本机无 Epic 117 Story，Story 页回归跳过")
        else:
            try:
                page.goto(f"{WEB_URL}/story/{story_id}", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_selector(".story-description, .detail-panel", timeout=15000)
                # Story 页默认 detail tab，点「📝 Task 列表」进入任务列表
                page.get_by_text("Task 列表", exact=False).first.click(timeout=5000)
                page.wait_for_selector(".task-list-summary, .entity-list", timeout=15000)
                page.wait_for_timeout(2000)
                page.screenshot(path=os.path.join(OUT_DIR, "ep117s2_story.png"), full_page=True)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"Story 页回归失败: {exc}")

        browser.close()

    if console_errors:
        failures.append(f"console errors: {console_errors[:5]}")
    if page_errors:
        failures.append(f"page errors: {page_errors[:5]}")
    if failed_requests:
        failures.append(f"failed reqs: {failed_requests[:5]}")

    print(f"overview counts: {overview['counts']}")
    print(f"story task requests on home: {len(story_task_requests)}")
    print(f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
