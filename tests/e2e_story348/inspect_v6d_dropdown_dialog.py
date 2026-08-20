"""v6d: 深究 (1) 主题切换是否在用户菜单下拉中 (2) 新建项目弹窗真实 DOM。"""
import os, json, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_v6_recheck")
os.makedirs(SHOT, exist_ok=True)

req = urllib.request.Request(BASE + "/api/auth/login",
    data=json.dumps({"username":"admin","password":"admin123"}).encode(),
    headers={"Content-Type":"application/json"}, method="POST")
TOKEN = json.loads(urllib.request.urlopen(req, timeout=15).read())["token"]

res = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width":1440,"height":900}, locale="zh-CN")
    page = ctx.new_page()
    page.set_default_navigation_timeout(60000)
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t=>localStorage.setItem('agentboard_token',t)", TOKEN)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("app-root", timeout=20000)
    time.sleep(2.5)

    # (1) 点击用户菜单, 探查下拉
    user_btn = page.query_selector(".user-button-v7")
    res["user_button_found"] = user_btn is not None
    dropdown_info = {}
    if user_btn:
        user_btn.click(timeout=3000); time.sleep(1.0)
        page.screenshot(path=os.path.join(SHOT, "user_menu_open.png"))
        menu = page.evaluate("""()=>{
            // 查找可见的下拉/菜单容器
            const cands = Array.from(document.querySelectorAll('[class*=menu],[class*=dropdown],[class*=popover],[role=menu],.cdk-overlay-container > *'));
            const vis = cands.filter(c=>c.offsetParent!==null);
            const out = vis.map(c=>({cls:(c.className&&c.className.toString?c.className.toString():'') .slice(0,50),
                text:(c.innerText||'').trim().slice(0,200)}));
            // 也找所有含 主题/暗/亮 的可点击元素
            const themeEls = Array.from(document.querySelectorAll('*')).filter(e=>{
                const t=(e.innerText||'').trim(); const r=e.getBoundingClientRect();
                return r.width>0 && r.height>0 && (t.includes('主题')||t.includes('暗色')||t.includes('亮色')||t.includes('深色'));
            }).map(e=>({tag:e.tagName, text:(e.innerText||'').trim().slice(0,30), cls:(e.className&&e.className.toString?e.className.toString():'').slice(0,40)}));
            return {visibleMenus: out, themeEls: themeEls};
        }""")
        dropdown_info = menu
        res["dropdown"] = menu
        # 尝试点击主题切换(若找到)
        theme_clicked = False
        before = page.evaluate("()=>document.documentElement.dataset.theme")
        for te in menu.get("themeEls", []):
            try:
                el = page.locator(f"text={te['text']}").first
                el.click(timeout=3000); time.sleep(0.8); theme_clicked=True; break
            except Exception:
                pass
        after = page.evaluate("()=>document.documentElement.dataset.theme")
        res["theme_via_menu"] = {"before": before, "after": after, "toggled": bool(after and after!=before), "clicked": theme_clicked}
        page.screenshot(path=os.path.join(SHOT, "theme_via_menu.png"))
    print("USER MENU dropdown:", json.dumps(dropdown_info, ensure_ascii=False)[:600])
    print("THEME via menu:", res.get("theme_via_menu"))

    # (2) 新建项目弹窗
    page.goto(BASE + "/projects", wait_until="domcontentloaded"); time.sleep(1.5)
    el = page.query_selector("button:has-text('新建项目')")
    res["create_button_found"] = el is not None
    if el:
        el.click(timeout=3000); time.sleep(1.5)
        dom = page.evaluate("""()=>{
            const all = Array.from(document.querySelectorAll('*'));
            const modalish = all.filter(e=>{const c=(e.className&&e.className.toString?e.className.toString():'').toLowerCase();
                return (c.includes('modal')||c.includes('dialog')||c.includes('overlay')||c.includes('popup')||c.includes('drawer')||c.includes('panel')) && e.offsetParent!==null;});
            const forms = Array.from(document.querySelectorAll('input,textarea,select,form')).filter(e=>e.offsetParent!==null).map(e=>({tag:e.tagName, type:e.type, ph:e.getAttribute('placeholder')||'', name:e.name||''}));
            return {modalishClasses: modalish.map(e=>(e.className&&e.className.toString?e.className.toString():'').slice(0,50)),
                    visibleFormFields: forms.slice(0,12)};
        }""")
        res["create_dialog_dom"] = dom
        page.screenshot(path=os.path.join(SHOT, "create_dialog_v6d.png"))
        print("CREATE dialog DOM:", json.dumps(dom, ensure_ascii=False)[:500])
    b.close()

with open(os.path.join(OUT,"report_v6d_dropdown_dialog.json"),"w",encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
print("=== v6d done ===")
