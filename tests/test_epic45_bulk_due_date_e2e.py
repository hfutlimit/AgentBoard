"""
E2E: 任务列表「批量改截止日期」(Epic 45 / v3.2)

验证流程：
  - admin 登录（注入 localStorage.agentboard_token 避开登录页）
  - 进入 story 25，勾选若干「无截止日期」任务
  - 批量操作栏出现 → 点「批量改截止日期」→ 日期输入框填值 → 点「应用」
  - 断言：选中任务 due_date 经 API 变为目标日期；再点「清除截止日期」恢复为 None
  - 断言：0 pageerror / 0 console error / 0 .js+.css 404
  - 还原：最终置为无截止日期（与初始一致），不改真实数据

用法：
  python tests/test_epic45_bulk_due_date_e2e.py
"""
import json
import os
import sys
import urllib.request
import urllib.error

WEB = os.environ.get("AGENTBOARD_TEST_WEB", "http://127.0.0.1:28080")
API = os.environ.get("AGENTBOARD_TEST_API", "http://127.0.0.1:18000")
STORY_ID = 25

H = {"Content-Type": "application/json"}


def api(method, path, body=None, token=None):
    headers = dict(H)
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, e.read().decode()[:300]


def main():
    from playwright.sync_api import sync_playwright

    # 1) 登录拿 token
    tok, err = api("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    assert tok, f"login failed: {err}"
    token = tok["token"]
    print("[ok] admin token len", len(token))

    # 2) 取 story 任务，挑 3 个 due_date 为 None 的作为目标
    data, err = api("GET", f"/api/stories/{STORY_ID}/tasks?limit=200", token=token)
    assert data, f"load tasks failed: {err}"
    items = data.get("items", data) if isinstance(data, dict) else data
    targets = [t for t in items if t.get("due_date") is None][:3]
    assert len(targets) >= 3, f"需要至少 3 个无截止日期任务，仅 {len(targets)}"
    print("[ok] 目标任务:", [(t["id"], t.get("due_date")) for t in targets])

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            pageerrors, console_errors, failed_res = [], [], []

            def on_pageerror(e): pageerrors.append(str(e))
            def on_console(m):
                if m.type == "error": console_errors.append(m.text)
            def on_reqfailed(r):
                u = r.url
                if u.endswith(".js") or u.endswith(".css"):
                    failed_res.append(f"{u} :: {r.failure}")
            page.on("pageerror", on_pageerror)
            page.on("console", on_console)
            page.on("requestfailed", on_reqfailed)

            # 3) 注入 token，跳过登录页
            page.add_init_script("localStorage.setItem('agentboard_token', " + json.dumps(token) + ");")

            # 4) 进入 story 页
            page.goto(f"{WEB}/story/{STORY_ID}", wait_until="domcontentloaded")
            page.wait_for_selector(".entity-item--rich", timeout=30000)
            print("[ok] story 页任务列表渲染")

            # 5) 勾选目标任务
            for t in targets:
                row = page.locator(f".entity-item--rich:has(a.entity-item-link[href='/task/{t['id']}'])")
                row.locator("input.task-checkbox").click()
            page.wait_for_selector(".bulk-action-bar", state="visible", timeout=10000)
            print("[ok] 批量操作栏出现（已选", len(targets), "）")

            # 6) 点「批量改截止日期」
            page.locator(".bulk-action-bar button", has_text="批量改截止日期").click()
            page.wait_for_selector(".bulk-panel input.bulk-date-input", timeout=10000)
            print("[ok] 截止日期设置面板出现")

            # 7) 填日期并应用
            TARGET = "2026-08-01"
            page.locator(".bulk-date-input").fill(TARGET)
            page.locator(".bulk-panel button", has_text="应用").click()

            # 8) 等待批量完成（选择清空）
            page.wait_for_function("document.querySelectorAll('.task-checkbox:checked').length === 0", timeout=15000)
            print("[ok] 批量改截止日期完成，选择已清空")

            # 9) API 断言：目标任务 due_date == TARGET
            ok = True
            for t in targets:
                d, e = api("GET", f"/api/tasks/{t['id']}", token=token)
                got = d.get("due_date") if d else None
                status = "OK" if got == TARGET else "FAIL"
                if got != TARGET:
                    ok = False
                print(f"  task {t['id']} due_date={got} (期望 {TARGET}) [{status}]")
            assert ok, "批量改截止日期未生效（API 校验失败）"

            # 10) 再次勾选 → 批量改截止日期 → 清除截止日期
            for t in targets:
                row = page.locator(f".entity-item--rich:has(a.entity-item-link[href='/task/{t['id']}'])")
                row.locator("input.task-checkbox").click()
            page.wait_for_selector(".bulk-action-bar", state="visible", timeout=10000)
            page.locator(".bulk-action-bar button", has_text="批量改截止日期").click()
            page.wait_for_selector(".bulk-panel input.bulk-date-input", timeout=10000)
            page.locator(".bulk-panel button", has_text="清除截止日期").click()
            page.wait_for_function("document.querySelectorAll('.task-checkbox:checked').length === 0", timeout=15000)

            # 11) API 断言：恢复为 None
            ok2 = True
            for t in targets:
                d, e = api("GET", f"/api/tasks/{t['id']}", token=token)
                got = d.get("due_date") if d else None
                if got is not None:
                    ok2 = False
                print(f"  task {t['id']} due_date={got} (期望 None) [{'OK' if got is None else 'FAIL'}])")
            assert ok2, "批量清除截止日期未生效（API 校验失败）"

            # 12) 错误断言
            assert not pageerrors, f"pageerror: {pageerrors}"
            assert not console_errors, f"console errors: {console_errors}"
            assert not failed_res, f".js/.css 404/failed: {failed_res}"
            print("[ok] 0 pageerror / 0 console error / 0 .js+.css 404")

            browser.close()
    finally:
        # 13) 还原现场（确保全部回到无截止日期，与初始一致）
        for t in targets:
            api("POST", "/api/tasks/bulk-update", {"task_ids": [t["id"]], "clear_due_date": True}, token=token)
        print("[ok] 已还原", len(targets), "个任务为无截止日期")

    print("\n=== PASS: 批量改截止日期 E2E 全绿 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
