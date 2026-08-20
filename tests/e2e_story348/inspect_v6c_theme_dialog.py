"""v6c: 主题切换 + 新建项目弹窗复核(改用 domcontentloaded 避免 WS 导致的 networkidle 挂起)。"""
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

    # 全量 dump header/全部 button
    btns = page.evaluate("""()=>{
        const all=Array.from(document.querySelectorAll('button'));
        return all.map(x=>({
            text:(x.innerText||'').trim().slice(0,14),
            title:x.title||'',
            aria:x.getAttribute('aria-label')||'',
            cls:(x.className&&x.className.toString?x.className.toString():'').slice(0,70),
            hasSvg: !!x.querySelector('svg'),
            w: Math.round(x.getBoundingClientRect().width),
            h: Math.round(x.getBoundingClientRect().height)
        }));
    }""")
    # 找主题相关
    cand = [x for x in btns if (x['hasSvg'] or x['text'] or x['title'] or x['aria']) and
            (('主题' in (x['text']+x['title']+x['aria'])) or
             ('theme' in (x['text']+x['title']+x['aria']).lower()) or
             ('暗' in (x['text']+x['title'])) or ('亮' in (x['text']+x['title'])) or
             ('sun' in (x['text']+x['title']+x['aria']).lower()) or
             ('moon' in (x['text']+x['title']+x['aria']).lower()))]
    res["all_button_count"] = len(btns)
    res["theme_candidates"] = cand
    res["header_buttons_sample"] = btns[:30]
    print("total buttons:", len(btns))
    print("theme candidates:", json.dumps(cand, ensure_ascii=False))
    # 打印所有带文本或 svg 的 button(前 20)
    for x in btns[:24]:
        print(f"   btn text={x['text']!r} title={x['title']!r} aria={x['aria']!r} svg={x['hasSvg']} cls={x['cls'][:40]}")

    before = page.evaluate("()=>document.documentElement.dataset.theme")
    res["theme_before"] = before
    toggled = False; used = None
    for c in cand:
        sel = None
        if c['aria']: sel = f"button[aria-label='{c['aria']}']"
        elif c['title']: sel = f"button[title='{c['title']}']"
        elif c['text']: sel = f"button:has-text('{c['text']}')"
        if not sel: continue
        el = page.query_selector(sel)
        if not el: continue
        used = sel
        try: el.click(timeout=3000); time.sleep(0.8)
        except Exception: pass
        after = page.evaluate("()=>document.documentElement.dataset.theme")
        if after and after != before:
            toggled = True; res["theme_after"] = after; break
        before = after
    if used and toggled:
        page.screenshot(path=os.path.join(SHOT,"theme_toggled.png"))
        try: page.query_selector(used).click(timeout=3000); time.sleep(0.5)
        except Exception: pass
    res["theme_toggle_selector"] = used
    res["theme_toggled"] = toggled
    print("THEME toggled:", toggled, "selector:", used)

    # 新建项目弹窗
    page.goto(BASE + "/projects", wait_until="domcontentloaded"); time.sleep(1.5)
    el = page.query_selector("button:has-text('新建项目')")
    res["create_button_found"] = el is not None
    opened = False
    if el:
        el.click(timeout=3000); time.sleep(1.2)
        opened = page.evaluate("""()=>{const m=document.querySelector('.modal-overlay,.modal,[role=dialog],app-modal,.dialog'); return !!m && m.offsetParent!==null;}""")
        res["create_opened"] = opened
        if opened:
            page.screenshot(path=os.path.join(SHOT,"create_dialog_recheck.png"))
            # 表单校验: 不填直接提交
            for sub in ["button[type=submit]","button:has-text('创建')","button:has-text('保存')"]:
                se=page.query_selector(sub)
                if se:
                    try:
                        se.click(timeout=1500); time.sleep(0.6)
                        validated=page.evaluate("""()=>{const e=document.querySelector('.error,.invalid,app-field-error,[class*=error],.ng-invalid'); return !!e && e.offsetParent!==null;}""")
                        res["create_validation_shown"]=bool(validated)
                        break
                    except Exception: break
    print("CREATE dialog opened:", opened)
    b.close()

with open(os.path.join(OUT,"report_v6c_theme_dialog.json"),"w",encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
print("=== v6c done ===")
