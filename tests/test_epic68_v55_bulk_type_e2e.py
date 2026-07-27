"""
Epic 68 v5.5 — 任务列表批量修改类型（type）端到端验证。

流程：
  1. 登录（token 注入 localStorage），访问追踪 story 107（含任务 1123/1124/1125）
  2. 勾选 3 个任务 → 点击「批量修改类型」
  3. 点击类型面板「Bug」
  4. 断言：toast 提示「已批量更新 3 个任务的类型为「Bug」」
  5. 断言：API 复核 3 个任务 type 均变为 'bug'
  6. 断言：0 pageerror / console error / .js+.css 404
  7. 清理：PATCH 还原 3 个任务 type='task'；删除探针任务 1124/1125（保留追踪任务 1123）

断言全部绿色即视为通过。
"""
import json
import urllib.request
import urllib.error
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:58125"
WEB = "http://127.0.0.1:8090"
STORY_ID = 107  # Story 68.1
TRACK_TASK = 1123  # 追踪任务（保留）
PROBE_TASKS = [1124, 1125]  # 探针任务（清理删除）


def api(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        txt = resp.read().decode()
        return resp.status, (json.loads(txt) if txt else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def main():
    # ---- login ----
    st, user = api("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    assert st == 200, f"login failed {st}"
    token = user["token"]

    errors = []
    console_errors = []
    failed_assets = []
    ids = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-proxy-server"])
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: (
                failed_assets.append(r.url) if r.url.endswith(".js") or r.url.endswith(".css") else None
            ))

            page.add_init_script(f"localStorage.setItem('agentboard_token', '{token}');")
            page.goto(WEB + f"/story/{STORY_ID}", wait_until="domcontentloaded")
            page.wait_for_selector(".entity-item--rich", timeout=20000)

            rows_before = page.locator(".entity-item--rich").count()
            assert rows_before == 3, f"expected 3 task rows, got {rows_before}"

            # read task ids from row links
            links = page.locator(".entity-item--rich a.entity-item-link")
            hrefs = [links.nth(i).get_attribute("href") for i in range(rows_before)]
            ids = [int(h.rstrip("/").split("/")[-1]) for h in hrefs]
            print(f"[rows] ids={ids}")

            # select all 3 checkboxes
            checks = page.locator(".entity-item--rich .task-checkbox")
            assert checks.count() >= 3, f"checkboxes count {checks.count()}"
            for i in range(3):
                checks.nth(i).click()
            page.wait_for_selector(".bulk-action-bar", timeout=10000)
            assert "3 项已选" in page.locator(".bulk-action-bar").inner_text(), "selection count mismatch"

            # click 批量修改类型
            page.get_by_role("button", name="批量修改类型").click()
            page.wait_for_selector(".bulk-panel .status-btn.type--bug", timeout=10000)

            # click Bug
            page.locator(".bulk-panel .status-btn.type--bug").click()
            page.wait_for_timeout(4000)

            # toast appears
            page.wait_for_selector("#toast .toast", timeout=15000)
            toast_text = page.locator("#toast .toast").inner_text(timeout=5000)
            assert "已批量更新" in toast_text and "Bug" in toast_text, f"toast unexpected: {toast_text}"

            # API verify each task type == 'bug'
            for tid in ids:
                st_t, t = api("GET", f"/api/tasks/{tid}", token=token)
                assert st_t == 200, f"get task {tid} failed {st_t}"
                assert t.get("type") == "bug", f"task {tid} type expected 'bug', got {t.get('type')}"
            print(f"[verify] all {len(ids)} tasks type='bug' via API; toast='{toast_text}'")

            browser.close()

        print(f"[errors] pageerror={len(errors)} console_err={len(console_errors)} asset_fail={len(failed_assets)}")
        assert len(errors) == 0, f"page errors: {errors}"
        assert len(console_errors) == 0, f"console errors: {console_errors}"
        assert len(failed_assets) == 0, f"failed assets: {failed_assets}"
        print("E2E PASS")
    finally:
        # ---- cleanup: restore type='task' for all 3, delete probe tasks ----
        for tid in ids:
            api("PATCH", f"/api/tasks/{tid}", {"type": "task"}, token=token)
        for tid in PROBE_TASKS:
            api("DELETE", f"/api/tasks/{tid}", token=token)
        print(f"[cleanup] restored types, deleted probe tasks {PROBE_TASKS}")


if __name__ == "__main__":
    main()
