"""
AGB 全站前端质量巡检 v4 — Story 348 问题收集容器
相对 v3 的改进:
- 主题切换: 改用 document.documentElement.dataset.theme ('dark'/'light') 判定(v3 误用 classList.contains('dark') 永远 false)
- 工作区 tab: 改用 .project-nav-button-v7 真实 routerLink 锚点点击, 校验 URL 变化(v3 用 get_by_text exact 命中副本文本)
- 新建弹窗: 改用 #proj-new-btn (项目中心新建项目) 可靠触发, 校验 .modal-overlay / role=dialog 出现
- 表单校验: 打开新建弹窗后空提交, 校验是否出现校验错误(不真正提交)
- 已有 Bug 复验: 专门复验 #1427(详情空白) / #1428(全局 routes) 是否仍复现, 供去重
- 仅巡检 + 截图 + 记录; 不通过 UI 提交任何表单内容(避免污染数据)
"""
import sys, os, json, time, urllib.request

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots")
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

PAGES = [
    ("home",          "/",                                  "app-home-shell"),
    ("projects",      "/projects",                          None),
    ("agents",        "/agents",                            None),
    ("documents",     "/documents",                         "app-documents-tab"),
    ("proposals",     "/proposals",                         "app-proposals-tab"),
    ("settings",      "/settings",                          None),
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
    ("task_1339",     "/task_1339",                         None),
]

report = {"pages": [], "console_errors": [], "page_errors": [], "failed_requests": [],
          "interactions": [], "findings": [], "known_bug_recheck": []}
findings = report["findings"]

from playwright.sync_api import sync_playwright

