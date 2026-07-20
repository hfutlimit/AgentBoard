"""
Epic 33 v2.2: 任务列表「只看指派给我」快速筛选 - Playwright E2E 验证
说明：应用存在已知竞态——loadDashboard() 会把全量 tasks 注入 tasks()，
导致 story 视图的 tasks() 也含全项目任务（不止本 story）。因此本测试
采用「与竞态无关」的断言：统计可见行中「指派给当前用户」的数量，
验证点击「只看我」后列表恰好收敛到该子集，且不依赖 tasks() 的范围。

确定性场景（自建，验证后清理）：
- project 6 (Chess-2) 中 admin(id=18) 是成员 → myUserId() 可解析（守卫通过）
- story 69 含 3 个任务：T1 指派给 admin；T2 未指派；T3 指派给其它用户

验证项：
1. 登录 admin
2. 打开 /story/69，统计「指派给我」的行数 = M（≥1）
3. .mine-toggle 渲染、初始非 active
4. 点击「只看我」→ 行数 == M、按钮 active、每个可见行 assignee == 当前用户
5. 刷新 → 持久化（仍 active、行数仍为 M）
6. 再次点击 → 还原全部（行数回 total_before）
7. 零 JS 报错 / 零 .js+.css 404 / 零 console error
"""
import asyncio
import sys

STORY_ID = 69  # project 6 下自建 story；验证后清理


async def _count_my_rows(page, username: str) -> int:
    rows = page.locator(".entity-item--rich")
    n = await rows.count()
    c = 0
    for i in range(n):
        av = rows.nth(i).locator(".assignee-avatar-sm")
        if await av.count() == 0:
            continue
        title = (await av.first.get_attribute("title")) or ""
        cls = (await av.first.get_attribute("class")) or ""
        if title and ("未指派" not in title) and ("unassigned" not in cls) and title == username:
            c += 1
    return c


