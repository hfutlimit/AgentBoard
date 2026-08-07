"""Epic 122 切片 3 M1 — Webhook 事件接入 E2E（REST + Playwright）。

验证点：
1. REST：登录 → 建项目/epic/story → 建全局 webhook（events 过滤）→ 建 story/task
   触发事件 → 业务端点全部 200（webhook 派发不阻断主业务）；webhook CRUD 正常；
2. 前端：登录后首页 / 项目页 / Story 页渲染正常，0 console error / 0 pageerror /
   0 js·css 失败；
3. 截图落 tmp/。

运行：
    <venv-python> tests/test_epic122_s3m1_e2e.py
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

    st, login = _api("POST", "/api/auth/login", body={"username": USERNAME, "password": PASSWORD})
    assert st == 200, f"登录失败 {st}: {login}"
    token = login["token"]

    # ---------- Webhook CRUD + 事件点 200 ----------
    st, wh = _api("POST", "/api/webhooks", token, {
        "name": "s3m1-e2e",
        "url": "http://127.0.0.1:9/unreachable",  # 不可达 → 派发失败也不影响业务
        "events": ["story.created", "task.ready_for_review"],
    })
    assert st == 201, f"建 webhook 失败 {st}: {wh}"
    wid = wh["id"]
    print(f"[REST] webhook {wid} 创建 OK")

    st, lst = _api("GET", "/api/webhooks", token)
    assert st == 200 and any(w["id"] == wid for w in lst["items"]), "webhook 列表未包含新建项"
    print(f"[REST] webhook 列表 OK（{len(lst['items'])} 条）")

    # 事件点触发（业务端点必须全部 200，即使 webhook 派发失败）
    st, pid = _api("POST", "/api/projects", token, {"name": "S3M1 E2E 项目"})
    assert st in (200, 201), f"建项目失败 {st}"
    pid = pid["id"]
    st, eid = _api("POST", f"/api/projects/{pid}/epics", token, {"title": "S3M1 E2E Epic"})
    assert st in (200, 201), f"建 Epic 失败 {st}"
    eid = eid["id"]
    st, sid = _api("POST", f"/api/epics/{eid}/stories", token, {"title": "S3M1 E2E Story"})
    assert st in (200, 201), f"建 Story（story.created 事件点）失败 {st}: {sid}"
    sid = sid["id"]
    print(f"[REST] story.created 事件点 → HTTP {st}")

    st, tid = _api("POST", f"/api/stories/{sid}/tasks", token,
                   {"project_id": pid, "title": "S3M1 E2E Task"})
    assert st == 201, f"建 Task 失败 {st}: {tid}"
    tid = tid["id"]
    st, _ = _api("POST", f"/api/tasks/{tid}/claim", token)
    assert st == 200, f"claim 失败 {st}"
    st, _ = _api("POST", f"/api/tasks/{tid}/submit-review", token)
    assert st == 200, f"submit-review（task.ready_for_review 事件点）失败 {st}"
    print(f"[REST] task.ready_for_review 事件点 → HTTP {st}")

    # toggle / delete webhook
    st, _ = _api("PATCH", f"/api/webhooks/{wid}?enabled=false", token)
    assert st == 200, f"toggle webhook 失败 {st}"
    st, _ = _api("DELETE", f"/api/webhooks/{wid}", token)
    assert st == 200, f"删除 webhook 失败 {st}"
    print("[REST] webhook toggle/delete OK")

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

        page.goto(f"{WEB_URL}/", wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector(".skeleton", state="detached", timeout=20000)
        except Exception:
            failures.append("首页骨架屏 20s 内未消失")
        page.wait_for_selector(".dashboard-analytics", timeout=20000)
        page.screenshot(path=os.path.join(OUT_DIR, "s3m1_e2e_home.png"), full_page=True)
        print("[E2E] 首页 OK")

        page.goto(f"{WEB_URL}/project/{pid}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector(".tab-bar", timeout=15000)
        page.wait_for_selector(".entity-list, .epic-list", timeout=15000)
        page.screenshot(path=os.path.join(OUT_DIR, "s3m1_e2e_project.png"), full_page=True)
        print("[E2E] 项目页 OK")

        page.goto(f"{WEB_URL}/story/{sid}", wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector(".story-description", timeout=15000)
        except Exception:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector(".story-description", timeout=15000)
        page.screenshot(path=os.path.join(OUT_DIR, "s3m1_e2e_story.png"), full_page=True)
        print("[E2E] Story 页 OK")

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