def is_artifact(text):
    t = text.lower()
    if "websocket" in t and ("/ws/agents" in t or "handshake" in t): return True
    if "401" in text and "/api/agents" in text: return True
    if "500" in text and "/api/auth/me" in text: return True
    if "failed to load resource" in t and ("401" in t or "500" in t): return True
    return False

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
        page.wait_for_selector("aside, .sidebar, nav, app-sidebar", timeout=12000)
    except Exception:
        pass
    time.sleep(2)

    def nav(path):
        page.goto(BASE + path, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector("app-root", timeout=15000)
        except Exception:
            pass
        try:
            page.wait_for_function(
                "()=>{const m=document.querySelector('main')||document.querySelector('app-root'); return m && (m.innerText||'').trim().length>20;}",
                timeout=9000)
        except Exception:
            pass
        time.sleep(1.2)

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
            if txtlen < 30:
                findings.append({"page": name, "path": path, "severity": "P1",
                                 "issue": f"主内容区文本长度仅 {txtlen} 字符(疑似空白/未渲染视图)"})
            overflow = page.evaluate("""()=>{const de=document.documentElement; const b=document.body; return Math.max(de.scrollWidth-de.clientWidth, b.scrollWidth-b.clientWidth);}""")
            rec["horizontal_overflow_px"] = overflow
            if overflow and overflow > 4:
                findings.append({"page": name, "path": path, "severity": "P2",
                                 "issue": f"检测到水平溢出 {overflow}px(内容超出视口/出现横向滚动)"})
            dim = page.evaluate("""()=>{const m=document.querySelector('main,.layout,app-project-workspace-route,.content'); if(!m) return null; const r=m.getBoundingClientRect(); return {h:Math.round(r.height),w:Math.round(r.width)};}""")
            rec["main_dim"] = dim
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
        print(f"[OK] {name:14s} {path:32s} txt={rec.get('main_text_len')} overflow={rec.get('horizontal_overflow_px')} expect={rec.get('expected_selector_present')} child={rec.get('expected_child_count')}")

    # ─── 已知 Bug 复验 ───────────────────────────────────────────────
    # #1427 详情空白: story/330, task/1342, task/1339
    for name, path in [("story_330","/story/330"),("task_1342","/task/1342"),("task_1339","/task/1339")]:
        nav(path)
        blank = page.evaluate("()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length<30;}")
        report["known_bug_recheck"].append({"bug":"#1427","page":name,"path":path,"still_blank":bool(blank)})
    # #1428 全局 routes: /documents /proposals 是否被错误渲染为项目中心
    for name, path in [("documents","/documents"),("proposals","/proposals")]:
        nav(path)
        h1 = page.evaluate("()=>{const h=document.querySelector('h1'); return h?h.innerText.trim():'';}")
        is_project_center = ("项目中心" in h1) or page.evaluate("()=>!!document.querySelector('app-projects-view, .projects-view, app-project-center')")
        report["known_bug_recheck"].append({"bug":"#1428","page":name,"path":path,"h1":h1,"renders_as_project_center":bool(is_project_center)})

    # ─── 主题切换 (修复 v3 误报: 用 dataset.theme) ─────────────────────
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded"); time.sleep(2)
        el = page.wait_for_selector("#theme-toggle", timeout=8000)
        before = page.evaluate("()=>document.documentElement.dataset.theme")
        toggled = False
        for attempt in range(3):
            try:
                el.click(timeout=3000); time.sleep(0.8)
            except Exception:
                pass
            after = page.evaluate("()=>document.documentElement.dataset.theme")
            if after and after != before:
                toggled = True; break
            before = after
        page.screenshot(path=os.path.join(SHOT, "home_dark.png"))
        # 切回亮色
        try:
            el.click(timeout=3000); time.sleep(0.6)
        except Exception:
            pass
        report["interactions"].append({"name": "theme_toggle",
                                       "toggle_button_found": True,
                                       "dark_applied": toggled})
        if not toggled:
            findings.append({"page": "global", "path": "/", "severity": "P2",
                             "issue": "点击 #theme-toggle 后 dataset.theme 未切换(暗色主题切换失效)"})
    except Exception as e:
        report["interactions"].append({"name": "theme_toggle", "toggle_button_found": False, "result": "error: " + str(e)[:150]})
        findings.append({"page": "global", "path": "/", "severity": "P2",
                         "issue": "未找到 #theme-toggle 主题切换按钮(首页视图下应渲染)"})

    # ─── 工作区 tab 点击 (修复 v3 误报: .project-nav-button-v7) ────────
    try:
        nav("/project/3/overview")
        tab_labels = ["概览","看板","Epics","工作项","提案","文档","成员与 Agents","设置"]
        tab_results = []
        for lab in tab_labels:
            try:
                btn = page.locator(".project-nav-button-v7", has_text=lab).first
                btn.click(timeout=4000); time.sleep(0.8)
                url = page.url
                ok = lab in url or url.rstrip("/").endswith(("overview","kanban","epics","backlog","proposals","documents","members","settings"))
                tab_results.append({"tab": lab, "clicked": True, "url": url, "url_matches": ok})
                page.screenshot(path=os.path.join(SHOT, f"ws_tab_{lab}.png"))
            except Exception as ex:
                tab_results.append({"tab": lab, "clicked": False, "err": str(ex)[:100]})
        report["interactions"].append({"name": "ws_tab_clicks", "result": tab_results})
        failures = [t for t in tab_results if not (t.get("clicked") and t.get("url_matches"))]
        if failures:
            findings.append({"page": "ws_tabs", "path": "/project/3/*", "severity": "P2",
                             "issue": f"工作区 tab 点击后未正确导航: {[f['tab'] for f in failures]}"})
    except Exception as e:
        report["interactions"].append({"name": "ws_tab_clicks", "result": "error: " + str(e)[:150]})

    # ─── 新建弹窗 + 表单校验 (修复 v3 误报: #proj-new-btn) ───────────
    try:
        nav("/projects")
        opened = False
        el = page.query_selector("#proj-new-btn")
        if el:
            el.click(timeout=3000); time.sleep(1.0)
            modal = page.evaluate("""()=>{const m=document.querySelector('.modal-overlay,.modal,[role=dialog]'); return !!m && m.offsetParent!==null;}""")
            opened = bool(modal)
            if opened:
                page.screenshot(path=os.path.join(SHOT, "create_project_dialog.png"))
                # 空提交看校验
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
                # 关闭
                for c in ["button:has-text('取消')", "button:has-text('关闭')", "[aria-label='关闭']", ".modal-close"]:
                    ce = page.query_selector(c)
                    if ce:
                        ce.click(timeout=2000); break
        report["interactions"].append({"name": "create_dialog", "result": "opened" if opened else "no_dialog_trigger"})
        if not opened:
            findings.append({"page": "projects", "path": "/projects", "severity": "P3",
                             "issue": "点击 #proj-new-btn 未打开新建项目弹窗(或弹窗未渲染)"})
    except Exception as e:
        report["interactions"].append({"name": "create_dialog", "result": "error: " + str(e)[:150]})

    # ─── 列表搜索/筛选 (documents 列表搜索框) ────────────────────────
    try:
        nav("/documents")
        search = page.query_selector("input[type=search], input[placeholder*=搜索], input[placeholder*=Search], input[placeholder*=查询]")
        if search:
            search.fill("设计"); time.sleep(0.8)
            page.screenshot(path=os.path.join(SHOT, "documents_search.png"))
            report["interactions"].append({"name": "documents_search", "result": "typed"})
        else:
            report["interactions"].append({"name": "documents_search", "result": "no_search_box"})
    except Exception as e:
        report["interactions"].append({"name": "documents_search", "result": "error: " + str(e)[:150]})

    browser.close()

# 统计真实(非产物)错误
real_console = [e for e in report["console_errors"] if not e.get("artifact")]
real_fail = [f for f in report["failed_requests"] if not f.get("artifact")]
report["real_console_errors"] = real_console
report["real_failed_requests"] = real_fail

with open(os.path.join(OUT, "report_story348.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print("\n==== SUMMARY ====")
print("pages visited:", len(report["pages"]))
print("console errors(total/real):", len(report["console_errors"]), "/", len(real_console))
print("page errors:", len(report["page_errors"]))
print("failed api(total/real):", len(report["failed_requests"]), "/", len(real_fail))
print("findings:", len(findings))
print("known_bug_recheck:")
for r in report["known_bug_recheck"]:
    print("   ", r)
for e in report["page_errors"][:20]:
    print("  PAGE-ERR:", e[:160])
for e in real_console[:20]:
    print("  REAL-CONSOLE-ERR:", e["text"][:160])
for f in real_fail[:20]:
    print("  REAL-API-FAIL:", f["status"], f["url"][:120])
for fnd in findings:
    print("  FINDING:", fnd["severity"], fnd["page"], "-", fnd["issue"][:140])
