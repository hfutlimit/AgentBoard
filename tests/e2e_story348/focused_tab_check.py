"""
Focused re-check: workspace tab click URL staleness.
Goto /project/3/overview, click each sidebar tab, capture BOTH
page.url and window.location.pathname + active sidebar highlight, screenshot.
Determines if the URL actually desyncs from the view, or if it's a
Playwright page.url timing artifact.
"""
import os, json, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_focused")
os.makedirs(SHOT, exist_ok=True)

req = urllib.request.Request(
    BASE + "/api/auth/login",
    data=json.dumps({"username":"admin","password":"admin123"}).encode(),
    headers={"Content-Type":"application/json"}, method="POST")
TOKEN = json.loads(urllib.request.urlopen(req, timeout=15).read())["token"]

# Sidebar tab labels as they ACTUALLY appear in the UI (from screenshots):
# 概览, 看板, Epics, 工作项, 搜索, 文档, 成员与 Agents, 设置
tabs = ["概览","看板","Epics","工作项","搜索","文档","成员与 Agents","设置"]

results = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width":1440,"height":900}, locale="zh-CN")
    page = ctx.new_page()
    page.set_default_navigation_timeout(60000)
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t=>localStorage.setItem('agentboard_token',t)", TOKEN)
    page.goto(BASE + "/project/3/overview", wait_until="domcontentloaded")
    page.wait_for_selector("app-root", timeout=20000)
    time.sleep(2.0)

    for lab in tabs:
        try:
            # click the sidebar tab containing this label
            btn = page.locator("a, button").filter(has_text=lab).first
            btn.click(timeout=4000)
        except Exception as e:
            results.append({"tab":lab, "click_err":str(e)[:120]})
            continue
        time.sleep(1.2)
        page_url = page.url
        loc = page.evaluate("()=>location.pathname")
        active_text = page.evaluate("""()=>{const els=document.querySelectorAll('.active, [aria-current=page], .selected'); return Array.from(els).map(e=>e.innerText.trim().slice(0,40));}""")
        main_heading = page.evaluate("()=>{const h=document.querySelector('main h1, main h2, .workspace h1, .workspace h2'); return h?h.innerText.trim().slice(0,60):''}")
        shot = os.path.join(SHOT, f"focus_{lab}.png")
        page.screenshot(path=shot, full_page=False)
        results.append({"tab":lab, "page_url":page_url, "location_pathname":loc,
                        "active_texts":active_text[:5], "main_heading":main_heading})
        print(f"  {lab:20s} page.url={page_url}  loc.path={loc}  active={active_text[:2]}  heading={main_heading}")

    browser.close()

with open(os.path.join(OUT, "focused_tab_check.json"),"w",encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("\nSaved:", os.path.join(OUT, "focused_tab_check.json"))
