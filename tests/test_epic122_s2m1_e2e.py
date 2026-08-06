"""Epic 122 切片 2 M1 — REST 全链路 + Playwright E2E 回归验证。

验证点：
1. REST：claim → 200/in_progress/assignee 回填；重复 claim → 409；submit-review → in_review；
   非认领用户 submit-review → 422；claim 端点 401（无 token）；
2. 前端：登录后首页 / 项目页 / Story 页渲染正常，0 console error / 0 pageerror / 0 js·css 失败；
3. 截图落 tmp/。

运行：
    <venv-python> tests/test_epic122_s2m1_e2e.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from playwright.sync_api import sync_playwright  # noqa: E402

WEB_URL = os.getenv("AGENTBOARD_WEB_URL", "http://localhost:28080")
API_URL = os.getenv("AGENTBOARD_API_URL", "http://localhost:18000")
USERNAME = os.getenv("AGENTBOARD_E2E_USER", "admin")
PASSWORD = os.getenv("AGENTBOARD_E2E_PASS", "admin123")

OUT_DIR = os.path.join(_ROOT, "tmp")
os.makedirs(OUT_DIR, exist_ok=True)


def _api(method: str, path: str, token: str | None = None, body: dict | None = None):
    """返回 (status, json)。"""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{API_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {"detail": e.read().decode(errors="replace")[:200]}


def main() -> int:
    failures: list[str] = []

    # ---------- REST 全链路（真实 token） ----------
    st, login = _api("POST", "/api/auth/login", body={"username": USERNAME, "password": PASSWORD})
    assert st == 200, f"登录失败 {st}: {login}"
    token = login["token"]

    st, pid = _api("POST", "/api/projects", token, {"name": "S2M1 E2E 验证项目"})
    assert st in (200, 201), f"建项目失败 {st}: {pid}"
    pid = pid["id"]
    st, eid = _api("POST", f"/api/projects/{pid}/epics", token, {"title": "S2M1 E2E Epic"})
    assert st in (200, 201), f"建 Epic 失败 {st}"
    eid = eid["id"]
    st, sid = _api("POST", f"/api/epics/{eid}/stories", token, {"title": "S2M1 E2E Story"})
    assert st in (200, 201), f"建 Story 失败 {st}"
    sid = sid["id"]
    st, tid = _api("POST", f"/api/stories/{sid}/tasks", token,
                   {"project_id": pid, "title": "S2M1 E2E Task", "type": "task"})
    assert st == 201, f"建 Task 失败 {st}: {tid}"
    tid = tid["id"]

    # 1) claim → 200 / in_progress / assignee 回填
    st, body = _api("POST", f"/api/tasks/{tid}/claim", token)
    if st != 200 or body.get("status") != "in_progress" or body.get("assignee_id") != login["id"]:
        failures.append(f"claim 失败: {st} {body}")
    else:
        print(f"[REST] claim OK → task {tid} status={body['status']} assignee={body['assignee_id']}")

    # 2) 重复 claim → 409
    st, body = _api("POST", f"/api/tasks/{tid}/claim", token)
    if st != 409:
        failures.append(f"重复 claim 应为 409，实际 {st}: {body}")
    else:
        print(f"[REST] 重复 claim 409 OK: {body.get('detail', '')[:80]}")

    # 3) submit-review → 200 / in_review
    st, body = _api("POST", f"/api/tasks/{tid}/submit-review", token)
    if st != 200 or body.get("status") != "in_review":
        failures.append(f"submit-review 失败: {st} {body}")
    else:
        print(f"[REST] submit-review OK → task {tid} status={body['status']}")

    # 4) 无 token → 401（REQUIRE_AUTH=1 下）
    st, _ = _api("POST", f"/api/tasks/{tid}/claim")
    if st not in (401, 422):
        failures.append(f"无 token claim 应 401/422，实际 {st}")

    # ---------- Playwright 前端渲染 ----------
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

        # 首页
        page.goto(f"{WEB_URL}/", wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector(".skeleton", state="detached", timeout=15000)
        except Exception:
            failures.append("首页骨架屏 15s 内未消失")
        page.wait_for_selector(".dashboard-analytics", timeout=15000)
        page.screenshot(path=os.path.join(OUT_DIR, "s2m1_e2e_home.png"), full_page=True)

        # 项目页
        page.goto(f"{WEB_URL}/project/{pid}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(".tab-bar", timeout=15000)
        page.wait_for_selector(".entity-list, .epic-list", timeout=15000)
        page.screenshot(path=os.path.join(OUT_DIR, "s2m1_e2e_project.png"), full_page=True)

        # Story 页
        page.goto(f"{WEB_URL}/story/{sid}", wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector(".story-description", timeout=15000)
        except Exception:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(".story-description", timeout=15000)
        page.screenshot(path=os.path.join(OUT_DIR, "s2m1_e2e_story.png"), full_page=True)

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
