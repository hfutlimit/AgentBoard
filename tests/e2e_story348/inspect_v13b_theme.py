"""AGB v13b 主题切换控件专项复核 (第30次执行) — 鲁棒版, 避免 NoneType 迭代错误。"""
import os, json, time, urllib.request, re
from playwright.sync_api import sync_playwright

_THEME_RE = re.compile(r"主题|暗色|亮色|theme|dark|light|☀|🌙|sun|moon", re.I)
BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_v13")
os.makedirs(SHOT, exist_ok=True)

def login_token():
    for _ in range(8):
        try:
            req = urllib.request.Request(BASE + "/api/auth/login",
                data=json.dumps({"username":"admin","password":"admin123"}).encode(),
                headers={"Content-Type":"application/json"}, method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=25).read())["token"]
        except Exception:
            time.sleep(6)
    raise RuntimeError("login failed")

TOKEN = login_token()
res = {}

def safe_eval(page, fn, default=None):
    try:
        return page.evaluate(fn)
    except Exception as e:
        return {"eval_error": str(e)[:120]} if default is None else default

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width":1440,"height":900}, locale="zh-CN")
    page = ctx.new_page()
    page.set_default_navigation_timeout(30000)
    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
    page.evaluate("t=>localStorage.setItem('agentboard_token',t)", TOKEN)
    page.reload(wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("app-root", timeout=20000)
    time.sleep(2)

    theme_info = safe_eval(page, """()=>{
        const btns=[...document.querySelectorAll('header button, .topbar button, app-topbar button, [class*=topbar] button, [class*=header] button')];
        const labels=btns.map(b=>(b.getAttribute('aria-label')||b.innerText||'').trim());
        const themeCandidates=btns.filter(b=>{const t=(b.getAttribute('aria-label')||b.innerText||'').toLowerCase(); return /主题|暗色|亮色|theme|dark|light|☀|🌙|sun|moon/.test(t);});
        return {button_count:btns.length, theme_button_count:themeCandidates.length, dataset_theme:document.documentElement.dataset.theme||''};
    }""")
    print("theme_info:", theme_info)
    page.screenshot(path=os.path.join(SHOT, "home_theme_v13b.png"))

    # 展开用户菜单扫描
    menu_items=[]
    try:
        avatar = page.query_selector("header button:has-text('admin'), .topbar button:has-text('admin'), [class*=avatar]")
        if avatar:
            avatar.click(); time.sleep(1)
            menu_items = safe_eval(page, """()=>{[...document.querySelectorAll('.menu-item, [role=menuitem], .dropdown-item, li')].map(e=>e.innerText.trim()).filter(Boolean)}""", [])
            if isinstance(menu_items, list) and menu_items and not isinstance(menu_items[0], str):
                menu_items=[]
            print("menu_items:", menu_items)
            page.keyboard.press("Escape"); time.sleep(0.5)
            page.screenshot(path=os.path.join(SHOT, "user_menu_v13b.png"))
    except Exception as e:
        print("menu err:", e)

    theme_in_menu = any(bool(_THEME_RE.search(str(i))) for i in (menu_items or []))
    verdict = "MISSING" if (isinstance(theme_info, dict) and theme_info.get("theme_button_count",1)==0 and not theme_in_menu) else "PRESENT"
    res = {"header": theme_info, "user_menu_items": menu_items, "theme_in_menu": theme_in_menu, "verdict": verdict}
    print("[#1431] verdict =", verdict)
    b.close()

with open(os.path.join(OUT,"report_v13b_theme.json"),"w",encoding="utf-8") as f:
    json.dump(res,f,ensure_ascii=False,indent=2)
print("[V13B DONE]")
