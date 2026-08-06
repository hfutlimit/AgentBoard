"""Epic 122 S1 M3 — Playwright E2E 回归验证。

验证点：
1. 登录后首页渲染（dashboard-analytics），0 骨架屏残留；
2. 项目页 / 新建项目 → Epic → Story → Task 渲染正常；
3. Story 详情页渲染（评审态 chip 不报错）；
4. 0 console error / 0 pageerror / 0 js·css 失败资源。

运行：
    <venv-python> tests/test_epic122_s1_m3_e2e.py
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


def _api(method: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    import json
    import urllib.request

    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{API_URL}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def main() -> int:
    failures: list[str] = []
    # 登录 + 动态建数据（与本地 DB 解耦）
    token = _api("POST", "/api/auth/login", body={"username": USERNAME, "password": PASSWORD})["token"]
    pid = _api("POST", "/api/projects", token, {"name": "M3 E2E 验证项目"})["id"]
    eid = _api("POST", f"/api/projects/{pid}/epics", token, {"title": "M3 E2E Epic"})["id"]
    sid = _api("POST", f"/api/epics/{eid}/stories", token, {"title": "M3 E2E Story"})["id"]
    _api("POST", f"/api/stories/{sid}/tasks", token, {"project_id": pid, "title": "M3 E2E Task", "type": "task"})

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        console_errors: list[str] = []
        page_errors: list[str] = []
        failed_requests: list[str] = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.method} {req.url} {req.failure}")
                if (req.url.endswith(".js") or req.url.endswith(".css")) else None)
        page.on("response", lambda resp: failed_requests.append(f"HTTP {resp.status} {resp.url}")
                if resp.status >= 400 and (resp.url.endswith(".js") or resp.url.endswith(".css")) else None)

        page.add_init_script(f"localStorage.setItem('agentboard_token', '{token}');")

        # 1. 首页
        page.goto(f"{WEB_URL}/", wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector(".skeleton", state="detached", timeout=15000)
        except Exception:
            failures.append("首页骨架屏 15s 内未消失")
        page.wait_for_selector(".dashboard-analytics", timeout=15000)
        page.screenshot(path=os.path.join(OUT_DIR, "m3_e2e_home.png"), full_page=True)

        # 2. 项目页（动态创建的项目）
        page.goto(f"{WEB_URL}/project/{pid}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(".tab-bar", timeout=15000)
        page.wait_for_selector(".entity-list, .epic-list", timeout=15000)
        page.screenshot(path=os.path.join(OUT_DIR, "m3_e2e_project.png"), full_page=True)

        # 3. Story 详情页（含评审态 status 映射，前端零改动回归）
        page.goto(f"{WEB_URL}/story/{sid}", wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector(".story-description", timeout=15000)
        except Exception:
            # 深链有信号竞态，重试一次（项目既有规则）
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(".story-description", timeout=15000)
        page.screenshot(path=os.path.join(OUT_DIR, "m3_e2e_story.png"), full_page=True)

        browser.close()

    print(f"console_errors: {console_errors}")
    print(f"page_errors: {page_errors}")
    print(f"failed_requests: {failed_requests}")
    if console_errors:
        failures.append(f"console error: {console_errors[:5]}")
    if page_errors:
        failures.append(f"pageerror: {page_errors[:5]}")
    if failed_requests:
        failures.append(f"js/css 失败: {failed_requests[:5]}")

    if failures:
        print("E2E FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("E2E ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
