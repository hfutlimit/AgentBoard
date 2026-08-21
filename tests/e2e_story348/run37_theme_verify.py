"""
Run37 theme toggle functional verification (#1431).
点击用户菜单中的「切换到深色模式」，验证 dataset.theme 是否真正翻转。
"""
import json, os, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_run37")
os.makedirs(SHOT, exist_ok=True)

def login():
    req = urllib.request.Request(BASE + "/api/auth/login",
        data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["token"]

TOKEN = login()
print("token len:", len(TOKEN))
report = {}

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    page = ctx.new_page()
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t=>localStorage.setItem('agentboard_token', t)", TOKEN)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("app-root", timeout=20000)
    time.sleep(2)

    before = page.evaluate("()=>document.documentElement.dataset?document.documentElement.dataset.theme:null")
    report["theme_before"] = before
    page.screenshot(path=os.path.join(SHOT, "theme_before.png"))

    # 点击头像/用户菜单
    clicked = False
    try:
        avatar = page.query_selector("header button:has(svg), .user-avatar, [class*=avatar], header button")
        # 优先匹配头像类
        av = page.query_selector(".user-avatar")
        if not av:
            av = page.query_selector("[class*=avatar]")
        if not av:
            av = page.query_selector("header button:has(svg)")
        if av:
            av.click()
            time.sleep(1)
            clicked = True
    except Exception as e:
        report["avatar_click_err"] = repr(e)[:150]

    report["menu_opened"] = clicked

    # 点击「切换到深色模式」
    toggled = False
    try:
        item = page.get_by_text("切换到深色模式", exact=True)
        if item.count() > 0:
            item.first.click()
            time.sleep(1.5)
            toggled = True
    except Exception as e:
        report["toggle_click_err"] = repr(e)[:150]

    after = page.evaluate("()=>document.documentElement.dataset?document.documentElement.dataset.theme:null")
    report["theme_after_click_dark"] = after
    report["toggled"] = toggled
    page.screenshot(path=os.path.join(SHOT, "theme_after_dark.png"))

    # 再点回浅色（若有「切换到浅色模式」）
    try:
        item2 = page.get_by_text("切换到浅色模式", exact=True)
        if item2.count() > 0:
            item2.first.click()
            time.sleep(1.5)
            report["theme_after_click_light"] = page.evaluate(
                "()=>document.documentElement.dataset?document.documentElement.dataset.theme:null")
    except Exception as e:
        report["toggle_back_err"] = repr(e)[:150]

    b.close()

with open(os.path.join(OUT, "report_run37_theme.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print("REPORT:", json.dumps(report, ensure_ascii=False))
print("DONE")
