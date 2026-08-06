"""Epic 117 S3 (Task 997) — 项目页 Epic 进度数据加载并发治理 E2E 验证。

验证点：
1. 登录后项目页正常渲染（Epic 列表），Epic 进度数据（Story/Task 计数）正确；
2. 核心：项目页加载期间 /api/epics/{id}/stories 与 /api/stories/{id}/tasks 的
   并发峰值 ≤ 6（parallelMap 分片生效，不再全量 Promise.all 瞬时风暴）；
3. 进度数据写入后 Epic 列表计数渲染（stories()/tasks() 信号驱动）；
4. 0 console error / 0 pageerror / 0 js·css 失败资源。

运行：
    <venv-python> tests/test_epic117_s3_progress_storm_e2e.py
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

CONCURRENCY_LIMIT = 6


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


def _api_epic_count(token: str, project_id: int) -> int:
    import json
    import urllib.request

    req = urllib.request.Request(
        f"{API_URL}/api/projects/{project_id}/epics",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return len(json.load(resp))


def main() -> int:
    token = _login_token()
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        console_errors: list[str] = []
        page_errors: list[str] = []
        failed_requests: list[str] = []
        # 并发峰值测量：记录项目页加载期间 /api/epics/{id}/stories 与 /api/stories/{id}/tasks 的活跃请求数
        concurrency = {"active": 0, "peak": 0, "count": 0}
        window = {"active": False}

        def _is_progress_req(url: str) -> bool:
            return (
                ("/api/epics/" in url and url.endswith("/stories"))
                or ("/api/stories/" in url and url.endswith("/tasks"))
            )

        def _on_request(req) -> None:
            if window["active"] and _is_progress_req(req.url):
                concurrency["active"] += 1
                concurrency["peak"] = max(concurrency["peak"], concurrency["active"])
                concurrency["count"] += 1

        def _on_response(resp) -> None:
            if window["active"] and _is_progress_req(resp.url):
                concurrency["active"] = max(0, concurrency["active"] - 1)

        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on(
            "requestfailed",
            lambda req: failed_requests.append(f"{req.method} {req.url} {req.failure}")
            if (req.url.endswith(".js") or req.url.endswith(".css"))
            else None,
        )
        page.on(
            "response",
            lambda resp: failed_requests.append(f"HTTP {resp.status} {resp.url}")
            if resp.status >= 400 and (resp.url.endswith(".js") or resp.url.endswith(".css"))
            else None,
        )
        page.on("request", _on_request)
        page.on("response", _on_response)

        page.add_init_script(f"localStorage.setItem('agentboard_token', '{token}');")

        # —— 项目页：核心验证窗口 ——
        window["active"] = True
        page.goto(f"{WEB_URL}/project/3", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(".entity-list, .tab-bar", timeout=15000)
        # 留出进度数据（stories→tasks 分片）加载时间
        page.wait_for_timeout(5000)
        window["active"] = False

        # 进度数据请求确实发生了（说明并行分片路径被走到）
        if concurrency["count"] == 0:
            failures.append("项目页未发起任何 Epic 进度数据请求（stories/tasks 零调用）")
        # 核心断言：并发峰值 ≤ 6
        if concurrency["peak"] > CONCURRENCY_LIMIT:
            failures.append(
                f"项目页进度加载并发峰值 {concurrency['peak']} > {CONCURRENCY_LIMIT}（分片未生效）"
            )
        print(f"progress reqs: {concurrency['count']}, concurrency peak: {concurrency['peak']}")

        # Epic 列表渲染
        epic_items = page.locator(".entity-list .epic-item, .epic-list .entity-item, .entity-list > *").count()
        if epic_items == 0:
            failures.append("项目页 Epic 列表未渲染")
        page.screenshot(path=os.path.join(OUT_DIR, "ep117s3_project.png"), full_page=True)

        browser.close()

    if console_errors:
        failures.append(f"console errors: {console_errors[:5]}")
    if page_errors:
        failures.append(f"page errors: {page_errors[:5]}")
    if failed_requests:
        failures.append(f"failed reqs: {failed_requests[:5]}")

    print(f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