async def main() -> bool:
    from playwright.async_api import async_playwright

    WEB_URL = "http://localhost:8080"

    errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []

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
        token = await page.evaluate("localStorage.getItem('agentboard_token')")
        if not token:
            print("FAIL: Login failed")
            await browser.close()
            return False
        username = (await page.evaluate("localStorage.getItem('agentboard_user')")) or "admin"
        print(f"PASS: Logged in as '{username}'")
        # 让 dashboard 预加载落定（无论范围，不影响本次基于 admin 行数的断言）
        await page.wait_for_timeout(6000)

        # Step2: open story 69 (list view)
        print(f"Step 2: Open /story/{STORY_ID}...")
        await page.goto(WEB_URL + f"/story/{STORY_ID}", wait_until="networkidle")
        await page.wait_for_timeout(3500)
        list_btn = page.locator("button:has-text('列表')")
        if await list_btn.count() > 0:
            await list_btn.first.click()
            await page.wait_for_timeout(1500)
        rows = page.locator(".entity-item--rich")
        total_before = await rows.count()
        print(f"  total visible rows: {total_before}")
        if total_before == 0:
            print("FAIL: 0 rows")
            await browser.close()
            return False
        my_before = await _count_my_rows(page, username)
        print(f"  rows assigned to me: {my_before} (expected >= 1)")
        if my_before < 1:
            print("FAIL: no row assigned to current user (filter would be a no-op)")
            await browser.close()
            return False

        # Step3: verify .mine-toggle rendered, not active
        print("Step 3: Verify .mine-toggle exists and inactive...")
        toggle = page.locator(".mine-toggle")
        if await toggle.count() == 0:
            print("FAIL: .mine-toggle not found")
            await browser.close()
            return False
        if "active" in ((await toggle.first.get_attribute("class")) or ""):
            print("FAIL: .mine-toggle should start inactive")
            await browser.close()
            return False
        print("PASS: .mine-toggle rendered, inactive")

        # Step4: click 只看我
        print("Step 4: Click 只看我 ...")
        await toggle.first.click()
        await page.wait_for_timeout(1300)
        if "active" not in ((await toggle.first.get_attribute("class")) or ""):
            print("FAIL: .mine-toggle not active after click")
            await browser.close()
            return False
        after = await rows.count()
        my_after = await _count_my_rows(page, username)
        print(f"  rows after filter: {after} (expected == assigned-to-me {my_before}); admin-assigned visible: {my_after}")
        if after != my_before:
            print(f"FAIL: filtered row count {after} != assigned-to-me {my_before}")
            await browser.close()
            return False
        if my_after != after:
            print(f"FAIL: not all visible rows are mine ({my_after} != {after})")
            await browser.close()
            return False
        # every visible row must be assigned to me
        ok = True
        for i in range(after):
            av = rows.nth(i).locator(".assignee-avatar-sm")
            title = (await av.first.get_attribute("title")) or ""
            cls = (await av.first.get_attribute("class")) or ""
            if ("未指派" in title) or ("unassigned" in cls) or (title != username):
                print(f"  FAIL: row {i} assignee title='{title}' class='{cls}' (expected '{username}')")
                ok = False
        if not ok:
            await browser.close()
            return False
        print("PASS: list shows only tasks assigned to me (count + per-row assignee verified)")

        # Step5: persistence across reload
        print("Step 5: Reload and verify persistence...")
        ls_before = await page.evaluate("localStorage.getItem('agentboard_filter_mine')")
        print(f"  localStorage before reload: {ls_before}")
        if ls_before != "1":
            print("FAIL: localStorage not persisted")
            await browser.close()
            return False
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(4000)
        list_btn2 = page.locator("button:has-text('列表')")
        if await list_btn2.count() > 0:
            await list_btn2.first.click()
            await page.wait_for_timeout(1500)
        toggle2 = page.locator(".mine-toggle")
        if "active" not in ((await toggle2.first.get_attribute("class")) or ""):
            print("FAIL: .mine-toggle not active after reload")
            await browser.close()
            return False
        after_reload = await page.locator(".entity-item--rich").count()
        my_reload = await _count_my_rows(page, username)
        print(f"  rows after reload: {after_reload} (expected {my_before}); admin-assigned: {my_reload}")
        if after_reload != my_before or my_reload != my_before:
            print(f"FAIL: list not correctly filtered after reload ({after_reload}/{my_reload} != {my_before})")
            await browser.close()
            return False
        print("PASS: selection persisted across reload")

        # Step6: click again to clear
        print("Step 6: Click again to clear filter...")
        await toggle2.first.click()
        await page.wait_for_timeout(1300)
        if "active" in ((await toggle2.first.get_attribute("class")) or ""):
            print("FAIL: .mine-toggle still active after second click")
            await browser.close()
            return False
        total_cleared = await page.locator(".entity-item--rich").count()
        print(f"  rows after clear: {total_cleared} (expected {total_before})")
        if total_cleared != total_before:
            print(f"FAIL: clearing filter did not restore all rows ({total_cleared} != {total_before})")
            await browser.close()
            return False
        print("PASS: toggling off restores all tasks")

        # Screenshot
        await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/epic33_v22_mine_filter.png", full_page=False)
        print("PASS: Screenshot saved")

        # Step7: Error summary
        print("\n=== Error Summary ===")
        print(f"  pageerror: {len(page_errors)}")
        print(f"  console errors: {len(errors)}")
        print(f"  failed requests: {len(failed_requests)}")
        for e in page_errors[:5]:
            print(f"    pageerror: {e}")
        for e in errors[:5]:
            print(f"    console: {e}")
        for r in failed_requests[:5]:
            print(f"    reqfail: {r}")

        await browser.close()
        ok = not page_errors and not errors and not failed_requests
        print("\n=== ALL PASS ===" if ok else "\n=== FAIL: non-zero errors ===")
        return ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
