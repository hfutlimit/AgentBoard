"""
E2E: 任务列表「批量修改优先级」(Epic 41 / Story / Task 1105, v2.9)

验证流程：
  - admin 登录（注入 localStorage.agentboard_token 避开登录页）
  - 进入 story 25，勾选若干任务
  - 批量操作栏出现 → 点「批量修改优先级」→ 点目标优先级按钮
  - 断言：选中的任务 priority 经 API 变为目标值；还原现场
  - 断言：0 pageerror / 0 console error / 0 .js+.css 404

用法：
  python tests/test_bulk_priority_e2e.py
"""
import json
import sys
import urllib.request
import urllib.error

WEB = "http://127.0.0.1:8080"
API = "http://127.0.0.1:58125"
STORY_ID = 25
TARGET_PRIORITY = "high"          # 目标优先级
TARGET_LABEL = "高"               # priorityLabel('high')
RESTORE = {}                       # {task_id: original_priority} 运行时填充

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

    # 2) 取 story 任务，挑 3 个 priority != 目标的作为目标
    data, err = api("GET", f"/api/stories/{STORY_ID}/tasks?limit=50", token=token)
    assert data, f"load tasks failed: {err}"
    items = data.get("items", data) if isinstance(data, dict) else data
    targets = [t for t in items if t["priority"] != TARGET_PRIORITY][:3]
    assert len(targets) >= 3, f"需要至少 3 个非 {TARGET_PRIORITY} 任务，仅 {len(targets)}"
    for t in targets:
        RESTORE[t["id"]] = t["priority"]
    print("[ok] 目标任务:", [(t["id"], t["priority"]) for t in targets])

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

            # 6) 点「批量修改优先级」
            page.locator(".bulk-action-bar button", has_text="批量修改优先级").click()
            page.wait_for_selector(".bulk-panel .priority--" + TARGET_PRIORITY, timeout=10000)
            print("[ok] 优先级选择面板出现")

            # 7) 点目标优先级按钮
            page.locator(".bulk-panel button.priority--" + TARGET_PRIORITY).click()

            # 8) 等待批量完成（toast / 选择清空）
            page.wait_for_function("document.querySelectorAll('.task-checkbox:checked').length === 0", timeout=15000)
            print("[ok] 批量操作完成，选择已清空")

            # 9) API 断言：目标任务 priority == 目标
            ok = True
            for t in targets:
                d, e = api("GET", f"/api/tasks/{t['id']}", token=token)
                got = d.get("priority") if d else None
                status = "OK" if got == TARGET_PRIORITY else "FAIL"
                if got != TARGET_PRIORITY:
                    ok = False
                print(f"  task {t['id']} priority={got} (期望 {TARGET_PRIORITY}) [{status}]")
            assert ok, "批量修改优先级未生效（API 校验失败）"

            # 10) 错误断言
            assert not pageerrors, f"pageerror: {pageerrors}"
            assert not console_errors, f"console errors: {console_errors}"
            assert not failed_res, f".js/.css 404/failed: {failed_res}"
            print("[ok] 0 pageerror / 0 console error / 0 .js+.css 404")

            browser.close()
    finally:
        # 11) 还原现场（不改真实数据）
        for tid, prio in RESTORE.items():
            api("PATCH", f"/api/tasks/{tid}", {"priority": prio}, token=token)
        print("[ok] 已还原", len(RESTORE), "个任务优先级")

    print("\n=== PASS: 批量修改优先级 E2E 全绿 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
