"""
AGB 全站前端巡检 R41 (第 41 次 hourly) — 单进程、顺序执行，避免并发 login 超时。
覆盖:
  - 26 路由冷启暖机 settle 测量 (规避 lazy-chunk 时序误报)
  - 顶层导航点击穿透 (6-8 锚点)
  - 工作区 8 tab 动态点击
  - 已知 Bug 复验: #1427 详情空白 / #1428 全局路由误渲染 / #1429 侧栏标签 /
    #1430 全局路由 404 / #1431 主题切换 / #1433 /bugs skeleton 卡死
  - 主题功能验证 + 新建弹窗 + 文档搜索 + 评论框
仅巡检 + 截图 + 记录；不通过 UI 提交任何表单内容。
"""
import json, os, time, urllib.request, sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_run41")
os.makedirs(SHOT, exist_ok=True)

def login():
    for attempt in range(8):
        try:
            req = urllib.request.Request(
                BASE + "/api/auth/login",
                data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())["token"]
        except Exception as e:
            print("login retry", attempt, repr(e)[:120])
            time.sleep(2)
    raise SystemExit("login failed")

TOKEN = login()
print("token len:", len(TOKEN))

PAGES = [
    ("home",          "/",                                  "app-home-shell"),
    ("projects",      "/projects",                          None),
    ("epics",         "/epics",                             None),
    ("stories",       "/stories",                           None),
    ("tasks",         "/tasks",                             None),
    ("bugs",          "/bugs",                              None),
    ("documents",     "/documents",                         None),
    ("dashboard",     "/dashboard",                         None),
    ("settings",      "/settings",                          None),
    ("agents",        "/agents",                            None),
    ("proposals",     "/proposals",                         None),
    ("notifications", "/notifications",                     None),
    ("admin",         "/admin",                             None),
    ("ws_overview",   "/project/3/overview",                "app-project-workspace-route"),
    ("ws_kanban",     "/project/3/kanban",                  "app-project-workspace-route"),
    ("ws_epics",      "/project/3/epics",                   "app-project-workspace-route"),
    ("ws_backlog",    "/project/3/backlog",                 "app-project-workspace-route"),
    ("ws_proposals",  "/project/3/proposals",               "app-project-workspace-route"),
    ("ws_documents",  "/project/3/documents",               "app-project-workspace-route"),
    ("ws_members",    "/project/3/members",                 "app-project-workspace-route"),
    ("ws_settings",   "/project/3/settings",                "app-project-workspace-route"),
    ("epic_152",      "/epic/152",                          None),
    ("story_348",     "/story/348",                         None),
    ("story_330",     "/story/330",                         None),
    ("task_1342",     "/task/1342",                         None),
    ("task_1339",     "/task/1339",                         None),
]

report = {"pages": [], "console_errors": [], "page_errors": [], "failed_requests": [],
          "interactions": [], "findings": [], "known_bug_recheck": [], "top_nav": [], "ws_tabs": []}
findings = report["findings"]

def is_artifact(text):
    t = text.lower()
    if "websocket" in t and ("/ws/agents" in t or "handshake" in t): return True
    if "401" in text and "/api/agents" in text: return True
    if "500" in text and "/api/auth/me" in text: return True
    if "failed to load resource" in t and ("401" in t or "500" in t): return True
    return False

def robust_404(page):
    return page.evaluate("""()=>{
        const vis = (el)=> el && getComputedStyle(el).display!=='none' && el.offsetParent!==null;
        const h1=document.querySelector('h1'); const h2=document.querySelector('h2');
        const ht = ((h1&&h1.innerText)||'') + ' ' + ((h2&&h2.innerText)||'');
        const notFound = document.querySelector('.not-found,[class*=not-found],.error-404,[class*=error-404]');
        const bodyHas = (document.body.innerText||'').includes('页面不存在');
        return { h1: h1?h1.innerText.trim():'', h2: h2?h2.innerText.trim():'',
                 heading_match: (ht.includes('页面不存在')||ht.includes('找不到')),
                 component_match: !!notFound && vis(notFound), body_match: bodyHas };
    }""")

