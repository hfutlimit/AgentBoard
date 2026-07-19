"""Epic 30 (前端体验升级 v0.8) — API 缓存强化 端到端验证。

覆盖 Task 801 / Task 802 的浏览器侧验证：
- 登录 AgentBoard Web（8080 -> 58125）
- 通过浏览器 fetch 调用 GET /api/cache/stats（跨域，CORS 允许）
- 断言返回结构含 size/hits/misses/hit_rate/default_ttl
- 全程无 page error / console error / .js|.css 404
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from playwright.async_api import async_playwright

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8080")
API = "http://127.0.0.1:58125"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})

        page_errors = []
        console_errors = []
        failed_requests = []

        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("requestfailed", lambda r: (
            failed_requests.append(f"{r.url} ({r.failure})")
            if r.url.endswith((".js", ".css")) else None
        ))

        # Step 1: Login
        await page.goto(f"{BASE}/login", wait_until="networkidle")
        await page.wait_for_selector(".auth-tab", timeout=10000)
        login_tab = page.locator(".auth-tab:has-text('登录')")
        if await login_tab.count() > 0:
            await login_tab.click()
            await page.wait_for_timeout(300)
        await page.fill('input[name="username"]', "admin")
        await page.fill('input[name="password"]', "admin123")
        await page.click(".login-submit")
        await page.wait_for_selector(".topbar", timeout=10000)
        print("[OK] Login successful")

        # Step 2: 进入一个项目页，确保 Web 与 API 连通
        await page.goto(f"{BASE}/project/3", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        print(f"[OK] Navigated to project. URL: {page.url}")

        # Step 3: 通过浏览器 fetch 调用缓存统计端点
        result = await page.evaluate(
            """async () => {
                const r = await fetch('%s/api/cache/stats');
                if (!r.ok) throw new Error('status ' + r.status);
                return await r.json();
            }""" % API
        )
        expected = {"size", "hits", "misses", "hit_rate", "default_ttl"}
        assert isinstance(result, dict), result
        missing = expected - set(result.keys())
        assert not missing, f"missing keys: {missing}"
        assert isinstance(result["default_ttl"], int) and result["default_ttl"] > 0, result
        assert 0.0 <= float(result["hit_rate"]) <= 1.0, result
        print("[OK] /api/cache/stats ->", result)

        # Step 4: 断言无错误
        assert not page_errors, f"page errors: {page_errors}"
        assert not console_errors, f"console errors: {console_errors}"
        assert not failed_requests, f"failed .js/.css requests: {failed_requests}"
        print("[OK] No page/console/.js/.css errors")

        await browser.close()
        print("ALL_EPIC30_E2E_OK")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
