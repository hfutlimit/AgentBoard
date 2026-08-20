"""
Epic 149 自动化 E2E 验证 — Story 318 (阶段2 侧边栏+ManagedList) 与 Story 319 (阶段3 八视图迁移)。

环境：
  本地 Angular 源码（含 Story 318/319 提交）经 ng serve 跑在 127.0.0.1:4200，
  /api /ws 经 proxy.conf.json 转发到生产后端（持有 AGB 项目 3 / Epic 149 数据）。
  浏览器只与 127.0.0.1:4200 同源通信，不受生产 CORS 限制。

判定严格基于真实运行结果，不臆造。

导航原则：首屏用 page.goto 加载 /project/3；之后所有视图切换均经 SPA 内点击
.project-nav-button-v7 按钮（E2E 不得 page.goto 整页刷新）。
"""
import json
import os
import time
import urllib.request

import pytest
try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - collected without E2E extras
    sync_playwright = None

WEB = "http://127.0.0.1:4200"
API = "http://124.220.44.12"
USER = os.environ.get("AGENTBOARD_E2E_USER", "admin")
PASS = os.environ.get("AGENTBOARD_E2E_PASS", "admin123")
PROJECT_ID = 3
EPIC_ID = 149

OUT = os.path.dirname(os.path.abspath(__file__))
SHOT_318 = os.path.join(OUT, "screenshots", "318")
SHOT_319 = os.path.join(OUT, "screenshots", "319")
os.makedirs(SHOT_318, exist_ok=True)
os.makedirs(SHOT_319, exist_ok=True)

# navy #10243e -> rgb(16,36,62)
NAVY = "rgb(16, 36, 62)"
# 外壳层字符图标残留判定（仅限 shell 区域，避免内部视图「⚙设置」等误报）
SHELL_CHARS = ["▦", "◇", "⚙", "▤", "▪", "▫"]

# 侧边栏 8 项导航：(中文标签, tab 名, 期望渲染的视图组件 selector)
NAV_ITEMS = [
    ("概览", "overview", "app-overview-tab"),
    ("看板", "kanban", "app-kanban-tab"),
    ("Epics", "epics", "app-epics-tab"),
    ("工作项", "backlog", "app-backlog-tab"),
    ("提案", "proposals", "app-proposals-tab"),
    ("文档", "documents", "app-documents-tab"),
    ("成员与 Agents", "members", "app-members-tab"),
    ("设置", "settings", None),
]

# Story 318：5 个列表视图应使用 ManagedListComponent
LIST_TABS = ["epics", "backlog", "proposals", "documents", "members"]


