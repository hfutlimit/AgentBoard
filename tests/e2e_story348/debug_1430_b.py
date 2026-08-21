"""Debug /epics with hard reload + check route table from inside Angular."""
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
    # Hard reload — bypass cache
    page.goto(BASE + "/epics", wait_until="domcontentloaded", timeout=15000)
    page.reload(wait_until="domcontentloaded", timeout=15000)
    time.sleep(5)
    print("=== /epics url ===", page.url)
    # Find any app-route-anchor element and dump its full innerText
    has_anchor = page.evaluate("() => !!document.querySelector('app-route-anchor')")
    print("=== has app-route-anchor? ===", has_anchor)
    if has_anchor:
        print("=== app-route-anchor innerText ===")
        print(page.evaluate("() => document.querySelector('app-route-anchor')?.innerText || ''"))
    # Check the route config in JS
    print("=== Angular routes available ===")
    routes_dump = page.evaluate("""() => {
        const router = window.ng?.getComponent?.(document.querySelector('app-root'));
        // Try other approach
        const appRef = window.ng?.getInjector?.(document.querySelector('app-root'));
        return JSON.stringify({has_ng: !!window.ng, has_app_root: !!document.querySelector('app-root')});
    }""")
    print(routes_dump)
    b.close()
