"""
Run39 focused ground-truth recheck (第 39 次巡检暖机复核).
Covers all 9 P1 zero-text routes reported by v6 this round:
  /stories /tasks /bugs /documents /dashboard /settings /proposals /admin /project/3/overview
Uses settle polling (max 25s) to distinguish real blank vs cold-start lazy-chunk timing.
Also: #1431 theme toggle functional verify (click user menu '切换到深色模式', check dataset.theme flip)
Also: P3 create-project dialog opens.
单一进程顺序执行，避免并发 login 超时（历史 R27/R38 教训）。
"""
import json, os, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_run39")
os.makedirs(SHOT, exist_ok=True)

def login():
    for _ in range(8):
        try:
            req = urllib.request.Request(BASE + "/api/auth/login",
                data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())["token"]
        except Exception:
            time.sleep(2)
    raise SystemExit("login failed")

TOKEN = login()
print("token len:", len(TOKEN))

TARGETS = ["/stories", "/tasks", "/bugs", "/documents", "/dashboard",
           "/settings", "/proposals", "/admin", "/project/3/overview"]

def is_404(page):
    return page.evaluate("""()=>{
        const h1=document.querySelector('h1'); const h2=document.querySelector('h2');
        const ht=((h1&&h1.innerText)||'')+' '+((h2&&h2.innerText)||'');
        const nf=document.querySelector('.not-found,[class*=not-found],.error-404,[class*=error-404]');
        const vis=el=>el&&getComputedStyle(el).display!=='none'&&el.offsetParent!==null;
        const bodyHas=(document.body.innerText||'').includes('页面不存在');
        return {h1:h1?h1.innerText.trim():'',h2:h2?h2.innerText.trim():'',
                heading_match:(ht.includes('页面不存在')||ht.includes('找不到')),
                component_match:!!nf&&vis(nf),body_match:bodyHas};
    }""")

def settle(page, url, max_wait=25):
    page.goto(BASE + url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("app-root", timeout=15000)
    except Exception:
        pass
    last=-1; txt=0; h1=""
    for _ in range(int(max_wait/0.5)):
        d = page.evaluate("""()=>{const m=document.querySelector('main')||document.querySelector('app-root');
            const h=document.querySelector('h1');
            return {t:(m?m.innerText:'').trim().length, h:h?h.innerText.trim():''};}""")
        txt=d["t"]; h1=d["h"]
        if txt==last and txt>20: break
        last=txt
        page.wait_for_timeout(500)
    return txt, h1

report = {"targets": [], "theme_check": None, "create_dialog": None}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width":1440,"height":900}, locale="zh-CN")
    page = ctx.new_page()
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t=>localStorage.setItem('agentboard_token', t)", TOKEN)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("app-root", timeout=20000)
    time.sleep(2)

    # 1) 暖机复核 9 路由
    for url in TARGETS:
        try:
            txt, h1 = settle(page, url)
            nf = is_404(page)
            blank = txt < 30
            name = url.strip("/").replace("/", "_") or "root"
            page.screenshot(path=os.path.join(SHOT, name + ".png"))
            is404 = nf["heading_match"] or nf["component_match"] or nf["body_match"]
            rec = {"url": url, "txt": txt, "h1": h1, "blank": blank, "is_404": is404,
                   "screenshot": os.path.relpath(os.path.join(SHOT, name + ".png"), OUT)}
            report["targets"].append(rec)
            print(f"[RECHECK] {url} txt={txt} h1={h1!r} blank={blank} is404={is404}")
        except Exception as e:
            report["targets"].append({"url": url, "error": repr(e)[:200]})
            print(f"[RECHECK-ERR] {url} {repr(e)[:200]}")

    # 2) 主题功能验证 (#1431)
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded")
        time.sleep(2)
        theme_before = page.evaluate("()=>document.documentElement.dataset?document.documentElement.dataset.theme:null")
        avatar = page.query_selector(".user-avatar") or page.query_selector("[class*=avatar]") or page.query_selector("header button:has(svg)")
        menu_items=[]
        toggled=False
        if avatar:
            avatar.click(); time.sleep(1)
            menu_items = page.evaluate("""()=>{
                const menus=Array.from(document.querySelectorAll('.menu,.dropdown,[class*=menu],[class*=dropdown]'));
                const items=[]; menus.forEach(m=>Array.from(m.querySelectorAll('a,button,li')).forEach(li=>{const t=(li.innerText||'').trim(); if(t) items.push(t);}));
                return items;
            }""")
            try:
                item = page.get_by_text("切换到深色模式", exact=True)
                if item.count()>0:
                    item.first.click(); time.sleep(1.5); toggled=True
            except Exception as e:
                report["theme_check"]={"toggle_err":repr(e)[:150]}
        theme_after = page.evaluate("()=>document.documentElement.dataset?document.documentElement.dataset.theme:null")
        report["theme_check"]={"theme_before":theme_before,"theme_after":theme_after,
                               "user_menu_items":menu_items,"toggled":toggled,
                               "flipped": theme_before!=theme_after}
        print(f"[THEME] before={theme_before} after={theme_after} toggled={toggled} flipped={theme_before!=theme_after} menu={menu_items}")
    except Exception as e:
        report["theme_check"]={"error":repr(e)[:200]}
        print(f"[THEME-ERR] {repr(e)[:200]}")

    # 3) 新建项目弹窗 (P3)
    try:
        page.goto(BASE + "/projects", wait_until="domcontentloaded")
        time.sleep(2)
        btn=None
        try:
            btn = page.query_selector("button.heading-action-btn")
            if not btn:
                loc = page.get_by_text("新建项目", exact=False)
                if loc.count()>0:
                    btn = loc.first
        except Exception:
            btn=None
        opened=False; err=None
        if btn:
            try:
                btn.click(); time.sleep(2)
                modal = page.query_selector(".modal") or page.query_selector("[role=dialog]") or page.query_selector(".cdk-overlay-container .modal")
                opened = modal is not None
                page.screenshot(path=os.path.join(SHOT,"create_dialog.png"))
            except Exception as e:
                err=repr(e)[:150]
        report["create_dialog"]={"opened":opened,"err":err}
        print(f"[CREATE] opened={opened} err={err}")
    except Exception as e:
        report["create_dialog"]={"error":repr(e)[:150]}
        print(f"[CREATE-ERR] {repr(e)[:150]}")

    b.close()

with open(os.path.join(OUT,"report_run39_focused.json"),"w",encoding="utf-8") as f:
    json.dump(report,f,ensure_ascii=False,indent=2)
print("DONE")
