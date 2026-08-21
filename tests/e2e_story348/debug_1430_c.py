"""Debug — find where the home shell content comes from when at /epics."""
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
    ctx.add_init_script(f'window.localStorage.setItem("agentboard_token", "{token}")')
    page.goto(BASE + "/epics", wait_until="domcontentloaded", timeout=15000)
    time.sleep(5)
    print("=== /epics url ===", page.url)
    # Check view() signal value
    print("=== main element class ===")
    print(page.evaluate("() => document.querySelector('main')?.className"))
    # Check what is inside main
    print("=== main inner children ===")
    print(page.evaluate("""() => Array.from(document.querySelector('main')?.children || []).map(c => c.tagName + '.' + c.className)"""))
    # Check empty-state 404 element
    print("=== has 404 empty-state? ===")
    print(page.evaluate("() => !!document.querySelector('main .empty-state h2')"))
    print("=== 404 h2 text ===")
    print(page.evaluate("() => document.querySelector('main .empty-state h2')?.innerText"))
    # Has home-shell
    print("=== has app-home-shell? ===")
    print(page.evaluate("() => !!document.querySelector('app-home-shell')"))
    # check view
    print("=== app.ts view signal — try via getComponent ===")
    try:
        view_state = page.evaluate("""() => {
            try {
                const root = document.querySelector('app-root');
                const comp = window.ng?.getComponent?.(root);
                return comp ? (comp.view?.() || 'no_view') : 'no_comp';
            } catch(e) { return 'err:' + e.message; }
        }""")
        print("view():", view_state)
    except Exception as e:
        print("view() err:", e)
    b.close()