def login():
    req = urllib.request.Request(
        API + "/api/auth/login",
        data=json.dumps({"username": USER, "password": PASS}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["token"]


def shell_char_scan(page):
    """仅在外壳区域（project-sidebar-v7 + header/topbar）扫描字符图标残留。"""
    return page.evaluate(
        """(CHARS) => {
            const shellSel = 'aside.project-sidebar-v7, header, .topbar, [class*=topbar], [class*=shell]';
            const roots = Array.from(document.querySelectorAll(shellSel));
            const hits = [];
            for (const ch of CHARS) {
                let n = 0; const samples = [];
                for (const root of roots) {
                    const all = root.querySelectorAll('*');
                    for (const el of all) {
                        if (el.children && el.children.length > 3) continue;
                        const t = (el.textContent || '').trim();
                        if (!t || t.length > 60) continue;
                        if (t.includes(ch)) {
                            n++;
                            if (samples.length < 3) samples.push(t.slice(0, 60));
                        }
                    }
                }
                if (n > 0) hits.push({ch, count: n, samples});
            }
            return {hits, shellRegions: roots.length};
        }""",
        SHELL_CHARS,
    )


def main():
    token = login()
    print("[login] token len=%d" % len(token))

    console_errors = []   # type == 'error'
    console_warnings = []  # type == 'warning'
    page_errors = []

    rep318 = {
        "story": 318,
        "title": "阶段2 侧边栏 + managed-list 抽象",
        "project_id": PROJECT_ID,
        "web": WEB, "api": API,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "checks": {}, "screenshots": [], "issues": [], "verdict": "UNKNOWN",
    }
    rep319 = {
        "story": 319,
        "title": "阶段3 视图逐个迁移（八视图）",
        "project_id": PROJECT_ID,
        "web": WEB, "api": API,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "views": [], "screenshots": [], "issues": [], "verdict": "UNKNOWN",
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: (
            console_errors.append({"type": m.type, "text": m.text})
            if m.type == "error" else
            console_warnings.append({"type": m.type, "text": m.text})
        ))
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        page.add_init_script(
            "localStorage.setItem('agentboard_token','%s');"
            "localStorage.setItem('agentboard_user','admin');" % token
        )

        # 首屏加载（允许一次 page.goto）
        t0 = time.time()
        page.goto(WEB + "/project/%d" % PROJECT_ID, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector("aside.project-sidebar-v7", timeout=30000)
        except Exception as e:
            rep318["issues"].append("侧边栏 project-sidebar-v7 未渲染: %s" % e)
            rep319["issues"].append("侧边栏未渲染，无法进入视图验证: %s" % e)
            # 仍截图当前状态
            page.screenshot(path=os.path.join(SHOT_318, "fatal_no_sidebar.png"))
            browser.close()
            _finalize(rep318, rep319, console_errors, console_warnings, page_errors, OUT)
            return rep318, rep319
        load_ms = int((time.time() - t0) * 1000)
        print("[load] /project/%d ready in %dms" % (PROJECT_ID, load_ms))

        # ============ Story 318 检查 ============
        # 1) 侧边栏存在 + 8 项导航 + 标签
        c = page.evaluate(
            """() => {
                const aside = document.querySelector('aside.project-sidebar-v7');
                if (!aside) return {present:false};
                const btns = Array.from(aside.querySelectorAll('.project-nav-button-v7'));
                const labels = btns.map(b => (b.innerText||'').replace(/\\s+/g,' ').trim());
                const hp = !!aside.querySelector('.health-pulse-v7');
                const bg = getComputedStyle(aside).backgroundColor;
                return {present:true, navCount:btns.length, labels, healthPulse:hp,
                        sidebarBg:bg, asideClass:aside.className};
            }"""
        )
        rep318["checks"]["sidebar"] = c
        shot = os.path.join(SHOT_318, "01_sidebar.png")
        page.screenshot(path=shot, full_page=False)
        rep318["screenshots"].append(shot)
        print("[318] sidebar present=%s navCount=%s healthPulse=%s bg=%s"
              % (c.get("present"), c.get("navCount"), c.get("healthPulse"), c.get("sidebarBg")))

        # 2) 外壳字符图标残留（shell 区域）
        sc = shell_char_scan(page)
        rep318["checks"]["shell_char_icons"] = sc
        if sc["hits"]:
            rep318["issues"].append("外壳层检测到字符图标残留: %s" % json.dumps(sc["hits"], ensure_ascii=False))

        # 3) 5 个列表视图是否使用 ManagedListComponent
        ml_result = {}
        for tab in LIST_TABS:
            # 找对应按钮点击
            try:
                page.click('.project-nav-button-v7:has-text("%s")' % _nav_label_for(tab), timeout=4000)
            except Exception:
                # fallback：按 tab 名匹配
                page.click('.project-nav-button-v7:has-text("%s")' % tab, timeout=4000)
            page.wait_for_timeout(1800)
            info = page.evaluate(
                """() => {
                    const main = document.querySelector('#app') || document.body;
                    return {
                        managedListCount: main.querySelectorAll('app-managed-list').length,
                        svgUse: main.querySelectorAll('svg use').length,
                        mainTextLen: (main.innerText||'').length,
                    };
                }"""
            )
            ml_result[tab] = info
            shot = os.path.join(SHOT_318, "list_%s.png" % tab)
            page.screenshot(path=shot, full_page=False)
            rep318["screenshots"].append(shot)
            print("[318] list tab=%s managedList=%s svgUse=%s textLen=%s"
                  % (tab, info["managedListCount"], info["svgUse"], info["mainTextLen"]))
        rep318["checks"]["managed_list_usage"] = ml_result
        # ManagedList 期望在 epics/proposals/documents 至少出现；backlog 合并 workitems 也应走 managed-list
        for tab in ["epics", "proposals", "documents", "backlog", "members"]:
            if ml_result.get(tab, {}).get("managedListCount", 0) == 0:
                rep318["issues"].append("列表视图 %s 未检测到 app-managed-list 组件" % tab)

        # 4) 两个 popover：项目切换器 + 通知面板
        pop = {}
        # project switcher
        try:
            page.click(".project-switcher-button-v7", timeout=4000)
            page.wait_for_timeout(800)
            ps = page.evaluate(
                """() => {
                    const el = document.querySelector('.project-switcher-v7');
                    if(!el) return {visible:false, reason:'no element'};
                    const cs = getComputedStyle(el);
                    const visible = cs.display!=='none' && cs.visibility!=='hidden' && parseFloat(cs.opacity||'1')>0.1;
                    return {visible, hasSearch: !!el.querySelector('input'),
                            projectItems: el.querySelectorAll('.switcher-project-v7').length};
                }"""
            )
            pop["project_switcher"] = ps
            shot = os.path.join(SHOT_318, "popover_project_switcher.png")
            page.screenshot(path=shot, full_page=False)
            rep318["screenshots"].append(shot)
            # 关闭
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception as e:
            pop["project_switcher"] = {"visible": False, "error": str(e)}
        # notification panel
        try:
            page.click(".notif-btn--bell", timeout=4000)
            page.wait_for_timeout(800)
            np_ = page.evaluate(
                """() => {
                    const el = document.querySelector('.notification-panel-v7');
                    if(!el) return {visible:false, reason:'no element'};
                    const cs = getComputedStyle(el);
                    const visible = cs.display!=='none' && cs.visibility!=='hidden' && parseFloat(cs.opacity||'1')>0.1;
                    return {visible, hasList: !!el.querySelector('.notification-list-v7')};
                }"""
            )
            pop["notification_panel"] = np_
            shot = os.path.join(SHOT_318, "popover_notification.png")
            page.screenshot(path=shot, full_page=False)
            rep318["screenshots"].append(shot)
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception as e:
            pop["notification_panel"] = {"visible": False, "error": str(e)}
        rep318["checks"]["popovers"] = pop
        if not pop.get("project_switcher", {}).get("visible"):
            rep318["issues"].append("项目切换器 popover 未能打开")
        if not pop.get("notification_panel", {}).get("visible"):
            rep318["issues"].append("通知面板 popover 未能打开")

        # ============ Story 319 检查：逐视图验证 ============
        for label, tab, sel in NAV_ITEMS:
            try:
                page.click('.project-nav-button-v7:has-text("%s")' % label, timeout=4000)
            except Exception as e:
                rep319["views"].append({"label": label, "tab": tab, "error": "click failed: %s" % e})
                continue
            page.wait_for_timeout(2000)
            info = page.evaluate(
                """(SEL) => {
                    const main = document.querySelector('#app') || document.body;
                    const sel = SEL ? (main.querySelector(SEL) ? true : false) : null;
                    return {
                        viewSelectorPresent: sel,
                        svgUse: main.querySelectorAll('svg use').length,
                        svgCount: main.querySelectorAll('svg').length,
                        mainTextLen: (main.innerText||'').length,
                        hasTable: main.querySelectorAll('table').length,
                        hasCards: main.querySelectorAll('.card, .kanban-card, .task-card, .metric-card').length,
                    };
                }""",
                sel,
            )
            info["label"] = label
            info["tab"] = tab
            rep319["views"].append(info)
            shot = os.path.join(SHOT_319, "view_%s.png" % tab)
            page.screenshot(path=shot, full_page=False)
            rep319["screenshots"].append(shot)
            if tab == "members":
                # members 视图：持久 tab strip 约 146 字符，真实成员列表应远超；
                # 严格判定（避免被持久页头拉高误判通过）：主内容区文本须远超持久条。
                ok = info["mainTextLen"] > 300
            else:
                ok = info["mainTextLen"] > 50 and (sel is None or info["viewSelectorPresent"])
            print("[319] view=%s textLen=%s svgUse=%s selector(%s)=%s -> %s"
                  % (tab, info["mainTextLen"], info["svgUse"], sel, info["viewSelectorPresent"], "OK" if ok else "THIN"))
            if not ok:
                rep319["issues"].append("视图 %s 渲染异常（文本过少或组件未挂载）" % tab)

        browser.close()

    _finalize(rep318, rep319, console_errors, console_warnings, page_errors, OUT)
    return rep318, rep319


@pytest.mark.e2e
@pytest.mark.legacy
@pytest.mark.skip(reason="legacy manual E2E; run this file directly")
def test_story318_319_legacy_e2e() -> None:
    """Collect the legacy Stories 318/319 script without running it by default."""
    reports = main()
    assert all(report.get("verdict") == "PASS" for report in reports), reports


def _nav_label_for(tab):
    for label, t, _ in NAV_ITEMS:
        if t == tab:
            return label
    return tab


def _finalize(rep318, rep319, console_errors, console_warnings, page_errors, out):
    # 同步错误快照
    for rep in (rep318, rep319):
        rep["console_errors"] = console_errors
        rep["console_warnings"] = console_warnings
        rep["page_errors"] = page_errors

    # Story 318 判定
    fatal318 = len(page_errors) > 0 or len(console_errors) > 0
    if rep318["issues"]:
        rep318["verdict"] = "FAIL"
    elif fatal318:
        rep318["verdict"] = "FAIL"
        rep318["issues"].append("运行期报错：page_errors=%d console_errors=%d" % (len(page_errors), len(console_errors)))
    else:
        rep318["verdict"] = "PASS"

    # Story 319 判定
    if rep319["issues"]:
        rep319["verdict"] = "FAIL"
    elif fatal318:
        rep319["verdict"] = "FAIL"
        rep319["issues"].append("运行期报错：page_errors=%d console_errors=%d" % (len(page_errors), len(console_errors)))
    else:
        rep319["verdict"] = "PASS"

    for rep, name in ((rep318, "report_story318"), (rep319, "report_story319")):
        with open(os.path.join(out, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        _write_md(rep, os.path.join(out, name + ".md"))

    print('=== 318 VERDICT:', rep318['verdict'], '| issues:', len(rep318['issues']), '===')
    print('=== 319 VERDICT:', rep319['verdict'], '| issues:', len(rep319['issues']), '===')
    print("page_errors=%d console_errors=%d warnings=%d"
          % (len(page_errors), len(console_errors), len(console_warnings)))


def _write_md(rep, path):
    v = rep["verdict"]
    lines = [
        "# Epic 149 Story %d 自动化 E2E 验证报告" % rep["story"],
        "",
        "**标题**: %s" % rep["title"],
        "**验证时间**: %s" % rep["started_at"],
        "**环境**: 本地 ng serve %s（/api 代理生产后端 %s），项目 %d" % (rep["web"], rep["api"], rep["project_id"]),
        "**结论**: **%s**" % v,
        "",
        "## 运行期错误",
        "- page_errors（致命）: %d" % len(rep.get("page_errors", [])),
        "- console errors: %d" % len(rep.get("console_errors", [])),
        "- console warnings: %d" % len(rep.get("console_warnings", [])),
        "",
    ]
    if rep["story"] == 318:
        lines += ["## Story 318 检查项", ""]
        s = rep["checks"].get("sidebar", {})
        lines.append("- 侧边栏 present=%s, 导航项=%s, 标签=%s" % (s.get("present"), s.get("navCount"), s.get("labels")))
        lines.append("- health-pulse footer: %s" % s.get("healthPulse"))
        lines.append("- 侧边栏背景色: %s" % s.get("sidebarBg"))
        ml = rep["checks"].get("managed_list_usage", {})
        lines.append("- ManagedListComponent 使用: " + ", ".join("%s=%s" % (k, v2.get("managedListCount")) for k, v2 in ml.items()))
        pop = rep["checks"].get("popovers", {})
        lines.append("- 项目切换器 popover: %s" % pop.get("project_switcher"))
        lines.append("- 通知面板 popover: %s" % pop.get("notification_panel"))
        sc = rep["checks"].get("shell_char_icons", {})
        lines.append("- 外壳字符图标残留: %s" % (sc.get("hits") or "无"))
    else:
        lines += ["## Story 319 视图验证", ""]
        for vw in rep["views"]:
            lines.append("- %s (tab=%s): textLen=%s svgUse=%s selector=%s"
                         % (vw.get("label"), vw.get("tab"), vw.get("mainTextLen"),
                            vw.get("svgUse"), vw.get("viewSelectorPresent")))
    lines += ["", "## 截图", ""]
    for s in rep["screenshots"]:
        lines.append("- %s" % s)
    if rep["issues"]:
        lines += ["", "## 问题清单", ""]
        for i in rep["issues"]:
            lines.append("- %s" % i)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