def settle(page, url, max_wait=25):
    """转到 url, 轮询直至 main 文本稳定(>20 且两次相等)或超时, 规避冷启 skeleton 误报。"""
    page.goto(BASE + url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("app-root", timeout=15000)
    except Exception:
        pass
    last=-1; txt=0; h1=""; stable=0
    for _ in range(int(max_wait/0.5)):
        d = page.evaluate("""()=>{const m=document.querySelector('main')||document.querySelector('app-root');
            const h=document.querySelector('h1');
            return {t:(m?m.innerText:'').trim().length, h:h?h.innerText.trim():''};}""")
        txt=d["t"]; h1=d["h"]
        if txt==last and txt>20:
            stable+=1
            if stable>=2: break
        else:
            stable=0
        last=txt
        page.wait_for_timeout(500)
    return txt, h1

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width":1440,"height":900}, locale="zh-CN")
    page = ctx.new_page()
    page.set_default_navigation_timeout(60000)

    def on_console(msg):
        if msg.type == "error":
            report["console_errors"].append({"text": msg.text, "artifact": is_artifact(msg.text)})
    def on_pageerror(err):
        report["page_errors"].append(str(err))
    def on_response(resp):
        if resp.status >= 400 and "/api/" in resp.url:
            report["failed_requests"].append({"url": resp.url, "status": resp.status,
                                              "artifact": is_artifact(str(resp.status)+resp.url)})
    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("response", on_response)

    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t => localStorage.setItem('agentboard_token', t)", TOKEN)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("app-root", timeout=20000)
    try:
        page.wait_for_selector("aside, .sidebar, nav, app-sidebar, header", timeout=12000)
    except Exception:
        pass
    time.sleep(2)

    # ── 全路由暖机 settle 测量 ──
    for name, path, expect_sel in PAGES:
        try:
            txt, h1 = settle(page, path)
            nf = robust_404(page)
            is404 = bool(nf["heading_match"] or nf["component_match"] or nf["body_match"])
            rec = {"name": name, "path": path, "url": BASE + path, "main_text_len": txt,
                   "h1": h1, "is_404": is404}
            if expect_sel:
                present = page.query_selector(expect_sel) is not None
                childcount = page.evaluate("(s)=>{const e=document.querySelector(s); return e?e.children.length:-1;}", expect_sel)
                rec["expected_selector_present"] = present
                rec["expected_child_count"] = childcount
            overflow = page.evaluate("""()=>{const de=document.documentElement; const b=document.body; return Math.max(de.scrollWidth-de.clientWidth, b.scrollWidth-b.clientWidth);}""")
            rec["horizontal_overflow_px"] = overflow
            sp = os.path.join(SHOT, f"{name}.png")
            page.screenshot(path=sp, full_page=False)
            rec["screenshot"] = os.path.relpath(sp, OUT)
            # 判定真实空白 (排除 404)
            if txt < 30 and not is404:
                findings.append({"page": name, "path": path, "severity": "P1",
                                 "issue": f"主内容区文本长度仅 {txt} 字符(疑似空白/未渲染视图, 非404)"})
            if overflow and overflow > 4:
                findings.append({"page": name, "path": path, "severity": "P2",
                                 "issue": f"检测到水平溢出 {overflow}px(内容超出视口/出现横向滚动)"})
            report["pages"].append(rec)
            print(f"[OK] {name:14s} {path:30s} txt={txt} h1={h1[:24]!r} ovf={overflow} 404={is404}")
        except Exception as e:
            report["pages"].append({"name": name, "path": path, "error": str(e)[:240]})
            findings.append({"page": name, "path": path, "severity": "P1", "issue": f"页面加载/巡检异常: {str(e)[:200]}"})
            try:
                page.screenshot(path=os.path.join(SHOT, f"{name}_error.png"))
            except Exception:
                pass

    # ── 顶层导航点击穿透 ──
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded"); time.sleep(2)
        anchors = page.evaluate("""()=>{
            const hosts = Array.from(document.querySelectorAll('header a, .topbar a, nav a, app-sidebar a, [class*=sidebar] a, [class*=navbar] a'));
            const seen = {}; const out = [];
            for (const a of hosts){
                const href = a.getAttribute('href') || a.getAttribute('routerlink') || '';
                const text = (a.innerText||'').trim();
                const key = href + '|' + text;
                if(!href.startsWith('/')) continue;
                if(seen[key]) continue;
                seen[key]=1; out.push({href, text});
            }
            return out;
        }""")
        print("top_nav anchors found:", len(anchors))
        for a in anchors:
            rec = {"label": a["text"], "href": a["href"]}
            try:
                loc = page.locator(f"a[href='{a['href']}']").first
                loc.click(timeout=5000); time.sleep(1.2)
                path = page.evaluate("()=>window.location.pathname")
                txtlen = page.evaluate("()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length;}")
                nf = robust_404(page)
                is_404 = bool(nf["heading_match"] or nf["component_match"] or nf["body_match"])
                rec.update({"landed_path": path, "main_text_len": txtlen, "is_404": is_404,
                            "h1": nf["h1"], "low_content": txtlen < 30 and not is_404})
                if is_404:
                    findings.append({"page": "top_nav", "path": a["href"], "severity": "P1",
                                     "issue": f"一级导航『{a['text']}』({a['href']}) 点击后落到 404 页(h1={nf['h1']!r})"})
                elif txtlen < 30:
                    findings.append({"page": "top_nav", "path": a["href"], "severity": "P2",
                                     "issue": f"一级导航『{a['text']}』({a['href']}) 点击后主内容仅 {txtlen}  ͨ字符(疑似空白/未渲染)"})
                if path != a["href"]:
                    rec["note"] = f"点击后实际路由={path}(与 href 不一致)"
                page.screenshot(path=os.path.join(SHOT, f"nav_{a['text'][:8]}.png"))
            except Exception as e:
                rec.update({"error": str(e)[:150]})
            report["top_nav"].append(rec)
            print(f"[NAV] {a['text'][:10]:12s} {a['href']:28s} -> landed={rec.get('landed_path')} txt={rec.get('main_text_len')} 404={rec.get('is_404')}")
    except Exception as e:
        report["top_nav"].append({"error": str(e)[:200]})

    # ── 工作区 8 tab 动态点击 ──
    try:
        page.goto(BASE + "/project/3/overview", wait_until="domcontentloaded"); time.sleep(2)
        tabs_loc = page.locator(".project-nav a, .project-nav button, [class*=project-nav] a, [class*=project-nav] button")
        n = tabs_loc.count()
        for i in range(n):
            try:
                lab = tabs_loc.nth(i).inner_text().strip()
                tabs_loc.nth(i).click(timeout=4000); time.sleep(0.9)
                path = page.evaluate("()=>window.location.pathname")
                ok = path.startswith("/project/3/")
                report["ws_tabs"].append({"tab": lab, "path": path, "url_ok": ok})
            except Exception as ex:
                report["ws_tabs"].append({"tab": lab if 'lab' in dir() else '?', "error": str(ex)[:120]})
        bad = [t for t in report["ws_tabs"] if not t.get("url_ok")]
        if bad:
            findings.append({"page": "ws_tabs", "path": "/project/3/*", "severity": "P2",
                             "issue": f"工作区 tab 点击后路径异常: {[t.get('tab') for t in bad]}"})
        print("ws_tabs:", report["ws_tabs"])
    except Exception as e:
        report["interactions"].append({"name": "ws_tab_clicks", "result": "error: " + str(e)[:150]})

    # ── 已知 Bug 复验 #1427 详情空白 ──
    for name, path in [("story_330","/story/330"),("task_1342","/task/1342"),("task_1339","/task/1339"),
                       ("epic_152","/epic/152"),("story_348","/story/348")]:
        try:
            txt, h1 = settle(page, path)
            is404 = page.evaluate("()=>{const t=(document.body.innerText||''); return t.includes('页面不存在');}")
            still_blank = txt < 30 and not is404
            report["known_bug_recheck"].append({"bug":"#1427","page":name,"path":path,"txt":txt,"still_blank":bool(still_blank),"is_404":bool(is404)})
            if not still_blank and not is404:
                page.screenshot(path=os.path.join(SHOT, f"recheck_{name}.png"))
        except Exception as e:
            report["known_bug_recheck"].append({"bug":"#1427","page":name,"error":str(e)[:150]})

    # ── #1428 全局 routes 误渲染 ──
    for name, path in [("documents","/documents"),("proposals","/proposals")]:
        try:
            txt, h1 = settle(page, path)
            is_project_center = ("项目中心" in h1) or page.evaluate("()=>!!document.querySelector('app-projects-view, .projects-view, app-project-center')")
            report["known_bug_recheck"].append({"bug":"#1428","page":name,"path":path,"h1":h1,"renders_as_project_center":bool(is_project_center)})
        except Exception as e:
            report["known_bug_recheck"].append({"bug":"#1428","page":name,"error":str(e)[:150]})

    # ── #1429 侧栏标签 ──
    try:
        page.goto(BASE + "/project/3/overview", wait_until="domcontentloaded"); time.sleep(2)
        tabs_loc = page.locator(".project-nav a, .project-nav button, [class*=project-nav] a, [class*=project-nav] button")
        n = tabs_loc.count()
        tab_list = []
        for i in range(n):
            try:
                lab = tabs_loc.nth(i).inner_text().strip()
            except Exception:
                lab = ""
            tab_list.append(lab)
        has_search = any("搜索" in t for t in tab_list)
        has_proposal = any("提案" in t for t in tab_list)
        report["known_bug_recheck"].append({"bug":"#1429","sidebar_labels":tab_list,
                                            "still_has_search_label":bool(has_search),
                                            "has_proposal_label":bool(has_proposal)})
    except Exception as e:
        report["known_bug_recheck"].append({"bug":"#1429","error":str(e)[:150]})

    # ── #1430 全局路由 404 ──
    for name, path in [("epics","/epics"),("stories","/stories"),("tasks","/tasks"),("bugs","/bugs"),("dashboard","/dashboard")]:
        try:
            txt, h1 = settle(page, path)
            nf = robust_404(page)
            is404 = bool(nf["heading_match"] or nf["component_match"] or nf["body_match"])
            report["known_bug_recheck"].append({"bug":"#1430","page":name,"path":path,"h1":h1,"is_404":is404,"txt":txt})
        except Exception as e:
            report["known_bug_recheck"].append({"bug":"#1430","page":name,"error":str(e)[:150]})

    # ── #1431 主题切换功能验证 ──
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded"); time.sleep(2)
        theme_before = page.evaluate("()=>document.documentElement.dataset?document.documentElement.dataset.theme:null")
        avatar = page.query_selector(".user-avatar") or page.query_selector("[class*=avatar]")
        menu_items=[]; toggled=False
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
        report["known_bug_recheck"].append({"bug":"#1431","theme_before":theme_before,"theme_after":theme_after,
                                            "user_menu_items":menu_items,"toggled":toggled,
                                            "flipped": theme_before!=theme_after})
        # 切回
        try:
            if toggled:
                avatar2 = page.query_selector(".user-avatar") or page.query_selector("[class*=avatar]")
                if avatar2:
                    avatar2.click(); time.sleep(0.6)
                    page.get_by_text("切换到深色模式", exact=True).first.click(); time.sleep(0.6)
        except Exception:
            pass
        print(f"[THEME] before={theme_before} after={theme_after} flipped={theme_before!=theme_after}")
    except Exception as e:
        report["known_bug_recheck"].append({"bug":"#1431","error":repr(e)[:200]})

    # ── #1433 /bugs skeleton 卡死复验 (3 次访问 + 30s 满等待) ──
    bugs_runs = []
    for run_i in range(3):
        try:
            page.goto(BASE + "/bugs", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("app-root", timeout=15000)
            skeleton_cycles=0
            txt=0; h1=""; stuck=False
            for _ in range(60):  # up to 30s
                d = page.evaluate("""()=>{const m=document.querySelector('main')||document.querySelector('app-root');
                    const h=document.querySelector('h1');
                    const sk=document.querySelector('[class*=skeleton],[class*=loading],.spinner,.loader');
                    return {t:(m?m.innerText:'').trim().length, h:h?h.innerText.trim():'', sk:!!sk};}""")
                txt=d["t"]; h1=d["h"]
                if txt>30: break
                if d["sk"]: skeleton_cycles+=1
                page.wait_for_timeout(500)
            bugs_runs.append({"run": run_i+1, "txt": txt, "h1": h1, "skeleton_seen": skeleton_cycles,
                             "stuck": txt<30})
            page.screenshot(path=os.path.join(SHOT, f"bugs_run{run_i+1}.png"))
        except Exception as e:
            bugs_runs.append({"run": run_i+1, "error": str(e)[:150]})
    report["known_bug_recheck"].append({"bug":"#1433","runs":bugs_runs,
                                        "still_reproduces": any(r.get("stuck") for r in bugs_runs if "error" not in r)})

    # ── 新建项目弹窗 ──
    try:
        page.goto(BASE + "/projects", wait_until="domcontentloaded"); time.sleep(2)
        btn=None
        try:
            btn = page.query_selector("button.heading-action-btn")
            if not btn:
                loc = page.get_by_text("新建项目", exact=False)
                if loc.count()>0: btn = loc.first
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
        report["interactions"].append({"name":"create_dialog","opened":opened,"err":err})
        if not opened:
            findings.append({"page":"projects","path":"/projects","severity":"P3","issue":"点击新建项目按钮未打开新建项目弹窗(或弹窗未渲染)"})
    except Exception as e:
        report["interactions"].append({"name":"create_dialog","result":"error: "+str(e)[:150]})

    # ── 文档搜索 ──
    try:
        page.goto(BASE + "/documents", wait_until="domcontentloaded"); time.sleep(2)
        search = page.query_selector("input[type=search], input[placeholder*=搜索], input[placeholder*=Search], input[placeholder*=查询]")
        if search:
            search.fill("设计"); time.sleep(0.8)
            page.screenshot(path=os.path.join(SHOT,"documents_search.png"))
            report["interactions"].append({"name":"documents_search","result":"typed"})
        else:
            report["interactions"].append({"name":"documents_search","result":"no_search_box"})
    except Exception as e:
        report["interactions"].append({"name":"documents_search","result":"error: "+str(e)[:150]})

    # ── 评论框存在性 ──
    try:
        page.goto(BASE + "/story/348", wait_until="domcontentloaded"); time.sleep(2)
        comment_box = page.query_selector("textarea, input[placeholder*=评论], [placeholder*=comment], .comment-editor, app-comment-editor")
        report["interactions"].append({"name":"story348_comment_box","found": comment_box is not None})
    except Exception as e:
        report["interactions"].append({"name":"story348_comment_box","result":"error: "+str(e)[:150]})

    browser.close()

real_console = [e for e in report["console_errors"] if not e.get("artifact")]
real_fail = [f for f in report["failed_requests"] if not f.get("artifact")]
report["real_console_errors"] = real_console
report["real_failed_requests"] = real_fail

with open(os.path.join(OUT,"report_story348_run41.json"),"w",encoding="utf-8") as f:
    json.dump(report,f,ensure_ascii=False,indent=2)

print("\n==== SUMMARY ====")
print("pages visited:", len(report["pages"]))
print("console errors(total/real):", len(report["console_errors"]), "/", len(real_console))
print("page errors:", len(report["page_errors"]))
print("failed api(total/  real):", len(report["failed_requests"]), "/", len(real_fail))
print("findings:", len(findings))
for r in report["known_bug_recheck"]:
    print("   RECHECK", r)
for fnd in findings:
    print("  FINDING:", fnd["severity"], fnd["page"], "-", fnd["issue"][:140])
print("DONE")
