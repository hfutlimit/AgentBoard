"""
AGB v6b 聚焦复核 — 针对 v6 初判的 9 条 finding 做暖机复核, 区分真实缺陷与冷启 lazy-chunk 时序误报。
1. 8 个全局列表页(epics/stories/tasks/bugs/documents/dashboard/settings/agents/proposals)做 networkidle + 轮询测量,
   直到主内容文本稳定或 20s, 取最终值, 判定是否真空白。
2. 侧边栏导航全量采集(所有 a[href^='/']), 判定 /epics 等全局路由是否 UI 可达。
3. 主题切换: 全量 dump header 内 button(text/title/aria/class/svg), 精确定位并测试 data-theme 翻转。
4. 新建项目弹窗: 重新触发并校验 modal DOM。
严禁臆造: 仅基于本次真实测量与截图上报。
"""
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

SUSPECT = ["/epics","/stories","/tasks","/bugs","/documents","/dashboard","/settings","/agents","/proposals"]

res = {"page_recheck": [], "sidebar_nav": [], "theme": {}, "create_dialog": {}}

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width":1440,"height":900}, locale="zh-CN")
    page = ctx.new_page()
    page.set_default_navigation_timeout(60000)
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t=>localStorage.setItem('agentboard_token',t)", TOKEN)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("app-root", timeout=20000)
    time.sleep(2)

    def settle_measure(path):
        page.goto(BASE+path, wait_until="networkidle", timeout=60000)
        # 轮询主内容长度直到稳定(最多 20s)
        prev = -1; stable_for = 0; last = 0
        for _ in range(40):
            time.sleep(0.5)
            last = page.evaluate("()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length;}")
            if last == prev:
                stable_for += 1
                if stable_for >= 3:
                    break
            else:
                stable_for = 0
            prev = last
        h1 = page.evaluate("()=>{const h=document.querySelector('h1'); return h?h.innerText.trim():'';}")
        is404 = page.evaluate("""()=>{const t=(document.body.innerText||''); const nf=document.querySelector('.not-found,[class*=not-found]'); return t.includes('页面不存在')||!!nf;}""")
        overflow = page.evaluate("()=>{const de=document.documentElement,b=document.body; return Math.max(de.scrollWidth-de.clientWidth,b.scrollWidth-b.clientWidth);}")
        return {"path":path,"final_text_len":last,"h1":h1,"is_404":bool(is404),"overflow_px":overflow}

    for path in SUSPECT:
        m = settle_measure(path)
        m["verdict"] = "BLANK" if (m["final_text_len"] < 30 and not m["is_404"]) else ("404" if m["is_404"] else "OK")
        page.screenshot(path=os.path.join(SHOT, f"recheck{path.replace('/','_')}.png"))
        res["page_recheck"].append(m)
        print(f"[RECHECK] {path:14s} final_txt={m['final_text_len']:5d} h1={m['h1'][:18]!r} 404={m['is_404']} -> {m['verdict']}")

    # ── 侧边栏导航全量采集 ──
    page.goto(BASE + "/", wait_until="networkidle"); time.sleep(2)
    navs = page.evaluate("""()=>{
        const hosts = Array.from(document.querySelectorAll('a[href^=\"/\"]'));
        const seen={}; const out=[];
        for(const a of hosts){
            const href=a.getAttribute('href'); const text=(a.innerText||'').trim();
            const key=href+'|'+text; if(seen[key])continue; seen[key]=1;
            out.push({href, text});
        }
        return out;
    }""")
    res["sidebar_nav"] = navs
    print("\nALL a[href^='/'] links in UI:")
    for n in navs:
        print(f"   {n['text'][:16]:18s} -> {n['href']}")

    # ── 主题切换 DOM 探查 ──
    page.goto(BASE + "/", wait_until="networkidle"); time.sleep(2)
    btns = page.evaluate("""()=>{
        const all=Array.from(document.querySelectorAll('button'));
        return all.map(x=>({
            text:(x.innerText||'').trim().slice(0,12),
            title:x.title||'',
            aria:x.getAttribute('aria-label')||'',
            cls:(x.className&&x.className.toString?x.className.toString():'').slice(0,60),
            hasSvg: !!x.querySelector('svg'),
            rect: Math.round(x.getBoundingClientRect().width)+'x'+Math.round(x.getBoundingClientRect().height)
        }));
    }""")
    res["theme"]["all_buttons"] = btns
    # 找可能是主题的: 含 svg 且文本/title/aria 涉及主题/暗/亮/sun/moon/theme
    cand = [x for x in btns if (x['hasSvg'] or x['text'] or x['title'] or x['aria']) and
            (('主题' in (x['text']+x['title']+x['aria'])) or
             ('theme' in (x['text']+x['title']+x['aria']).lower()) or
             ('暗' in (x['text']+x['title'])) or ('亮' in (x['text']+x['title'])) or
             ('sun' in (x['text']+x['title']+x['aria']).lower()) or
             ('moon' in (x['text']+x['title']+x['aria']).lower()))]
    res["theme"]["candidates"] = cand
    print("\nTHEME candidates:", json.dumps(cand, ensure_ascii=False))
    before = page.evaluate("()=>document.documentElement.dataset.theme")
    res["theme"]["before"] = before
    toggled = False; used = None
    for c in cand:
        # 重建 selector
        sel = None
        if c['aria']: sel = f"button[aria-label='{c['aria']}']"
        elif c['title']: sel = f"button[title='{c['title']}']"
        elif c['text']: sel = f"button:has-text('{c['text']}')"
        if not sel: continue
        el = page.query_selector(sel)
        if not el: continue
        used = sel
        try:
            el.click(timeout=3000); time.sleep(0.8)
        except Exception:
            pass
        after = page.evaluate("()=>document.documentElement.dataset.theme")
        if after and after != before:
            toggled = True; res["theme"]["after"] = after; break
        before = after
    if toggled and used:
        page.screenshot(path=os.path.join(SHOT, "theme_toggled.png"))
        # 切回
        try:
            page.query_selector(used).click(timeout=3000); time.sleep(0.5)
        except Exception: pass
    res["theme"]["toggle_selector"] = used
    res["theme"]["toggled"] = toggled
    print("THEME toggled:", toggled, "used:", used)

    # ── 新建项目弹窗复核 ──
    page.goto(BASE + "/projects", wait_until="networkidle"); time.sleep(1.5)
    el = page.query_selector("button:has-text('新建项目')")
    res["create_dialog"]["button_found"] = el is not None
    opened = False
    if el:
        el.click(timeout=3000); time.sleep(1.2)
        opened = page.evaluate("""()=>{const m=document.querySelector('.modal-overlay,.modal,[role=dialog],app-modal,.dialog'); return !!m && m.offsetParent!==null;}""")
        res["create_dialog"]["opened"] = opened
        if opened:
            page.screenshot(path=os.path.join(SHOT, "create_dialog_recheck.png"))
    print("CREATE dialog opened:", opened)

    b.close()

with open(os.path.join(OUT, "report_v6b_recheck.json"), "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
print("\n=== v6b done ===")
