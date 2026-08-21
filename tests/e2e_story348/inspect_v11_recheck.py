"""
AGB v11 聚焦复核 + 扩展交互巡检 (第 29 次执行, Story 348 全站巡检)
基于 v8/v7 鲁棒模式(domcontentloaded + 轮询稳定, 禁用 networkidle), 复用 Playwright venv。
目标:
1. 暖机复核 v6 初判 txt=0 的 7 页(projects/epics/tasks/bugs/documents/dashboard/settings),
   判定真假空白(冷启 lazy chunk 时序 artifact vs 真 bug)。
2. 复验 3 个已知 Bug:
   - #1428 全局 routes 误渲染 → /documents /proposals h1="项目中心 11"
   - #1430 全局路由 404 → /epics /stories /tasks /bugs /dashboard 稳定 404
   - #1431 主题切换缺失 → header 全 button 枚举 + 用户菜单展开扫描
3. 新增交互巡检(本轮扩展):
   - 响应式溢出: 1440/1280/1024 三档 viewport 测 home/projects/overview 水平溢出
   - 列表筛选/分页烟测: /project/3/tasks 是否存在筛选输入 + 分页器
   - 新建弹窗复验(v10 模式): projects 页「+ 新建项目」点击后 CDK overlay 是否出现表单
每环节独立 try/except, 末尾强制写 JSON。
"""
import os, json, time, urllib.request, re
from playwright.sync_api import sync_playwright

_THEME_RE = re.compile(r"主题|暗色|亮色|theme|dark|light|☀|🌙|sun|moon", re.I)
def has_theme(s):
    return bool(_THEME_RE.search(s or ""))

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_v11")
os.makedirs(SHOT, exist_ok=True)

def login_token():
    last = None
    for _ in range(8):
        try:
            req = urllib.request.Request(BASE + "/api/auth/login",
                data=json.dumps({"username":"admin","password":"admin123"}).encode(),
                headers={"Content-Type":"application/json"}, method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=25).read())["token"]
        except Exception as e:
            last = str(e)[:120]; time.sleep(6)
    raise RuntimeError("login failed: " + str(last))

TOKEN = login_token()
res = {"warm_recheck": [], "known_bug_recheck": {}, "responsive": [], "list_smoke": {}, "create_dialog": {}}

