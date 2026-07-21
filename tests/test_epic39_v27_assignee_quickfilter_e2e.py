"""
Epic 39 v2.7: 任务列表指派人快速筛选 chips - Playwright E2E 验证
验证项：
1. 登录 admin 用户
2. 自建隔离项目/Story，并注入：2 条指派人=admin 的任务 + 1 条未指派任务（用于可追踪数据）
3. 打开任意 Story 列表视图（任务列表 tasks() 为全局/项目级，本测试只验证筛选行为一致性）
4. 指派人快速筛选 bar（aria-label=按指派人快速筛选）渲染：全部 + 各指派人 chip（头像+计数）+ 未指派 chip
5. 点击 admin 指派人 chip → 列表行数 == 该 chip 计数（仅含该指派人的任务）；chip active 正确
6. 点击「全部」→ 清除筛选、列表恢复；localStorage 清空
7. 刷新页面 → 选择持久化（点 admin chip 后 reload，列表仍已筛、localStorage.agentboard_quick_assignee 保留 admin id）
8. 零 JS 报错 / 零 .js+.css 404 / 零 console error

测试使用独立隔离项目（运行结束删除），不污染人类项目数据。
"""
import asyncio
import json
import sys
import time
import urllib.request

API = "http://127.0.0.1:18000"
WEB_URL = "http://localhost:28080"  # 本地 web 直读 agentboard/web/static，无需 rebuild
ASSIGNEE_BAR = '.task-quickfilter-bar[aria-label="按指派人快速筛选"]'


