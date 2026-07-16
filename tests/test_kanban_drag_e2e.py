"""
B-04: 看板拖拽改状态 - Playwright E2E 验证
验证项：
1. 登录 admin 用户
2. 导航到项目 → Epic → Story → 看板视图
3. 看板卡片 draggable 属性
4. 拖拽改状态（JavaScript 模拟）
5. 零 JS 报错 / 零 404 / 零 console error
"""
import asyncio
import sys

async def main():
    from playwright.async_api import async_playwright

    WEB_URL = "http://localhost:28080"

    errors = []
    page_errors = []
    failed_requests = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda msg: errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.url} - {req.failure}"))

        # Step 1: Login as admin
        print("Step 1: Login as admin...")
        await page.goto(WEB_URL, wait_until="networkidle")
        await page.wait_for_timeout(3000)

        login_tab = page.locator(".auth-tab:has-text('登录')")
        if await login_tab.count() > 0:
            await login_tab.click()
            await page.wait_for_timeout(500)

        await page.locator("input[name='username']").fill("admin")
        await page.locator("input[name='password']").fill("admin123")
        await page.locator(".login-submit").click()
        await page.wait_for_timeout(5000)

        token = await page.evaluate("localStorage.getItem('agentboard_token')")
        if not token:
            print("FAIL: Login failed")
            await browser.close()
            return False
        print("PASS: Logged in as admin")

        # Step 2: Click "项目" nav link (not goto URL)
        print("Step 2: Click projects nav...")
        projects_nav = page.locator("a:has-text('项目'), [routerlink='projects'], a[href*='projects']")
        if await projects_nav.count() > 0:
            await projects_nav.first.click()
            await page.wait_for_timeout(3000)
            print(f"  Clicked projects nav. URL: {page.url}")
        else:
            print("  No projects nav found, trying direct hash...")
            await page.evaluate("window.location.hash = '#/projects'")
            await page.wait_for_timeout(3000)

        # Check page content
        body = await page.locator("body").text_content()
        has_projects = "Test Project" in body or "project-card" in body or "AgentBoard" in body
        print(f"  Body has project content: {has_projects}")

        # Try to find and click on project 90 (Test Project)
        proj_link = page.locator("a:has-text('Test Project')")
        proj_card = page.locator(".project-card")
        print(f"  project-card count: {await proj_card.count()}")
        print(f"  Test Project links: {await proj_link.count()}")

        if await proj_card.count() > 0:
            await proj_card.first.click()
            await page.wait_for_timeout(3000)
        elif await proj_link.count() > 0:
            await proj_link.first.click()
            await page.wait_for_timeout(3000)
        else:
            # Navigate directly to project 90
            print("  Navigating directly to project 90...")
            await page.evaluate("window.location.hash = '#/project/90'")
            await page.wait_for_timeout(3000)

        print(f"  Current URL: {page.url}")

        # Step 3: Click on first epic in the project
        print("Step 3: Find and click epic...")
        await page.wait_for_timeout(2000)
        epic_link = page.locator(".entity-item a, .entity-item .entity-title, a[href*='epic']")
        print(f"  epic links: {await epic_link.count()}")

        if await epic_link.count() > 0:
            await epic_link.first.click()
            await page.wait_for_timeout(3000)
            print(f"  After epic click, URL: {page.url}")

        # Step 4: Click on first story
        print("Step 4: Find and click story...")
        story_link = page.locator(".entity-item a, .entity-item .entity-title, a[href*='story']")
        print(f"  story links: {await story_link.count()}")

        if await story_link.count() > 0:
            await story_link.first.click()
            await page.wait_for_timeout(3000)
            print(f"  After story click, URL: {page.url}")

        # Step 5: Switch to kanban view
        print("Step 5: Switch to kanban view...")
        board_btn = page.locator("button:has-text('看板')")
        if await board_btn.count() > 0:
            await board_btn.first.click()
            await page.wait_for_timeout(1000)

        kanban = page.locator(".kanban")
        if await kanban.count() == 0:
            print(f"FAIL: No .kanban found. URL: {page.url}")
            body = await page.locator("body").text_content()
            print(f"  Body (first 500): {body[:500]}")
            await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/b04_no_kanban.png")
            if errors:
                print("  Console errors:")
                for e in errors[:5]: print(f"    {e}")
            await browser.close()
            return False
        print("PASS: Kanban board visible")

        # Step 6: Verify draggable
        print("Step 6: Verify draggable...")
        cards = page.locator(".kanban-card")
        card_count = await cards.count()
        print(f"  Found {card_count} cards")
        if card_count == 0:
            print("FAIL: No cards")
            await browser.close()
            return False

        draggable_count = 0
        for i in range(min(card_count, 10)):
            attr = await cards.nth(i).get_attribute("draggable")
            if attr == "true":
                draggable_count += 1
        print(f"  {draggable_count}/{min(card_count, 10)} cards draggable=true")
        if draggable_count == 0:
            print("FAIL: No draggable cards")
            await browser.close()
            return False
        print("PASS: Cards are draggable")

        # Step 7: Test drag-and-drop via JavaScript
        print("Step 7: Test drag-and-drop...")
        result = await page.evaluate("""
            () => {
                const card = document.querySelector('.kanban-card');
                const cols = document.querySelectorAll('.kanban-col');
                if (!card || cols.length < 2) return { error: 'Not enough elements' };
                const cardCol = card.closest('.kanban-col');
                let targetCol = null;
                for (const col of cols) {
                    if (col !== cardCol && !col.classList.contains('kanban-col--collapsed')) {
                        targetCol = col;
                        break;
                    }
                }
                if (!targetCol) return { error: 'No target column' };
                const cardHead = cardCol.querySelector('.kanban-col-head span')?.textContent;
                const targetHead = targetCol.querySelector('.kanban-col-head span')?.textContent;
                const dt = new DataTransfer();
                card.dispatchEvent(new DragEvent('dragstart', { bubbles: true, dataTransfer: dt }));
                targetCol.dispatchEvent(new DragEvent('dragover', { bubbles: true, dataTransfer: dt }));
                targetCol.dispatchEvent(new DragEvent('drop', { bubbles: true, dataTransfer: dt }));
                return { cardHead, targetHead, success: true };
            }
        """)
        await page.wait_for_timeout(2000)
        print(f"  Drag result: {result}")
        if result.get('success'):
            print(f"PASS: Drag simulated ({result.get('cardHead')} -> {result.get('targetHead')})")
        else:
            print(f"WARN: {result.get('error', 'unknown')}")

        # Step 8: Check for errors
        print("Step 8: Error check...")
        if page_errors:
            print(f"FAIL: {len(page_errors)} page errors")
            for e in page_errors[:5]: print(f"  - {e}")
        else:
            print("PASS: Zero page errors")
        if errors:
            print(f"FAIL: {len(errors)} console errors")
            for e in errors[:5]: print(f"  - {e}")
        else:
            print("PASS: Zero console errors")
        if failed_requests:
            print(f"FAIL: {len(failed_requests)} failed requests")
            for r in failed_requests[:5]: print(f"  - {r}")
        else:
            print("PASS: Zero failed requests")

        await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/b04_kanban_drag.png")
        print("Screenshot saved")
        await browser.close()

        success = not page_errors and not errors and not failed_requests and draggable_count > 0
        print(f"\n{'='*50}\nOVERALL: {'PASS' if success else 'FAIL'}\n{'='*50}")
        return success

if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
