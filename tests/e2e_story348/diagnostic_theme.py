import sys, os, json, time, urllib.request
BASE = "http://127.0.0.1:4200"
req = urllib.request.Request(BASE + "/api/auth/login",
    data=json.dumps({"username":"admin","password":"admin123"}).encode(),
    headers={"Content-Type":"application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    token = json.loads(r.read())["token"]
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width":1440,"height":900}, locale="zh-CN")
    page = ctx.new_page()
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t=>localStorage.setItem('agentboard_token',t)", token)
    page.reload(wait_until="domcontentloaded")
    time.sleep(2)
    info = page.evaluate("""()=>{
      const btn = document.querySelector('#theme-toggle');
      const all_btns = Array.from(document.querySelectorAll('button'));
      const theme_btns = all_btns.filter(b => (b.innerText||'').includes('☀') || (b.innerText||'').includes('🌙') || (b.title||'').includes('主题') || (b.getAttribute('aria-label')||'').includes('主题'));
      const view_value = (()=>{ try{ const w=window; const c=document.querySelector('app-root'); return (w.ng && w.ng.getComponent && c) ? (w.ng.getComponent(c).view ? w.ng.getComponent(c).view() : 'no-view-signal') : 'ng-unavailable'; }catch(e){ return e.message; } })();
      const header = document.querySelector('header');
      return {
        theme_toggle_in_dom: !!btn,
        theme_toggle_parent: btn ? btn.parentElement?.outerHTML?.slice(0,200) : null,
        topbar_right_html: document.querySelector('.topbar-right')?.outerHTML?.slice(0,800),
        header_html: header ? header.outerHTML.slice(0,2000) : null,
        view_value: view_value,
        theme_like_buttons: theme_btns.map(b=>({text:b.innerText.trim(), title:b.title, aria:b.getAttribute('aria-label'), classes:b.className, hidden:b.offsetParent===null, rect:b.getBoundingClientRect().width+'x'+b.getBoundingClientRect().height}))
      };
    }
    """)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    page.screenshot(path=os.path.join(os.path.dirname(__file__),"screenshots","diag_home_theme.png"))
    b.close()