def _api(method, path, token, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def setup_isolated_data(admin_id, admin_name, token):
    """创建隔离项目/Story/任务，返回 (story_id, project_id)."""
    ts = int(time.time())
    proj = _api("POST", "/api/projects", token, {
        "name": f"E2E v2.7 指派人筛选 {ts}", "key": f"E{ts % 100000}",
        "description": "isolated e2e data",
    })
    pid = proj["id"]
    epic = _api("POST", f"/api/projects/{pid}/epics", token, {
        "title": f"v2.7 e2e epic {ts}", "description": "isolated",
    })
    story = _api("POST", f"/api/epics/{epic['id']}/stories", token, {
        "title": f"v2.7 e2e story {ts}", "description": "isolated",
    })
    sid = story["id"]
    for i in range(1, 3):
        _api("POST", f"/api/stories/{sid}/tasks", token, {
            "project_id": pid, "title": f"[e2e] 指派人任务 {i}-{ts}",
            "type": "task", "priority": "medium", "assignee_id": admin_id,
        })
    _api("POST", f"/api/stories/{sid}/tasks", token, {
        "project_id": pid, "title": f"[e2e] 未指派任务 {ts}",
        "type": "task", "priority": "low",
    })
    print(f"  setup: project={pid} story={sid} (2 admin + 1 unassigned injected)")
    return sid, pid


async def main() -> bool:
    from playwright.async_api import async_playwright

    errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []

    # ---- REST setup: login + isolated data ----
    print("Setup: login + create isolated e2e data...")
    me = _api("POST", "/api/auth/login", None, {"username": "admin", "password": "admin123"})
    token = me["token"]
    admin_id = me["id"]
    admin_name = me.get("username") or me.get("name") or "admin"
    story_id, e2e_pid = setup_isolated_data(admin_id, admin_name, token)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1400, "height": 900})
            page = await context.new_page()

            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.on("console", lambda msg: errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
            page.on("requestfailed", lambda req: failed_requests.append(f"{req.url} - {req.failure}") if req.url.endswith(('.js', '.css')) else None)

            # Step1: Login
            print("Step 1: Login as admin...")
            await page.goto(WEB_URL + "/", wait_until="networkidle")
            await page.wait_for_timeout(2000)
            if await page.locator(".auth-tab").count() > 0:
                login_tab = page.locator(".auth-tab", has_text="登录")
                if await login_tab.count() > 0:
                    await login_tab.first.click()
                    await page.wait_for_timeout(500)
                await page.fill('input[name="username"]', "admin")
                await page.fill('input[name="password"]', "admin123")
                await page.click(".login-submit")
                await page.wait_for_timeout(3000)
            tk = await page.evaluate("localStorage.getItem('agentboard_token')")
            if not tk:
                print("FAIL: Login failed")
                await browser.close()
                return False
            print("PASS: Logged in as admin")

            # Step2: Open story (list view)
            print(f"Step 2: Open story {story_id} (list view)...")
            await page.goto(WEB_URL + f"/story/{story_id}", wait_until="networkidle")
            await page.wait_for_timeout(3500)
            list_btn = page.locator("button:has-text('列表')")
            if await list_btn.count() > 0:
                await list_btn.first.click()
                await page.wait_for_timeout(1500)

            row_count = await page.locator(".entity-item--rich").count()
            print(f"  task rows in list: {row_count}")
            if row_count == 0:
                print("FAIL: No task rows")
                await browser.close()
                return False

            # Step3: assignee quick-filter bar + chips
            print("Step 3: Verify assignee quick-filter bar + chips...")
            bar = page.locator(ASSIGNEE_BAR)
            if await bar.count() == 0:
                print("FAIL: assignee quick-filter bar not found")
                await browser.close()
                return False
            chips = bar.locator(".qf-chip")
            chip_count = await chips.count()
            print(f"  assignee .qf-chip count: {chip_count}")
            if chip_count < 3:
                print("FAIL: expected >=3 assignee qf-chip (全部 + 指派人 + 未指派)")
                await browser.close()
                return False
            all_chip = bar.locator(".qf-chip", has_text="全部")
            admin_chip = bar.locator(".qf-chip", has_text=admin_name).first
            unassigned_chip = bar.locator(".qf-chip", has_text="未指派").first
            all_count = (await all_chip.locator(".qf-count").text_content() or "").strip()
            if all_count != str(row_count):
                print(f"FAIL: 全部 chip count {all_count} != {row_count}")
                await browser.close()
                return False
            print("PASS: assignee bar + 全部 count correct")

            admin_count_txt = (await admin_chip.locator(".qf-count").text_content() or "").strip()
            unassigned_count_txt = (await unassigned_chip.locator(".qf-count").text_content() or "").strip()
            print(f"  admin('{admin_name}') count={admin_count_txt}, 未指派 count={unassigned_count_txt}")
            if not admin_count_txt.isdigit() or int(admin_count_txt) < 1:
                print("FAIL: admin chip count invalid")
                await browser.close()
                return False

            # Step4: click admin chip -> only admin tasks (rows == admin count)
            print("Step 4: Click admin assignee chip...")
            await admin_chip.click()
            await page.wait_for_timeout(1200)
            if "active" not in (await admin_chip.get_attribute("class") or ""):
                print("FAIL: admin chip not active after click")
                await browser.close()
                return False
            rows_admin = await page.locator(".entity-item--rich").count()
            print(f"  rows after admin click: {rows_admin} (expected {admin_count_txt})")
            if rows_admin != int(admin_count_txt):
                print("FAIL: admin-filtered row count mismatch (feature not filtering correctly)")
                await browser.close()
                return False
            print("PASS: admin filter works (rows == chip count)")

            # Step5: click 全部 -> reset
            print("Step 5: Click 全部 to reset...")
            await all_chip.click()
            await page.wait_for_timeout(1200)
            rows_reset = await page.locator(".entity-item--rich").count()
            if rows_reset != row_count:
                print("FAIL: reset row count mismatch")
                await browser.close()
                return False
            ls = await page.evaluate("localStorage.getItem('agentboard_quick_assignee')")
            if ls not in (None, "[]", ""):
                print(f"FAIL: localStorage not cleared after reset: {ls}")
                await browser.close()
                return False
            print("PASS: reset works, localStorage cleared")

            # Step6: persistence - select admin, reload
            print("Step 6: Select admin chip, reload, verify persistence...")
            await admin_chip.click()
            await page.wait_for_timeout(1000)
            ls_before = await page.evaluate("localStorage.getItem('agentboard_quick_assignee')")
            print(f"  localStorage before reload: {ls_before}")
            if str(admin_id) not in (ls_before or ""):
                print(f"FAIL: localStorage not persisted admin id: {ls_before}")
                await browser.close()
                return False
            await page.reload(wait_until="networkidle")
            await page.wait_for_timeout(3500)
            list_btn2 = page.locator("button:has-text('列表')")
            if await list_btn2.count() > 0:
                await list_btn2.first.click()
                await page.wait_for_timeout(1200)
            rows_persist = await page.locator(".entity-item--rich").count()
            persist_exp = int(admin_count_txt)
            print(f"  rows after reload: {rows_persist} (expected {persist_exp})")
            if rows_persist != persist_exp:
                print("FAIL: persistence broken after reload")
                await browser.close()
                return False
            admin_still_active = "active" in (await admin_chip.get_attribute("class") or "")
            print(f"  admin chip still active after reload: {admin_still_active}")
            if not admin_still_active:
                print("FAIL: admin chip not active after reload")
                await browser.close()
                return False
            print("PASS: persistence works")

            # Step7: error checks
            print("Step 7: Error checks...")
            real_failed = [f for f in failed_requests if ".js" in f or ".css" in f]
            if page_errors:
                print("FAIL: page errors:", page_errors[:5])
                await browser.close()
                return False
            if errors:
                print("FAIL: console errors:", errors[:5])
                await browser.close()
                return False
            if real_failed:
                print("FAIL: failed js/css requests:", real_failed[:5])
                await browser.close()
                return False
            print("PASS: zero page/console/404 errors")

            await browser.close()
            print("ALL CHECKS PASSED")
            return True
    finally:
        # cleanup isolated e2e project (keep tracking project 99)
        try:
            _api("DELETE", f"/api/projects/{e2e_pid}", token)
            print(f"  cleaned up e2e project {e2e_pid}")
        except Exception as e:
            print(f"  (warn) cleanup e2e project failed: {e}")


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
