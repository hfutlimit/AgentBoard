"""
Epic 65 v5.2 — 任务列表批量复制选中任务（克隆）端到端验证。

流程：
  1. 经 API 在追踪 project 52 / epic 55 下新建临时 story，种子 3 个任务
  2. 登录（token 注入 localStorage），访问 /story/{sid}
  3. 勾选 3 个任务 → 点击「批量复制」
  4. 断言：toast 提示、列表出现 3 个 (副本)、API 复核副本数 == 3
  5. 断言：0 pageerror / console error / .js+.css 404
  6. 清理：删除副本 + 原始任务 + 临时 story

断言全部绿色即视为通过。
"""
import os
import sys
import json
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:58125"
WEB = "http://127.0.0.1:8090"
PROJ_ID = 52  # AUTODEV65
EPIC_ID = 55  # Epic 65 v5.2


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

    # ---- seed: scratch story + 3 tasks ----
    st, story = api("POST", f"/api/epics/{EPIC_ID}/stories",
                    {"title": "E2E-SEED-v52-bulk-duplicate", "epic_id": EPIC_ID, "project_id": PROJ_ID}, token)
    assert st == 201, f"create story failed {st}: {story}"
    sid = story["id"]
    seed_ids = []
    for i in range(3):
        st, t = api("POST", f"/api/stories/{sid}/tasks",
                    {"title": f"SEED-v52-{i}", "story_id": sid, "project_id": PROJ_ID,
                     "type": "task", "priority": "medium"}, token)
        assert st == 201, f"seed task {i} failed {st}: {t}"
        seed_ids.append(t["id"])
    print(f"[seed] story={sid} tasks={seed_ids}")

    errors = []
    console_errors = []
    failed_assets = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-proxy-server"])
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: (
                failed_assets.append(r.url) if r.url.endswith(".js") or r.url.endswith(".css") else None
            ))

            # inject token before app boot
            page.add_init_script(f"localStorage.setItem('agentboard_token', '{token}');")
            page.goto(WEB + f"/story/{sid}", wait_until="domcontentloaded")
            page.wait_for_selector(".entity-item", timeout=20000)

            # ensure exactly 3 seed rows
            rows_before = page.locator(".entity-list .entity-item").count()
            assert rows_before == 3, f"expected 3 seed rows, got {rows_before}"

            # select all 3 checkboxes
            checks = page.locator(".entity-list .task-checkbox")
            assert checks.count() >= 3, f"checkboxes count {checks.count()}"
            for i in range(3):
                checks.nth(i).click()
            page.wait_for_selector(".bulk-action-bar", timeout=10000)
            assert "3 项已选" in page.locator(".bulk-action-bar").inner_text(), "selection count mismatch"

            # click 批量复制
            page.get_by_role("button", name="批量复制").click()
            page.wait_for_timeout(12000)
            # API verify copies created (independent of toast)
            st, data = api("GET", f"/api/stories/{sid}/tasks?limit=200", token=token)
            items = data["items"] if isinstance(data, dict) else data
            copies = [t for t in items if "(副本)" in (t.get("title") or "")]
            # toast appears
            page.wait_for_selector("#toast .toast", timeout=15000)
            toast_text = page.locator("#toast .toast").inner_text(timeout=5000)
            assert "已批量复制" in toast_text and "3" in toast_text, f"toast text unexpected: {toast_text}"

            # after refresh, list should contain 3 copies
            page.wait_for_function(
                "document.querySelectorAll('.entity-list .entity-item').length >= 6", timeout=15000
            )
            rows_after = page.locator(".entity-list .entity-item").count()
            assert rows_after == 6, f"expected 6 rows after duplicate, got {rows_after}"

            # API verify copies == 3
            st, data = api("GET", f"/api/stories/{sid}/tasks?limit=200", token=token)
            items = data["items"] if isinstance(data, dict) else data
            copies = [t for t in items if "(副本)" in (t.get("title") or "")]
            assert len(copies) == 3, f"expected 3 copies via API, got {len(copies)}"

            browser.close()

        print(f"[verify] rows_after={rows_after} copies={len(copies)} toast='{toast_text}'")
        print(f"[errors] pageerror={len(errors)} console_err={len(console_errors)} asset_fail={len(failed_assets)}")
        assert len(errors) == 0, f"page errors: {errors}"
        assert len(console_errors) == 0, f"console errors: {console_errors}"
        assert len(failed_assets) == 0, f"failed assets: {failed_assets}"
        print("E2E PASS")
    finally:
        # ---- cleanup: delete all tasks in story then story ----
        st, data = api("GET", f"/api/stories/{sid}/tasks?limit=200", token=token)
        items = data["items"] if isinstance(data, dict) else data
        for t in items:
            api("DELETE", f"/api/tasks/{t['id']}", token=token)
        api("DELETE", f"/api/stories/{sid}", token=token)
        print(f"[cleanup] removed story={sid} and {len(items)} tasks")


if __name__ == "__main__":
    main()
