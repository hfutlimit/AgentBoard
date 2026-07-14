"""Playwright verification: Settings page with API Key management."""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda err: errors.append(f"JS Error: {err}"))

        # Direct API login to get token
        api_resp = await page.request.post("http://localhost:58125/api/auth/login", data={
            "username": "jzhong2026", "password": "12345678"
        })
        data = await api_resp.json()
        token = data.get("token")
        print(f"API login: {api_resp.status}, token: {token[:20]}...")

        # Set token in localStorage
        await page.goto("http://localhost:5080/")
        await page.evaluate(f"() => {{ localStorage.setItem('agentboard_token', '{token}'); localStorage.setItem('agentboard_user', 'jzhong2026'); localStorage.setItem('agentboard_is_admin', 'true'); }}")
        print("Token set in localStorage")

        # Navigate to settings
        await page.goto("http://localhost:5080/settings", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        print(f"Settings page URL: {page.url}")

        await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/settings_page.png", full_page=True)
        print("Screenshot saved")

        content = await page.content()
        if "个人设置" in content: print("PASS: '个人设置' heading")
        else: errors.append("MISSING: '个人设置' heading")

        if "API Key" in content: print("PASS: 'API Key' section")
        else: errors.append("MISSING: 'API Key' section")

        if "新建 API Key" in content: print("PASS: '新建 API Key' button")
        else: errors.append("MISSING: '新建 API Key' button")

        # Test API key creation
        await page.click('button:has-text("新建 API Key")')
        await page.wait_for_timeout(500)
        await page.fill('input[placeholder*="例如"]', "Playwright Test Key")
        await page.click('button:has-text("创建")')
        await page.wait_for_timeout(1000)

        if "已创建" in await page.content() or "abk_" in await page.content():
            print("PASS: API key created")
            await page.screenshot(path="E:/Projects/WorkBuddy/AgentBoard/screenshots/settings_created_key.png", full_page=True)
        else:
            errors.append("MISSING: API key created notification")

        await browser.close()
        if errors:
            print(f"\nERRORS ({len(errors)}):")
            for e in errors: print(f"  ❌ {e}")
            return 1
        print("\n✅ All checks passed!")
        return 0

if __name__ == "__main__":
    exit(asyncio.run(main()))
