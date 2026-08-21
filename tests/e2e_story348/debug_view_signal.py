"""Debug：访问 /story/330 时 view() signal 实际值是什么。"""
import json, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"

def login_token():
    req = urllib.request.Request(BASE + "/api/auth/login",
        data=json.dumps({"username":"admin","password":"admin123"}).encode(),
        headers={"Content-Type":"application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())["token"]

res = {}
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context()
    page = ctx.new_page()
    token = login_token()
    ctx.add_init_script(f"window.localStorage.setItem('agentboard_token', '{token}')")

    # 1) /story/1 — 存在的
    page.goto(BASE + "/story/1", wait_until="domcontentloaded", timeout=20000)
    time.sleep(3)
    res["story_1"] = {
        "url": page.url,
        "main_text_len": page.evaluate("() => (document.querySelector('main')||document.body).innerText.length"),
        "has_story_330": page.evaluate("() => document.body.innerText.includes('Demo Epic')"),
        "has_home_shell": page.evaluate("() => !!document.querySelector('app-home-shell')"),
    }

    # 2) /story/330 — 不存在
    page.goto(BASE + "/story/330", wait_until="domcontentloaded", timeout=20000)
    time.sleep(3)
    res["story_330"] = {
        "url": page.url,
        "main_text_len": page.evaluate("() => (document.querySelector('main')||document.body).innerText.length"),
        "has_story_330": page.evaluate("() => document.body.innerText.includes('Demo Epic')"),
        "has_home_shell": page.evaluate("() => !!document.querySelector('app-home-shell')"),
        "main_text_first_300": page.evaluate("() => (document.querySelector('main')||document.body).innerText.substring(0, 300)"),
    }
    browser.close()

print(json.dumps(res, ensure_ascii=True, indent=2))
