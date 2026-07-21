"""
Epic 39 v2.6: 任务列表「按状态」排序 - Playwright E2E 验证
验证项：
1. 登录 admin 用户
2. 打开含任务的 Story（列表视图）
3. 排序下拉（.task-sort-select）含「状态」选项（value=status）
4. 选择「状态」→ 任务列表按状态工作流顺序排序（默认降序：done 在前、backlog 在后，序列单调不增）
5. 点击方向按钮（.task-sort-dir-btn）→ 切换为升序（backlog 在前、done 在后，序列单调不减）
6. 刷新页面 → 选择持久化（排序下拉仍选中「状态」、localStorage.agentboard_sort_key=='status'、列表仍按状态有序）
7. 恢复默认（创建时间）避免污染人类用户默认偏好
8. 零 JS 报错 / 零 .js+.css 404 / 零 console error
"""
import asyncio
import re
import sys

STATUS_ORDER = ['backlog', 'todo', 'in_progress', 'in_review', 'verifying', 'done']

STORY_ID = 25  # 历史数据丰富、含多状态任务


async def read_status_sequence(page) -> list[str]:
    rows = page.locator(".entity-item--rich")
    n = await rows.count()
    seq: list[str] = []
    for i in range(n):
        cls = await rows.nth(i).locator(".badge.status").get_attribute("class") or ""
        m = re.search(r"status--([a-z_]+)", cls)
        seq.append(m.group(1) if m else "")
    return seq


def is_non_increasing(seq: list[str]) -> bool:
    idx = [STATUS_ORDER.index(s) if s in STATUS_ORDER else -1 for s in seq]
    return all(idx[i] >= idx[i + 1] for i in range(len(idx) - 1))


def is_non_decreasing(seq: list[str]) -> bool:
    idx = [STATUS_ORDER.index(s) if s in STATUS_ORDER else 99 for s in seq]
    return all(idx[i] <= idx[i + 1] for i in range(len(idx) - 1))


async def main() -> bool:
    from playwright.async_api import async_playwright

    WEB_URL = "http://localhost:28080"  # 本地 web 直读 agentboard/web/static，无需 rebuild

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
        print("PASS: Logged in as admin")

        # Step2: Open story 25 (list view)
        print(f"Step 2: Open story {STORY_ID} (list view)...")
        await page.goto(WEB_URL + f"/story/{STORY_ID}", wait_until="networkidle")
        await page.wait_for_timeout(3500)
        list_btn = page.locator("button:has-text('列表')")
        if await list_btn.count() > 0:
            await list_btn.first.click()
            await page.wait_for_timeout(1500)

        row_count = await page.locator(".entity-item--rich").count()
        print(f"  task rows on story {STORY_ID}: {row_count}")
        if row_count == 0:
            print("FAIL: No task rows")
            await browser.close()
            return False

        # Baseline status order (default created_at desc) — should NOT be status-sorted
        base_seq = await read_status_sequence(page)
        base_sorted = is_non_increasing(base_seq) or is_non_decreasing(base_seq)
        print(f"  baseline status-sorted? {base_sorted} (acceptable either way)")

        # Step3: Sort select contains 状态 option
        print("Step 3: Verify sort select has 状态 option...")
        sort_select = page.locator("select.task-sort-select")
        if await sort_select.count() == 0:
            print("FAIL: sort select not found")
            await browser.close()
            return False
        opt_count = await sort_select.locator("option").count()
        labels = [await sort_select.locator("option").nth(i).text_content() for i in range(opt_count)]
        print(f"  sort options: {labels}")
        if not any("状态" in (l or "") for l in labels):
            print("FAIL: 状态 option missing in sort select")
            await browser.close()
            return False
        print("PASS: 状态 option present")

        # Step4: Select 状态 → list sorted by status (desc default)
        print("Step 4: Select 状态 (desc)...")
        await sort_select.select_option(value="status")
        await page.wait_for_timeout(1200)
        ls_key = await page.evaluate("localStorage.getItem('agentboard_sort_key')")
        print(f"  localStorage.agentboard_sort_key = {ls_key}")
        if ls_key != "status":
            print("FAIL: sort key not persisted as 'status'")
            await browser.close()
            return False
        desc_seq = await read_status_sequence(page)
        desc_n = len(desc_seq)
        desc_ok = is_non_increasing(desc_seq)
        print(f"  status sequence (first 12): {desc_seq[:12]}")
        print(f"  non-increasing (desc)? {desc_ok}  (n={desc_n})")
        if not desc_ok:
            print("FAIL: list not sorted by status (desc)")
            await browser.close()
            return False
        print("PASS: list sorted by status (desc)")

        # Step5: Toggle direction → asc
        print("Step 5: Toggle direction to asc...")
        dir_btn = page.locator("button.task-sort-dir-btn")
        if await dir_btn.count() == 0:
            print("FAIL: direction button not found")
            await browser.close()
            return False
        await dir_btn.first.click()
        await page.wait_for_timeout(1200)
        ls_order = await page.evaluate("localStorage.getItem('agentboard_sort_order')")
        print(f"  localStorage.agentboard_sort_order = {ls_order}")
        if ls_order != "asc":
            print("FAIL: sort order not persisted as 'asc'")
            await browser.close()
            return False
        asc_seq = await read_status_sequence(page)
        asc_ok = is_non_decreasing(asc_seq)
        print(f"  status sequence (first 12): {asc_seq[:12]}")
        print(f"  non-decreasing (asc)? {asc_ok}")
        if not asc_ok:
            print("FAIL: list not sorted by status (asc) after toggle")
            await browser.close()
            return False
        print("PASS: list sorted by status (asc) after toggle")

        # Step6: Persistence across reload
        print("Step 6: Reload and verify persistence...")
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(3500)
        sort_select2 = page.locator("select.task-sort-select")
        val_after = await sort_select2.input_value()
        ls_key2 = await page.evaluate("localStorage.getItem('agentboard_sort_key')")
        opts2 = [await sort_select2.locator("option").nth(i).text_content() for i in range(await sort_select2.locator("option").count())]
        print(f"  sort select value after reload: {val_after}")
        print(f"  localStorage.agentboard_sort_key after reload: {ls_key2}")
        print(f"  options after reload: {opts2}")
        if val_after != "status":
            print("FAIL: sort select not restored to 状态 after reload")
            await browser.close()
            return False
        ls_key2 = await page.evaluate("localStorage.getItem('agentboard_sort_key')")
        if ls_key2 != "status":
            print("FAIL: localStorage sort key lost after reload")
            await browser.close()
            return False
        reload_seq = await read_status_sequence(page)
        reload_ok = is_non_decreasing(reload_seq)
        print(f"  status sequence after reload (first 12): {reload_seq[:12]}")
        print(f"  non-decreasing after reload? {reload_ok}")
        if not reload_ok:
            print("FAIL: list not status-sorted after reload")
            await browser.close()
            return False
        print("PASS: sort persisted across reload")

        # Step7: Restore default sort to avoid polluting human default preference
        print("Step 7: Restore default sort (创建时间)...")
        await sort_select2.select_option(value="created_at")
        await page.wait_for_timeout(800)
        ls_key3 = await page.evaluate("localStorage.getItem('agentboard_sort_key')")
        if ls_key3 != "created_at":
            print("WARN: could not restore default sort key")
        else:
            print("PASS: default sort restored")

        await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/epic39_v26_status_sort.png", full_page=False)
        print("PASS: Screenshot saved")

        # Step8: Error summary
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
