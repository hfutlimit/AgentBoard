import sys, os, json, time, urllib.request
from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:4200"
req = urllib.request.Request(BASE + "/api/auth/login",
    data=json.dumps({"username":"admin","password":"admin123"}).encode(),
    headers={"Content-Type":"application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    token = json.loads(r.read())["token"]
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width":1440,"height":900}, locale="zh-CN")
    page = ctx.new_page()
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t => localStorage.setItem('agentboard_token', t)", token)
    for path in ["/documents", "/proposals", "/projects", "/agents"]:
        page.goto(BASE + path, wait_until="domcontentloaded")
        time.sleep(1.5)
        title = page.title()
        h1 = page.query_selector("h1")
        h1txt = h1.inner_text() if h1 else "(no h1)"
        print(f"{path}: title={title} h1={h1txt[:60]}")
    browser.close()
