"""
AGB 全站前端质量巡检 v6 — Story 348 问题收集容器 (第 26 次执行)
相对 v5 的改进:
- 顶层导航点击穿透验证: 真实点击 header/topbar/nav 内的导航链接,
  记录点击后 window.location.pathname + 主内容长度 + 是否 404,
  彻底判定「一级导航入口是否指向 404 / 空内容页」(v5 仅直接 GET URL,
  未验证 UI 实际可达性, 导致 /epics /stories /tasks /bugs /dashboard 长期被误判为预期 404)。
- 主题切换鲁棒化: 枚举 header 内所有 button, 按 ☀/🌙/主题/theme 文本或 aria-label 精确定位,
  点击后校验 document.documentElement.dataset.theme 是否翻转, 双态截图。
- 详情页暖机: 对每个详情路由做二次 nav(长等待), 规避 dev server 冷启 lazy chunk 编译中的 skeleton 误报。
- 已知 Bug 复验: #1427 / #1428 / #1429 仍复现判定。
- 仅巡检 + 截图 + 记录; 不通过 UI 提交任何表单内容(避免污染数据)。
"""
import sys, os, json, time, urllib.request

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_v6")
os.makedirs(SHOT, exist_ok=True)

def login():
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["token"]

TOKEN = login()
print("token len:", len(TOKEN))

# 全覆盖路由(全局一级导航 + 工作区 8 tab + 详情页 + 已知问题实体)
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
          "interactions": [], "findings": [], "known_bug_recheck": [],
          "top_nav": [], "ws_tabs": []}
findings = report["findings"]

from playwright.sync_api import sync_playwright

def is_artifact(text):
    t = text.lower()
    if "websocket" in t and ("/ws/agents" in t or "handshake" in t): return True
    if "401" in text and "/api/agents" in text: return True
    if "500" in text and "/api/auth/me" in text: return True
    if "failed to load resource" in t and ("401" in t or "500" in t): return True
    return False

