"""Story 199 视觉与功能验证（Playwright 真实浏览器）"""
import asyncio, os, sys
from pathlib import Path
from playwright.async_api import async_playwright

API = "http://127.0.0.1:18000"
WEB = "http://127.0.0.1:28080"
PROJECT_ID = 117
EPIC_ID = 126
STORY_ID = 199
USER = "admin"
PASSWORD = "admin123"
SHOTS = Path("screenshots"); SHOTS.mkdir(exist_ok=True)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page_errors, console_errors, failed_reqs = [], [], []
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("requestfailed", lambda r: failed_reqs.append(f"{r.url} -> {r.failure}"))

        # 1) Login via API
        import urllib.request, urllib.parse, json
        req = urllib.request.Request(
            f"{API}/api/auth/login",
            data=json.dumps({"username": USER, "password": PASSWORD}).encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = json.loads(urllib.request.urlopen(req).read())
        token = resp["token"]
        print(f"[auth] token={token[:20]}...")

        # Inject token via add_init_script so it lands BEFORE Angular bootstraps
        await ctx.add_init_script(f"localStorage.setItem('agentboard_token', '{token}')")
        await page.goto(WEB + f"/story/{STORY_ID}", wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        # 3) Verify Story 199 title rendered
        title = await page.locator("h2").first.text_content()
        print(f"[story] title={title!r}")

        # 4) Status badge — verify .status--backlog uses warning soft (yellow)
        status = page.locator(".badge.status.status--backlog").first
        if await status.count() > 0:
            bg = await status.evaluate("e => getComputedStyle(e).backgroundColor")
            color = await status.evaluate("e => getComputedStyle(e).color")
            print(f"[status--backlog] bg={bg} color={color}")
        else:
            print("[status--backlog] NOT FOUND")

        # 5) Story empty state present (since Story 199 has no tasks yet)
        empty = page.locator(".task-empty-state")
        cnt = await empty.count()
        print(f"[task-empty-state] count={cnt}")
        if cnt > 0:
            text = await empty.first.text_content()
            print(f"[task-empty-state] text={text!r}")

        # 6) Edit Story details present
        edit = page.locator(".edit-story")
        edit_cnt = await edit.count()
        print(f"[edit-story] count={edit_cnt}")
        if edit_cnt > 0:
            # open details
            await edit.first.locator("summary").click()
            await page.wait_for_timeout(300)
            ta = page.locator(".edit-story-textarea").first
            ta_count = await ta.count()
            print(f"[edit-story-textarea] count={ta_count}")
            await page.screenshot(path=str(SHOTS / "story199_edit_story.png"), full_page=True)

        # 7) Keyboard hint
        kbd = page.locator(".kbd-hint")
        kbd_cnt = await kbd.count()
        print(f"[kbd-hint] count={kbd_cnt}")
        if kbd_cnt > 0:
            print(f"[kbd-hint] text={(await kbd.first.text_content())!r}")

        # 8) Sidebar background should be surface-2 (light)
        sb = page.locator(".sidebar").first
        sb_bg = await sb.evaluate("e => getComputedStyle(e).backgroundColor")
        sb_w = await sb.evaluate("e => getComputedStyle(e).width")
        print(f"[sidebar] bg={sb_bg} width={sb_w}")

        # 9) main content max-width 1280px
        m = page.locator("main").first
        m_max = await m.evaluate("e => getComputedStyle(e).maxWidth")
        m_pad = await m.evaluate("e => getComputedStyle(e).padding")
        print(f"[main] max-width={m_max} padding={m_pad}")

        # 10) Story title H1
        h2 = page.locator(".story-title-left h2").first
        if await h2.count() > 0:
            h2_size = await h2.evaluate("e => getComputedStyle(e).fontSize")
            h2_weight = await h2.evaluate("e => getComputedStyle(e).fontWeight")
            print(f"[story-h1] size={h2_size} weight={h2_weight}")

        # 11) Topbar/sidebar H1 full page screenshot
        await page.screenshot(path=str(SHOTS / "story199_full.png"), full_page=True)
        print(f"[shot] {SHOTS / 'story199_full.png'}")

        # 12) Create a task to verify task list view (not empty)
        import urllib.request as ur
        create_body = json.dumps({
            "project_id": PROJECT_ID, "story_id": STORY_ID,
            "title": "[199 验证] 测试任务", "type": "task", "priority": "medium", "status": "todo"
        })
        try:
            creq = ur.Request(f"{API}/api/stories/{STORY_ID}/tasks", data=create_body.encode(),
                              headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
            cresp = json.loads(ur.urlopen(creq).read())
            print(f"[create-task] id={cresp.get('id')}")
        except Exception as e:
            print(f"[create-task] {e}")

        # refresh
        await page.goto(WEB + f"/story/{STORY_ID}", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(SHOTS / "story199_with_task.png"), full_page=True)
        print(f"[shot] {SHOTS / 'story199_with_task.png'}")

        # Final error summary
        print("\n=== ERRORS ===")
        print(f"page_errors:    {len(page_errors)}")
        for e in page_errors[:5]: print(f"  - {e}")
        print(f"console_errors: {len(console_errors)}")
        for e in console_errors[:5]: print(f"  - {e}")
        print(f"failed_reqs:    {len(failed_reqs)}")
        bad = [r for r in failed_reqs if r.endswith(".js") or r.endswith(".css") or "static/" in r]
        for r in bad[:5]: print(f"  - {r}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
