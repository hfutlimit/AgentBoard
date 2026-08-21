"""Debug /epics route — what's actually rendering?"""
import json, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
req = urllib.request.Request(BASE + "/api/auth/login",
    data=json.dumps({"username":"admin","password":"admin123"}).encode(),
    headers={"Content-Type":"application/json"}, method="POST")
token = json.loads(urllib.request.urlopen(req, timeout=15).read())["token"]

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context()
    page = ctx.new_page()
    page.on("console", lambda m: print(f"CONSOLE [{m.type}]:", m.text[:400]))
    page.on("pageerror", lambda e: print(f"PAGEERROR:", str(e)[:400]))
    ctx.add_init_script(f'window.localStorage.setItem("agentboard_token", "{token}")')
    page.goto(BASE + "/epics", wait_until="domcontentloaded", timeout=15000)
    time.sleep(4)
    print("=== /epics url ===", page.url)
    print("=== app-root children ===")
    print(page.evaluate("""() => Array.from(document.querySelector('app-root')?.children || [])
        .map(c => c.tagName + (c.id ? '#' + c.id : '') + (c.className ? '.' + c.className.split(' ')[0] : ''))"""))
    print("=== body innerText (truncated) ===")
    print(page.evaluate("() => document.body.innerText")[:1500])
    print("=== has app-global-stats-tab? ===", page.evaluate("() => !!document.querySelector('app-global-stats-tab')"))
    print("=== has app-route-anchor? ===", page.evaluate("() => !!document.querySelector('app-route-anchor')"))
    b.close()