def robust_404(page):
    """更严格的 404 判定: 可见 heading 含『页面不存在/找不到』或存在 .not-found 组件。"""
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

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
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

    def nav(path, wait=9):
        page.goto(BASE + path, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector("app-root", timeout=15000)
        except Exception:
            pass
        try:
            page.wait_for_function(
                "()=>{const m=document.querySelector('main')||document.querySelector('app-root'); return m && (m.innerText||'').trim().length>20;}",
                timeout=wait*1000)
        except Exception:
            pass
        time.sleep(1.0)

    # ─── 顶层导航点击穿透验证 ───────────────────────────────────────
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
                seen[key]=1;
                out.push({href, text});
            }
            return out;
        }""")
        print("top_nav anchors found:", len(anchors))
        for a in anchors:
            rec = {"label": a["text"], "href": a["href"]}
            try:
                # 用 SPA 内点击触发路由(而非整页 goto), 更贴近真实用户
                loc = page.locator(f"a[href='{a['href']}']").first
                loc.click(timeout=5000); time.sleep(1.2)
                path = page.evaluate("()=>window.location.pathname")
                txtlen = page.evaluate("()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length;}")
                nf = robust_404(page)
                is_404 = nf["heading_match"] or nf["component_match"] or nf["body_match"]
                rec.update({"landed_path": path, "main_text_len": txtlen, "is_404": bool(is_404),
                            "h1": nf["h1"], "low_content": txtlen < 30 and not is_404})
                if is_404:
                    findings.append({"page": "top_nav", "path": a["href"], "severity": "P1",
                                     "issue": f"一级导航『{a['text']}』({a['href']}) 点击后落到 404 页(h1={nf['h1']!r})"})
                elif txtlen < 30:
                    findings.append({"page": "top_nav", "path": a["href"], "severity": "P2",
                                     "issue": f"一级导航『{a['text']}』({a['href']}) 点击后主内容仅 {txtlen} 字符(疑似空白/未渲染)"})
                if path != a["href"]:
                    rec["note"] = f"点击后实际路由={path}(与 href 不一致)"
                page.screenshot(path=os.path.join(SHOT, f"nav_{a['text'][:8]}.png"))
            except Exception as e:
                rec.update({"error": str(e)[:150]})
            report["top_nav"].append(rec)
            print(f"[NAV] {a['text'][:10]:12s} {a['href']:28s} -> landed={rec.get('landed_path')} txt={rec.get('main_text_len')} 404={rec.get('is_404')}")
    except Exception as e:
        report["top_nav"].append({"error": str(e)[:200]})

    # ─── 全路由覆盖 ─────────────────────────────────────────────────
    for name, path, expect_sel in PAGES:
        rec = {"name": name, "path": path, "url": BASE + path}
        try:
            nav(path)
            if expect_sel:
                present = page.query_selector(expect_sel) is not None
                childcount = page.evaluate("(s)=>{const e=document.querySelector(s); return e?e.children.length:-1;}", expect_sel)
                rec["expected_selector_present"] = present
                rec["expected_child_count"] = childcount
                if present and childcount == 0:
                    findings.append({"page": name, "path": path, "severity": "P1",
                                     "issue": f"期望组件 {expect_sel} 存在但无子内容(疑似空白视图)"})
            txtlen = page.evaluate("()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length;}")
            rec["main_text_len"] = txtlen
            nf = robust_404(page)
            rec["is_404"] = bool(nf["heading_match"] or nf["component_match"] or nf["body_match"])
            rec["h1"] = nf["h1"]
            if txtlen < 30 and not rec["is_404"]:
                findings.append({"page": name, "path": path, "severity": "P1",
                                 "issue": f"主内容区文本长度仅 {txtlen} 字符(疑似空白/未渲染视图, 非404)"})
            overflow = page.evaluate("""()=>{const de=document.documentElement; const b=document.body; return Math.max(de.scrollWidth-de.clientWidth, b.scrollWidth-b.clientWidth);}""")
            rec["horizontal_overflow_px"] = overflow
            if overflow and overflow > 4:
                findings.append({"page": name, "path": path, "severity": "P2",
                                 "issue": f"检测到水平溢出 {overflow}px(内容超出视口/出现横向滚动)"})
            sp = os.path.join(SHOT, f"{name}.png")
            page.screenshot(path=sp, full_page=False)
            rec["screenshot"] = os.path.relpath(sp, OUT)
        except Exception as e:
            rec["error"] = str(e)[:240]
            findings.append({"page": name, "path": path, "severity": "P1",
                             "issue": f"页面加载/巡检异常: {str(e)[:200]}"})
            try:
                sp = os.path.join(SHOT, f"{name}_error.png")
                page.screenshot(path=sp)
                rec["screenshot"] = os.path.relpath(sp, OUT)
            except Exception:
                pass
        report["pages"].append(rec)
        print(f"[OK] {name:14s} {path:32s} txt={rec.get('main_text_len')} h1={rec.get('h1','')[:20]!r} ovf={rec.get('horizontal_overflow_px')} 404={rec.get('is_404')}")

    # ─── 详情页暖机复核(规避 skeleton 误报) ─────────────────────────
    for name, path in [("story_330","/story/330"),("task_1342","/task/1342"),("task_1339","/task/1339"),
                       ("epic_152","/epic/152"),("story_348","/story/348")]:
        nav(path, wait=15)
        blank = page.evaluate("()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length<30;}")
        is404 = page.evaluate("()=>{const t=(document.body.innerText||''); return t.includes('页面不存在');}")
        report["known_bug_recheck"].append({"bug":"#1427", "page": name, "path": path, "still_blank": bool(blank), "is_404": bool(is404)})
        if not blank and not is404:
            page.screenshot(path=os.path.join(SHOT, f"recheck_{name}.png"))

    # ─── #1428 全局 routes 误渲染复验 ───────────────────────────────
    for name, path in [("documents","/documents"),("proposals","/proposals")]:
        nav(path)
        h1 = page.evaluate("()=>{const h=document.querySelector('h1'); return h?h.innerText.trim():'';}")
        is_project_center = ("项目中心" in h1) or page.evaluate("()=>!!document.querySelector('app-projects-view, .projects-view, app-project-center')")
        report["known_bug_recheck"].append({"bug":"#1428","page":name,"path":path,"h1":h1,"renders_as_project_center":bool(is_project_center)})

    # ─── #1429 侧栏标签复验 ────────────────────────────────────────
    try:
        nav("/project/3/overview")
        tabs_loc = page.locator(".project-nav a, .project-nav button, [class*=project-nav] a, [class*=project-nav] button")
        n = tabs_loc.count()
        tab_list = []
        for i in range(n):
            try:
                lab = tabs_loc.nth(i).inner_text().strip()
            except Exception:
                lab = ""
            tab_list.append(lab)
        has_search_label = any("搜索" in t for t in tab_list)
        has_proposal_label = any(("提案" in t) for t in tab_list)
        report["known_bug_recheck"].append({"bug":"#1429","sidebar_labels":tab_list,
                                            "still_has_search_label":bool(has_search_label),
                                            "has_proposal_label":bool(has_proposal_label)})
    except Exception as e:
        report["known_bug_recheck"].append({"bug":"#1429","error":str(e)[:150]})

    # ─── 工作区 tab 动态点击 ────────────────────────────────────────
    try:
        nav("/project/3/overview")
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
    except Exception as e:
        report["interactions"].append({"name": "ws_tab_clicks", "result": "error: " + str(e)[:150]})

    # ─── 主题切换(鲁棒化) ───────────────────────────────────────────
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded"); time.sleep(2)
        cands = page.evaluate("""()=>{
            const btns = Array.from(document.querySelectorAll('button'));
            const out = btns.map(b=>({text:b.innerText.trim(), title:b.title, aria:b.getAttribute('aria-label'), cls:b.className}))
                .filter(b => (b.text && (b.text.includes('☀')||b.text.includes('🌙')||b.text.includes('主题')))
                        || (b.title && b.title.includes('主题'))
                        || (b.aria && b.aria.toLowerCase().includes('theme')));
            return out;
        }""")
        print("theme candidates:", cands)
        toggle = None
        if cands:
            # 用文本/aria 匹配点击
            for c in cands:
                sel = None
                if c["aria"]:
                    sel = f"button[aria-label='{c['aria']}']"
                elif c["title"]:
                    sel = f"button[title='{c['title']}']"
                elif c["text"]:
                    sel = f"button:has-text('{c['text']}')"
                if sel:
                    el = page.query_selector(sel)
                    if el:
                        toggle = el; break
        before = page.evaluate("()=>document.documentElement.dataset.theme")
        toggled = False
        for attempt in range(3):
            try:
                if toggle:
                    toggle.click(timeout=3000)
                else:
                    break
                time.sleep(0.8)
            except Exception:
                pass
            after = page.evaluate("()=>document.documentElement.dataset.theme")
            if after and after != before:
                toggled = True; break
            before = after
        page.screenshot(path=os.path.join(SHOT, "home_theme_after_v6.png"))
        # 切回
        try:
            if toggle:
                toggle.click(timeout=3000); time.sleep(0.6)
        except Exception:
            pass
        page.screenshot(path=os.path.join(SHOT, "home_theme_back_v6.png"))
        report["interactions"].append({"name": "theme_toggle", "candidates": cands,
                                        "toggle_found": toggle is not None,
                                        "dark_applied": toggled})
        if not toggle:
            findings.append({"page": "global", "path": "/", "severity": "P2",
                             "issue": "未找到主题切换按钮(header 内无 ☀/🌙/主题 标识按钮)"})
        elif not toggled:
            findings.append({"page": "global", "path": "/", "severity": "P2",
                             "issue": "主题切换按钮点击后 document.documentElement.dataset.theme 未翻转(暗色/亮色切换失效)"})
    except Exception as e:
        report["interactions"].append({"name": "theme_toggle", "toggle_found": False, "result": "error: " + str(e)[:150]})
        findings.append({"page": "global", "path": "/", "severity": "P2",
                         "issue": "主题切换验证异常: " + str(e)[:120]})

    # ─── 新建弹窗 + 表单校验 ────────────────────────────────────────
    try:
        nav("/projects")
        opened = False
        el = page.query_selector("button:has-text('新建项目')")
        if el:
            el.click(timeout=3000); time.sleep(1.0)
            modal = page.evaluate("""()=>{const m=document.querySelector('.modal-overlay,.modal,[role=dialog]'); return !!m && m.offsetParent!==null;}""")
            opened = bool(modal)
            if opened:
                page.screenshot(path=os.path.join(SHOT, "create_project_dialog_v6.png"))
                for sub in ["button[type=submit]", "button:has-text('创建')", "button:has-text('保存')"]:
                    se = page.query_selector(sub)
                    if se:
                        try:
                            se.click(timeout=1500); time.sleep(0.6)
                            validated = page.evaluate("""()=>{const e=document.querySelector('.error,.invalid,app-field-error,[class*=error],.ng-invalid'); return !!e && e.offsetParent!==null;}""")
                            if validated:
                                report["interactions"].append({"name": "create_dialog_validation", "result": "validation_shown"})
                            break
                        except Exception:
                            break
                for c in ["button:has-text('取消')", "button:has-text('关闭')", "[aria-label='关闭']", ".modal-close"]:
                    ce = page.query_selector(c)
                    if ce:
                        ce.click(timeout=2000); break
        report["interactions"].append({"name": "create_dialog", "result": "opened" if opened else "no_dialog_trigger"})
        if not opened:
            findings.append({"page": "projects", "path": "/projects", "severity": "P3",
                             "issue": "点击新建项目按钮未打开新建项目弹窗(或弹窗未渲染)"})
    except Exception as e:
        report["interactions"].append({"name": "create_dialog", "result": "error: " + str(e)[:150]})

    # ─── 列表搜索/筛选 ──────────────────────────────────────────────
    try:
        nav("/documents")
        search = page.query_selector("input[type=search], input[placeholder*=搜索], input[placeholder*=Search], input[placeholder*=查询]")
        if search:
            search.fill("设计"); time.sleep(0.8)
            page.screenshot(path=os.path.join(SHOT, "documents_search_v6.png"))
            report["interactions"].append({"name": "documents_search", "result": "typed"})
        else:
            report["interactions"].append({"name": "documents_search", "result": "no_search_box"})
    except Exception as e:
        report["interactions"].append({"name": "documents_search", "result": "error: " + str(e)[:150]})

    # ─── 详情页评论输入存在性(不提交) ───────────────────────────────
    try:
        nav("/story/348")
        comment_box = page.query_selector("textarea, input[placeholder*=评论], [placeholder*=comment], .comment-editor, app-comment-editor")
        report["interactions"].append({"name": "story348_comment_box", "found": comment_box is not None})
    except Exception as e:
        report["interactions"].append({"name": "story348_comment_box", "result": "error: " + str(e)[:150]})

    browser.close()

real_console = [e for e in report["console_errors"] if not e.get("artifact")]
real_fail = [f for f in report["failed_requests"] if not f.get("artifact")]
report["real_console_errors"] = real_console
report["real_failed_requests"] = real_fail

with open(os.path.join(OUT, "report_story348.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print("\n==== SUMMARY ====")
print("pages visited:", len(report["pages"]))
print("top_nav links:", len(report["top_nav"]))
print("console errors(total/real):", len(report["console_errors"]), "/", len(real_console))
print("page errors:", len(report["page_errors"]))
print("failed api(total/real):", len(report["failed_requests"]), "/", len(real_fail))
print("findings:", len(findings))
for r in report["known_bug_recheck"]:
    print("   RECHECK", r)
for fnd in findings:
    print("  FINDING:", fnd["severity"], fnd["page"], "-", fnd["issue"][:140])
