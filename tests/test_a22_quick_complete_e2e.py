"""
A-22: 任务「快速完成」勾选（列表 + 看板）- Playwright E2E 验证
验证项：
1. 登录 admin 用户
2. 导航到 项目(AgentBoard/AGB) -> Epic 11 -> Story 12.1（含任务列表）
3. 列表项出现 .task-quick-complete 勾选按钮
4. 点击 -> 经 REST API 确认任务状态翻转（done<->非 done），再次点击 -> 翻转回
5. 切换到看板视图，卡片出现 .kanban-qc 勾选按钮，点击 -> 经 API 确认状态翻转
6. 零 JS 报错 / 零 console error / 零 failed request
"""
import asyncio
import json
import sys
import urllib.request
import urllib.error

API = "http://127.0.0.1:58125"
WEB = "http://127.0.0.1:8080"


async def poll(page, selector, timeout=20000):
    step = 400
    for _ in range(timeout // step + 1):
        if await page.locator(selector).count() > 0:
            return True
        await page.wait_for_timeout(step)
    return False


def api_get_task(tid, token):
    req = urllib.request.Request(f"{API}/api/tasks/{tid}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode()).get("status")
    except Exception as e:
        return f"ERR:{e}"


async def main():
    from playwright.async_api import async_playwright

    errors, page_errors, failed_requests = [], [], []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}]") if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_requests.append(f"{r.url} - {r.failure}"))

        # Step 1: login
        await page.goto(WEB, wait_until="networkidle")
        await page.wait_for_timeout(2500)
        tab = page.locator(".auth-tab:has-text('登录')")
        if await tab.count() > 0:
            await tab.click()
            await page.wait_for_timeout(400)
        await page.locator("input[name='username']").fill("admin")
        await page.locator("input[name='password']").fill("admin123")
        await page.locator(".login-submit").click()
        await page.wait_for_timeout(4000)
        token = await page.evaluate("localStorage.getItem('agentboard_token')")
        assert token, "FAIL: login failed"
        print("PASS: logged in as admin")

        # Step 2: click-through navigation
        assert await poll(page, ".project-card"), "FAIL: project list not rendered"
        await page.locator("xpath=//a[contains(@class,'project-card')][contains(.,'AGB')]").first.click()
        assert await poll(page, "a[href*='epic']"), "FAIL: epic list not rendered"
        await page.wait_for_timeout(800)
        epic = page.locator("xpath=//a[contains(@class,'entity-item')][contains(.,'Epic 11')]").first
        if await epic.count() == 0:
            epic = page.locator("a[href*='epic']").first
        await epic.click()
        assert await poll(page, "a[href*='story']"), "FAIL: story list not rendered"
        await page.wait_for_timeout(800)
        story = page.locator("xpath=//a[contains(@class,'entity-item')][contains(.,'Story 12.1')]").first
        if await story.count() == 0:
            story = page.locator("a[href*='story']").first
        await story.click()
        await page.wait_for_timeout(2500)

        # ensure list view (default is list; switch if board shown)
        if await page.locator(".kanban").count() > 0:
            lb = page.locator("xpath=//button[contains(.,'列表')]").first
            if await lb.count() > 0:
                await lb.click()
                await page.wait_for_timeout(1000)

        assert await poll(page, ".task-quick-complete", 20000), "FAIL: no .task-quick-complete in list"
        print(f"list .task-quick-complete count = {await page.locator('.task-quick-complete').count()}")

        async def first_task_id():
            return await page.evaluate(
                "() => { const a=document.querySelector('.entity-item .entity-item-link'); "
                "if(!a) return null; const m=a.getAttribute('href').match(/\\/task\\/(\\d+)/); return m?m[1]:null; }"
            )

        # ---- LIST toggle (verified via API) ----
        tid = await first_task_id()
        assert tid, "FAIL: cannot resolve task id from list"
        tid = int(tid)
        before = api_get_task(tid, token)
        print(f"list task {tid} status before = {before}")
        assert before not in (None, "ERR:"), f"FAIL: cannot read status ({before})"
        list_btn = page.locator(
            f".entity-item--rich:has(a.entity-item-link[href*='/task/{tid}']) .task-quick-complete"
        )
        await list_btn.first.click()
        await page.wait_for_timeout(2500)
        after1 = api_get_task(tid, token)
        print(f"list task {tid} status after1 = {after1}")
        assert (before == "done") != (after1 == "done"), f"FAIL: first toggle should flip done-ness ({before}->{after1})"
        # toggle back (reopen -> 非 done 状态，如 todo)。列表重排后需用稳定定位器重定位同一任务行
        await list_btn.first.click()
        await page.wait_for_timeout(2500)
        after2 = api_get_task(tid, token)
        print(f"list task {tid} status after2 = {after2}")
        assert (after2 == "done") == (before == "done"), f"FAIL: second toggle should restore done-ness ({before}->{after1}->{after2})"
        print("PASS: list quick-complete toggles done<->reopen (API-verified)")

        # ---- KANBAN toggle (verified via API) ----
        board = page.locator("xpath=//button[contains(.,'看板')]").first
        await board.wait_for(state="visible", timeout=10000)
        await board.click()
        assert await poll(page, ".kanban", 10000), "FAIL: kanban view did not render"
        assert await poll(page, ".kanban-qc", 20000), "FAIL: no .kanban-qc in kanban"
        print(f"kanban .kanban-qc count = {await page.locator('.kanban-qc').count()}")
        # 目标 823 的看板卡片（列表 reopen 后 823 处于 todo，看板点击应重新标记完成 -> done）
        kanban_btn = page.locator(f".kanban-card:has(a[href*='/task/{tid}']) .kanban-qc")
        if await kanban_btn.count() == 0:
            kanban_btn = page.locator(".kanban-qc")
        kbefore = api_get_task(tid, token)
        print(f"kanban task {tid} status before = {kbefore}")
        await kanban_btn.first.click()
        await page.wait_for_timeout(2500)
        kafter = api_get_task(tid, token)
        print(f"kanban task {tid} status after = {kafter}")
        assert kafter != kbefore, f"FAIL: kanban toggle did not change status ({kbefore}->{kafter})"
        print("PASS: kanban quick-complete toggles (API-verified)")

        # ---- error checks ----
        # 排除良性噪声：favicon 请求、以及 Angular HttpClient 在 firstValueFrom 取首值后
        # 取消订阅导致的 net::ERR_ABORTED（请求已成功，状态已变更，见上方 API 校验）。
        real_failed = [
            f for f in failed_requests
            if "favicon" not in f and "ERR_ABORTED" not in f
        ]
        print(f"page_errors={len(page_errors)} console_errors={len(errors)} failed_requests={len(real_failed)}")
        for e in page_errors:
            print("  PAGEERROR:", e)
        for e in errors:
            print("  CONSOLE:", e)
        assert not page_errors, "FAIL: page errors present"
        assert not errors, "FAIL: console errors present"
        assert not real_failed, f"FAIL: failed requests: {real_failed}"

        await browser.close()
        print("ALL_PASS")
        return True


if __name__ == "__main__":
    try:
        ok = asyncio.run(main())
        sys.exit(0 if ok else 1)
    except AssertionError as ae:
        print(str(ae))
        sys.exit(1)
    except Exception as ex:
        print("EXCEPTION:", repr(ex))
        sys.exit(1)
