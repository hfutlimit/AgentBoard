"""
AGB v7 全局页聚焦复核 — 取代 v6b 的 networkidle 依赖(应用常驻 WebSocket 导致 networkidle 永不触发而超时)。
- 9 个全局列表页: goto domcontentloaded + 轮询主内容文本稳定(最多 20s), 判定真空白/404/OK。
- 侧栏导航全量采集。
- 主题切换 DOM 探查 + data-theme 翻转测试。
- 新建项目弹窗复核。
每个环节独立 try/except, 末尾强制写 JSON(即便中途异常也已落盘已采集部分)。
"""
import os, json, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_v7")
os.makedirs(SHOT, exist_ok=True)

def login_token():
    last=None
    for attempt in range(8):
        try:
            req = urllib.request.Request(BASE + "/api/auth/login",
                data=json.dumps({"username":"admin","password":"admin123"}).encode(),
                headers={"Content-Type":"application/json"}, method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=25).read())["token"]
        except Exception as e:
            last=str(e)[:80]
            print(f"[login retry {attempt+1}/8] {last}; sleep 8s")
            time.sleep(8)
    raise RuntimeError("login failed after retries: " + str(last))

TOKEN = login_token()

SUSPECT = ["/epics","/stories","/tasks","/bugs","/documents","/dashboard","/settings","/agents","/proposals"]
res = {"page_recheck": [], "sidebar_nav": [], "theme": {}, "create_dialog": {}}

def settle_measure(page, path):
    try:
        page.goto(BASE+path, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        return {"path":path,"error":str(e)[:120]}
    prev=-1; stable=0; last=0
    for _ in range(40):
        time.sleep(0.5)
        try:
            last = page.evaluate("()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length;}")
        except Exception:
            last = 0
        if last==prev:
            stable+=1
            if stable>=3: break
        else:
            stable=0
        prev=last
    h1 = page.evaluate("()=>{const h=document.querySelector('h1'); return h?h.innerText.trim():'';}")
    is404 = page.evaluate("""()=>{const t=(document.body.innerText||''); const nf=document.querySelector('.not-found,[class*=not-found]'); return t.includes('页面不存在')||!!nf;}""")
    overflow = page.evaluate("()=>{const de=document.documentElement,b=document.body; return Math.max(de.scrollWidth-de.clientWidth,b.scrollWidth-b.clientWidth);}")
    try:
        page.screenshot(path=os.path.join(SHOT, f"recheck{path.replace('/','_')}.png"))
    except Exception:
        pass
    return {"path":path,"final_text_len":last,"h1":h1,"is_404":bool(is404),"overflow_px":overflow}

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width":1440,"height":900}, locale="zh-CN")
    page = ctx.new_page()
    page.set_default_navigation_timeout(30000)
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
        page.evaluate("t=>localStorage.setItem('agentboard_token',t)", TOKEN)
        page.reload(wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("app-root", timeout=20000)
        time.sleep(2)
    except Exception as e:
        res["boot_error"] = str(e)[:200]

    for path in SUSPECT:
        try:
            m = settle_measure(page, path)
            m["verdict"] = "BLANK" if (m.get("final_text_len",0) < 30 and not m.get("is_404")) else ("404" if m.get("is_404") else "OK")
            res["page_recheck"].append(m)
            print(f"[RECHECK] {path:14s} final_txt={m.get('final_text_len')} h1={str(m.get('h1',''))[:18]!r} 404={m.get('is_404')} -> {m['verdict']}")
        except Exception as e:
            res["page_recheck"].append({"path":path,"error":str(e)[:120]})
            print(f"[RECHECK-ERR] {path}: {e}")

    # 侧栏导航全量采集
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000); time.sleep(2)
        navs = page.evaluate("""()=>{
            const hosts = Array.from(document.querySelectorAll('a[href^=\"/\"]'));
            const seen={}; const out=[];
            for(const a of hosts){const href=a.getAttribute('href'); const text=(a.innerText||'').trim(); const key=href+'|'+text; if(seen[key])continue; seen[key]=1; out.push({href, text});}
            return out;
        }""")
        res["sidebar_nav"] = navs
        print("\nALL a[href^='/'] links:")
        for n in navs: print(f"   {n['text'][:16]:18s} -> {n['href']}")
    except Exception as e:
        res["sidebar_nav_error"] = str(e)[:150]

    # 主题切换 DOM 探查
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000); time.sleep(2)
        btns = page.evaluate("""()=>{const all=Array.from(document.querySelectorAll('button')); return all.map(x=>({text:(x.innerText||'').trim().slice(0,12), title:x.title||'', aria:x.getAttribute('aria-label')||'', cls:(x.className&&x.className.toString?x.className.toString():'').slice(0,60), hasSvg:!!x.querySelector('svg'), rect:Math.round(x.getBoundingClientRect().width)+'x'+Math.round(x.getBoundingClientRect().height)}));}""")
        res["theme"]["all_buttons"] = btns
        cand = [x for x in btns if (x['hasSvg'] or x['text'] or x['title'] or x['aria']) and
                (('主题' in (x['text']+x['title']+x['aria'])) or ('theme' in (x['text']+x['title']+x['aria']).lower()) or
                 ('暗' in (x['text']+x['title'])) or ('亮' in (x['text']+x['title'])) or
                 ('sun' in (x['text']+x['title']+x['aria']).lower()) or ('moon' in (x['text']+x['title']+x['aria']).lower()))]
        res["theme"]["candidates"] = cand
        print("\nTHEME candidates:", json.dumps(cand, ensure_ascii=False))
        before = page.evaluate("()=>document.documentElement.dataset.theme")
        res["theme"]["before"] = before
        toggled=False; used=None
        for c in cand:
            sel=None
            if c['aria']: sel=f"button[aria-label='{c['aria']}']"
            elif c['title']: sel=f"button[title='{c['title']}']"
            elif c['text']: sel=f"button:has-text('{c['text']}')"
            if not sel: continue
            el=page.query_selector(sel)
            if not el: continue
            used=sel
            try: el.click(timeout=3000); time.sleep(0.8)
            except Exception: pass
            after=page.evaluate("()=>document.documentElement.dataset.theme")
            if after and after!=before: toggled=True; res["theme"]["after"]=after; break
            before=after
        if toggled and used:
            try: page.screenshot(path=os.path.join(SHOT,"theme_toggled.png")); page.query_selector(used).click(timeout=3000); time.sleep(0.5)
            except Exception: pass
        res["theme"]["toggle_selector"]=used; res["theme"]["toggled"]=toggled
        print("THEME toggled:", toggled, "used:", used)
    except Exception as e:
        res["theme"]["error"]=str(e)[:150]

    # 新建项目弹窗复核
    try:
        page.goto(BASE + "/projects", wait_until="domcontentloaded", timeout=30000); time.sleep(1.5)
        el=page.query_selector("button:has-text('新建项目')")
        res["create_dialog"]["button_found"]=el is not None
        opened=False
        if el:
            el.click(timeout=3000); time.sleep(1.2)
            opened=page.evaluate("""()=>{const m=document.querySelector('.modal-overlay,.modal,[role=dialog],app-modal,.dialog'); return !!m && m.offsetParent!==null;}""")
            res["create_dialog"]["opened"]=opened
            if opened: page.screenshot(path=os.path.join(SHOT,"create_dialog_recheck.png"))
        print("CREATE dialog opened:", opened)
    except Exception as e:
        res["create_dialog"]["error"]=str(e)[:150]

    b.close()

with open(os.path.join(OUT, "report_v7_global_recheck.json"), "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
print("\n=== v7 done ===")