def settle_measure(page, path, max_wait=24):
    try:
        page.goto(BASE+path, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        return {"path":path,"error":str(e)[:120]}
    prev=-1; stable=0; last=0
    for _ in range(int(max_wait*2)):
        time.sleep(0.5)
        try:
            last = page.evaluate("()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length;}")
        except Exception:
            last=0
        if last==prev:
            stable+=1
            if stable>=4: break
        else:
            stable=0
        prev=last
    h1 = page.evaluate("()=>{const h=document.querySelector('h1'); return h?h.innerText.trim():'';}")
    is404 = page.evaluate("""()=>{const t=(document.body.innerText||''); const nf=document.querySelector('.not-found,[class*=not-found]'); return t.includes('页面不存在')||!!nf;}""")
    overflow = page.evaluate("()=>{const de=document.documentElement,b=document.body; return Math.max(de.scrollWidth-de.clientWidth,b.scrollWidth-b.clientWidth);}")
    try:
        page.screenshot(path=os.path.join(SHOT, f"warm{path.replace('/','_') or 'root'}.png"))
    except Exception:
        pass
    verdict = "BLANK" if (last<30 and not is404) else ("404" if is404 else "OK")
    return {"path":path,"final_text_len":last,"h1":h1,"is_404":bool(is404),"overflow_px":overflow,"verdict":verdict}

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

    # 1) 暖机复核 7 个 v6 初判 0 字符页
    for path in ["/projects","/epics","/tasks","/bugs","/documents","/dashboard","/settings"]:
        try:
            m = settle_measure(page, path)
            res["warm_recheck"].append(m)
            print(f"[WARM] {path:14s} txt={m.get('final_text_len')} h1={str(m.get('h1',''))[:24]!r} 404={m.get('is_404')} ovf={m.get('overflow_px')} -> {m['verdict']}")
        except Exception as e:
            res["warm_recheck"].append({"path":path,"error":str(e)[:120]})
            print(f"[WARM-ERR] {path}: {e}")

    # 2) 已知 Bug 复验
    kb = {}
    # #1428 documents / proposals 误渲染
    for path in ["/documents","/proposals"]:
        try:
            m = settle_measure(page, path)
            kb.setdefault("1428", {})[path] = {"h1": m.get("h1"), "renders_as_project_center": (m.get("h1","").replace("\n"," ")=="项目中心 11"), "verdict": m.get("verdict")}
            print(f"[#1428] {path} h1={m.get('h1')!r} -> {kb['1428'][path]['renders_as_project_center']}")
        except Exception as e:
            kb.setdefault("1428", {})[path] = {"error": str(e)[:120]}
    # #1430 全局路由 404
    for path in ["/epics","/stories","/tasks","/bugs","/dashboard"]:
        try:
            m = settle_measure(page, path)
            kb.setdefault("1430", {})[path] = {"is_404": m.get("is_404"), "verdict": m.get("verdict")}
            print(f"[#1430] {path} is_404={m.get('is_404')} -> {m.get('verdict')}")
        except Exception as e:
            kb.setdefault("1430", {})[path] = {"error": str(e)[:120]}
    # #1431 主题切换缺失 — header 全 button 枚举 + 用户菜单展开
    try:
        page.goto(BASE+"/", wait_until="domcontentloaded", timeout=30000); time.sleep(2)
        theme_info = page.evaluate("""()=>{
            const btns=[...document.querySelectorAll('header button, .topbar button, app-topbar button, [class*=topbar] button, [class*=header] button')];
            const labels=btns.map(b=>(b.getAttribute('aria-label')||b.innerText||'').trim());
            const themeCandidates=btns.filter(b=>{const t=(b.getAttribute('aria-label')||b.innerText||'').toLowerCase(); return /主题|暗色|亮色|theme|dark|light|☀|🌙|sun|moon/.test(t);});
            const themeText=labels.filter(l=>/主题|暗色|亮色|theme|dark|light/.test(l.toLowerCase()));
            return {button_count:btns.length, theme_button_count:themeCandidates.length, theme_text_elements:themeText, dataset_theme:document.documentElement.dataset.theme||''};
        }""")
        # 展开用户菜单, 扫描菜单项是否含主题
        menu_items=[]
        try:
            avatar = page.query_selector("header button:has-text('admin'), .topbar button:has-text('admin'), [class*=avatar]")
            if avatar:
                avatar.click(); time.sleep(1)
                menu_items = page.evaluate("""()=>{[...document.querySelectorAll('.menu-item, [role=menuitem], .dropdown-item, li')].map(e=>e.innerText.trim()).filter(Boolean)}""")
                page.keyboard.press("Escape"); time.sleep(0.5)
        except Exception:
            pass
        kb["1431"] = {"header": theme_info, "user_menu_items": menu_items,
                      "theme_in_menu": any(has_theme(i) for i in menu_items),
                      "verdict": "MISSING" if (theme_info["theme_button_count"]==0 and not any(has_theme(i) for i in menu_items)) else "PRESENT"}
        print(f"[#1431] theme_btn={theme_info['theme_button_count']} in_menu={kb['1431']['theme_in_menu']} -> {kb['1431']['verdict']}")
    except Exception as e:
        kb["1431"] = {"error": str(e)[:150]}
    res["known_bug_recheck"] = kb

    # 3) 响应式溢出: 三档 viewport
    for w in [1440,1280,1024]:
        row={"viewport":w,"pages":{}}
        for path in ["/","/projects","/project/3/overview"]:
            try:
                page.set_viewport_size({"width":w,"height":900})
                page.goto(BASE+path, wait_until="domcontentloaded", timeout=30000); time.sleep(2.5)
                ovf = page.evaluate("()=>{const de=document.documentElement,b=document.body; return Math.max(de.scrollWidth-de.clientWidth,b.scrollWidth-b.clientWidth);}")
                row["pages"][path]=ovf
                print(f"[RESP] w={w} {path:20s} overflow={ovf}")
            except Exception as e:
                row["pages"][path]=f"ERR:{str(e)[:60]}"
        page.set_viewport_size({"width":1440,"height":900})
        res["responsive"].append(row)

    # 4) 列表筛选/分页烟测: /project/3/tasks
    try:
        page.goto(BASE+"/project/3/tasks", wait_until="domcontentloaded", timeout=30000); time.sleep(3)
        list_smoke = page.evaluate("""()=>{
            const inp=[...document.querySelectorAll('input')].map(i=>(i.placeholder||i.getAttribute('aria-label')||'').trim()).filter(Boolean);
            const pag=[...document.querySelectorAll('.pagination, [class*=pagination], .paginator, [class*=paginator], mat-paginator')].length;
            const filterBtn=[...document.querySelectorAll('button')].map(b=>b.innerText.trim()).filter(t=>/筛选|过滤|filter/i.test(t));
            const tableRows=document.querySelectorAll('table tbody tr, .row, [class*=row]').length;
            return {input_placeholders:inp.slice(0,15), paginator_count:pag, filter_buttons:filterBtn.slice(0,5), row_like_count:tableRows};
        }""")
        res["list_smoke"] = list_smoke
        print(f"[LIST] tasks smoke: {list_smoke}")
    except Exception as e:
        res["list_smoke"] = {"error": str(e)[:150]}

    # 5) 新建弹窗复验(v10 模式)
    try:
        page.goto(BASE+"/projects", wait_until="domcontentloaded", timeout=30000); time.sleep(3)
        btn = page.query_selector("button:has-text('新建项目')")
        opened=False; form_fields=0; err=""
        if btn:
            btn.click(); time.sleep(2)
            opened = page.evaluate("""()=>{const d=document.querySelector('.cdk-overlay-container .modal, .cdk-overlay-container [role=dialog], app-modal, .modal-create'); return !!d && d.offsetParent!==null; }""")
            form_fields = page.evaluate("""()=>{const d=document.querySelector('.cdk-overlay-container'); if(!d) return 0; return d.querySelectorAll('input,textarea,select,button').length;}""")
        else:
            err="button 新建项目 not found"
        res["create_dialog"]={"opened":bool(opened),"form_fields":form_fields,"error":err}
        print(f"[DIALOG] opened={opened} fields={form_fields} err={err}")
        try: page.screenshot(path=os.path.join(SHOT,"create_dialog_v11.png"))
        except Exception: pass
    except Exception as e:
        res["create_dialog"]={"error":str(e)[:150]}

    b.close()

with open(os.path.join(OUT,"report_v11_recheck.json"),"w",encoding="utf-8") as f:
    json.dump(res,f,ensure_ascii=False,indent=2)
print("\n[V11 DONE]")
