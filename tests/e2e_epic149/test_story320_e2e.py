"""
Epic 149 自动化 E2E 验证 — Story 320（阶段4 色板收口：indigo→navy 统一 + 删旧令牌 + 补暗色主题）。

环境：
  本地 Angular 源码（含 Story 320 提交 2973418）经 ng serve 跑在 127.0.0.1:4200，
  /api /ws 经 proxy.conf.json 转发到生产后端（持有 AGB 项目 3 / Epic 149 数据）。
  浏览器只与 127.0.0.1:4200 同源通信，不受生产 CORS 限制。

判定严格基于真实运行结果，不臆造。

验证范围（仅限 Story 320 自身 scope）：
  1. 品牌色板收口：外壳/内容区主色应为 navy(#10243e=rgb(16,36,62))/blue(#2864dc)，
     不应残留 indigo(#4f46e5=rgb(79,70,229)/#4338ca=rgb(67,56,202)/#3730a3=rgb(55,48,163)/#818cf8=rgb(129,140,248))。
     允许：图表调色板首色 #6366f1=rgb(99,102,241)（区分实体的循环色板，按 F9 规则保留）+ favicon。
  2. 暗色主题：.theme-toggle 可切换，切换后表层（卡片/侧边栏）背景变暗、对比度提升。
  3. 通用稳定性：8 视图（除已知 members 空白 Bug #1290 外）渲染正常、0 控制台/页面错误。

注意：members 视图空白为跨 Story 已知缺陷（Bug #1290，属 318/319 scope），不在 320 scope 内，
本脚本单独记录、不计入 320 判定，避免误关 320。
"""
import json
import os
import time
import urllib.request

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:4200"
API = "http://124.220.44.12"
USER = os.environ.get("AGENTBOARD_E2E_USER", "admin")
PASS = os.environ.get("AGENTBOARD_E2E_PASS", "admin123")
PROJECT_ID = 3
EPIC_ID = 149

OUT = os.path.dirname(os.path.abspath(__file__))
SHOT_320 = os.path.join(OUT, "screenshots", "320")
os.makedirs(SHOT_320, exist_ok=True)

# navy #10243e
NAVY = (16, 36, 62)
# indigo 家族（应已迁移到 navy）
INDIGO_RGB = {
    (79, 70, 229),   # #4f46e5
    (67, 56, 202),   # #4338ca
    (55, 48, 163),   # #3730a3
    (129, 140, 248), # #818cf8
}
# 图表调色板首色（按 F9 规则保留，不计入 indigo 缺陷）
CHART_PALETTE_RGB = {
    (99, 102, 241),  # #6366f1
}

NAV_ITEMS = [
    ("概览", "overview", "app-overview-tab"),
    ("看板", "kanban", "app-kanban-tab"),
    ("Epics", "epics", "app-epics-tab"),
    ("工作项", "backlog", "app-backlog-tab"),
    ("提案", "proposals", "app-proposals-tab"),
    ("文档", "documents", "app-documents-tab"),
    ("成员与 Agents", "members", None),
    ("设置", "settings", None),
]

KNOWN_ISSUE_TABS = {"members"}  # Bug #1290，跨 Story，不计入 320 判定


def login():
    req = urllib.request.Request(
        API + "/api/auth/login",
        data=json.dumps({"username": USER, "password": PASS}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["token"]


def parse_rgb(s):
    s = (s or "").strip()
    if not s.startswith("rgb"):
        return None
    nums = "".join(c if c.isdigit() or c in ".,-" else " " for c in s)
    parts = [p for p in nums.split() if p.replace(".", "", 1).lstrip("-").isdigit()]
    if len(parts) >= 3:
        try:
            return (int(float(parts[0])), int(float(parts[1])), int(float(parts[2])))
        except ValueError:
            return None
    return None


def indigo_scan(page):
    """扫描外壳+内容区计算样式中的 indigo 残留（排除 svg/canvas 图表与 favicon）。"""
    return page.evaluate(
        """(ARGS) => {
            const indigo = ARGS[0].map(a => a.join(','));
            const chart = ARGS[1].map(a => a.join(','));
            const hits = [];
            const all = Array.from(document.querySelectorAll('body *'));
            for (const el of all) {
                const tag = (el.tagName || '').toLowerCase();
                if (tag === 'svg' || tag === 'path' || tag === 'rect' || tag === 'circle' || tag === 'line' || tag === 'polygon') continue;
                // 跳过 svg 内部的任何元素
                if (el.closest && el.closest('svg')) continue;
                // 跳过 canvas（图表位图）
                if (tag === 'canvas') continue;
                const cs = getComputedStyle(el);
                const bg = cs.backgroundColor; const col = cs.color;
                const bgRgb = bg.startsWith('rgb') ? bg.replace(/rgb\\(|\\)|\\s/g,''):'';
                const colRgb = col.startsWith('rgb') ? col.replace(/rgb\\(|\\)|\\s/g,''):'';
                let matched = null;
                if (indigo.includes(bgRgb)) matched = {prop:'backgroundColor', val:bg};
                else if (indigo.includes(colRgb)) matched = {prop:'color', val:col};
                else if (chart.includes(bgRgb)) matched = {prop:'backgroundColor(CHART)', val:bg};
                else if (chart.includes(colRgb)) matched = {prop:'color(CHART)', val:col};
                if (matched) {
                    const txt = (el.textContent||'').trim().slice(0,40);
                    const cls = (el.className && el.className.toString) ? el.className.toString().slice(0,60) : '';
                    const insideChart = !!(el.closest && (el.closest('[class*=chart]') || el.closest('[class*=Chart]')));
                    hits.push({tag, cls, text:txt, ...matched, insideChart});
                }
            }
            return {hits, scanned: all.length};
        }""",
        [[list(x) for x in INDIGO_RGB], [list(x) for x in CHART_PALETTE_RGB]],
    )


def main():
    token = login()
    print("[login] token len=%d" % len(token))

    console_errors = []
    console_warnings = []
    page_errors = []

    rep = {
        "story": 320,
        "title": "阶段4 色板收口：indigo→navy 统一 + 删旧令牌 + 补暗色主题",
        "project_id": PROJECT_ID,
        "epic_id": EPIC_ID,
        "web": WEB, "api": API,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "checks": {}, "views": [], "screenshots": [], "issues": [],
        "known_issues": [], "verdict": "UNKNOWN",
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

        t0 = time.time()
        page.goto(WEB + "/project/%d" % PROJECT_ID, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector("aside.project-sidebar-v7", timeout=30000)
        except Exception as e:
            rep["issues"].append("侧边栏未渲染，无法验证: %s" % e)
            page.screenshot(path=os.path.join(SHOT_320, "fatal_no_sidebar.png"))
            browser.close()
            _finalize(rep, console_errors, console_warnings, page_errors, OUT)
            return rep
        load_ms = int((time.time() - t0) * 1000)
        rep["checks"]["load_ms"] = load_ms
        print("[load] /project/%d ready in %dms" % (PROJECT_ID, load_ms))

        # 1) 外壳 palette：侧边栏背景应为 navy
        sb = page.evaluate(
            """() => {
                const aside = document.querySelector('aside.project-sidebar-v7');
                const cs = getComputedStyle(aside);
                const brand = document.querySelector('.brand-mark-v7, .brand-mark');
                return {
                    sidebarBg: cs.backgroundColor,
                    sidebarBgRgb: (cs.backgroundColor.match(/\\d+/g)||[]).slice(0,3).map(Number),
                    brandColor: brand ? getComputedStyle(brand).color : null,
                };
            }"""
        )
        rep["checks"]["shell_palette"] = sb
        shot = os.path.join(SHOT_320, "01_shell_light.png")
        page.screenshot(path=shot, full_page=False)
        rep["screenshots"].append(shot)
        if tuple(sb.get("sidebarBgRgb") or [0, 0, 0]) != NAVY:
            rep["issues"].append("侧边栏背景非 navy %s，实际 %s" % (NAVY, sb.get("sidebarBg")))
        print("[320] sidebar bg=%s" % sb.get("sidebarBg"))

        # 2) indigo 残留扫描（亮色主题下）
        sc = indigo_scan(page)
        rep["checks"]["indigo_scan_light"] = {
            "scanned": sc["scanned"],
            "indigo_hits": [h for h in sc["hits"] if "CHART" not in h["prop"]],
            "chart_hits": [h for h in sc["hits"] if "CHART" in h["prop"]],
        }
        indigo_hits = [h for h in sc["hits"] if "CHART" not in h["prop"]]
        rep["checks"]["indigo_scan_light"]["indigo_count"] = len(indigo_hits)
        if indigo_hits:
            rep["issues"].append("亮色主题检测到 indigo 残留 %d 处: %s"
                                  % (len(indigo_hits), json.dumps(indigo_hits[:10], ensure_ascii=False)))

        # 3) 暗色主题切换验证
        dt = {}
        try:
            # 记录亮色表层背景
            light_surface = page.evaluate(
                """() => {
                    const card = document.querySelector('.card, .metric-card, .kanban-card');
                    const root = document.documentElement;
                    return {
                        bodyClass: document.body.className,
                        htmlClass: root.className,
                        cardBg: card ? getComputedStyle(card).backgroundColor : null,
                        cardBgVar: getComputedStyle(root).getPropertyValue('--card-bg').trim(),
                    };
                }"""
            )
            # 点击 theme-toggle
            page.click("#theme-toggle", timeout=5000)
            page.wait_for_timeout(1200)
            dark_surface = page.evaluate(
                """() => {
                    const card = document.querySelector('.card, .metric-card, .kanban-card');
                    const root = document.documentElement;
                    const bodyBg = getComputedStyle(document.body).backgroundColor;
                    return {
                        bodyClass: document.body.className,
                        htmlClass: root.className,
                        cardBg: card ? getComputedStyle(card).backgroundColor : null,
                        cardBgVar: getComputedStyle(root).getPropertyValue('--card-bg').trim(),
                        bodyBg: bodyBg,
                    };
                }"""
            )
            dt = {"light": light_surface, "dark": dark_surface}
            # 判定暗色是否生效：表层背景变暗（R+G+B 明显更低）或出现 dark class
            def lum(rgb_str):
                if not rgb_str or not rgb_str.startswith("rgb"):
                    return 765  # 白底最大亮度，作为“无背景”占位
                nums = "".join(c if (c.isdigit() or c == ",") else "" for c in rgb_str)
                parts = [int(x) for x in nums.split(",")[:3]]
                return sum(parts)
            light_lum = lum(light_surface.get("cardBg"))
            dark_lum = lum(dark_surface.get("cardBg"))
            dark_active = (dark_lum < light_lum - 30) or ("dark" in (dark_surface.get("htmlClass") or "").lower()) or ("dark" in (dark_surface.get("bodyClass") or "").lower())
            dt["dark_active"] = dark_active
            dt["light_lum"] = light_lum
            dt["dark_lum"] = dark_lum
            shot = os.path.join(SHOT_320, "02_shell_dark.png")
            page.screenshot(path=shot, full_page=False)
            rep["screenshots"].append(shot)
            # 切回亮色
            page.click("#theme-toggle", timeout=5000)
            page.wait_for_timeout(800)
            if not dark_active:
                rep["issues"].append("暗色主题切换未生效（cardBg 亮度 %s→%s，无 dark class）" % (light_lum, dark_lum))
            print("[320] dark theme active=%s lum %s->%s" % (dark_active, light_lum, dark_lum))
        except Exception as e:
            dt["error"] = str(e)
            rep["issues"].append("暗色主题切换异常: %s" % e)
        rep["checks"]["dark_theme"] = dt

        # 4) 逐视图渲染验证（8 视图）
        for label, tab, sel in NAV_ITEMS:
            try:
                page.click('.project-nav-button-v7:has-text("%s")' % label, timeout=4000)
            except Exception as e:
                rep["views"].append({"label": label, "tab": tab, "error": "click failed: %s" % e})
                continue
            page.wait_for_timeout(2000)
            info = page.evaluate(
                """(SEL) => {
                    const main = document.querySelector('#app') || document.body;
                    const selHit = SEL ? !!main.querySelector(SEL) : null;
                    return {
                        viewSelectorPresent: selHit,
                        svgUse: main.querySelectorAll('svg use').length,
                        mainTextLen: (main.innerText||'').length,
                        hasTable: main.querySelectorAll('table').length,
                        hasCards: main.querySelectorAll('.card, .kanban-card, .task-card, .metric-card').length,
                    };
                }""",
                sel,
            )
            info["label"] = label
            info["tab"] = tab
            rep["views"].append(info)
            shot = os.path.join(SHOT_320, "view_%s.png" % tab)
            page.screenshot(path=shot, full_page=False)
            rep["screenshots"].append(shot)

            if tab in KNOWN_ISSUE_TABS:
                # members 空白为已知 Bug #1290（managedList=0，无 @if 渲染块），单独记录，不计入 320 判定
                rep["known_issues"].append(
                    "成员与 Agents 视图主内容区空白（已知 Bug #1290，跨 Story，不在 320 scope）：textLen=%s" % info["mainTextLen"]
                )
                continue

            ok = info["mainTextLen"] > 50 and (sel is None or info["viewSelectorPresent"])
            if not ok:
                rep["issues"].append("视图 %s 渲染异常（文本过少或组件未挂载）" % tab)
            print("[320] view=%s textLen=%s sel=%s -> %s" % (tab, info["mainTextLen"], info["viewSelectorPresent"], "OK" if ok else "THIN"))

        browser.close()

    _finalize(rep, console_errors, console_warnings, page_errors, OUT)
    return rep


def _finalize(rep, console_errors, console_warnings, page_errors, out):
    rep["console_errors"] = console_errors
    rep["console_warnings"] = console_warnings
    rep["page_errors"] = page_errors

    fatal = len(page_errors) > 0 or len(console_errors) > 0
    if rep["issues"]:
        rep["verdict"] = "FAIL"
    elif fatal:
        rep["verdict"] = "FAIL"
        rep["issues"].append("运行期报错：page_errors=%d console_errors=%d" % (len(page_errors), len(console_errors)))
    else:
        rep["verdict"] = "PASS"

    with open(os.path.join(out, "report_story320.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    _write_md(rep, os.path.join(out, "report_story320.md"))

    print('=== 320 VERDICT:', rep['verdict'], '| issues:', len(rep['issues']), '| known:', len(rep['known_issues']), '===')
    print("page_errors=%d console_errors=%d warnings=%d"
          % (len(page_errors), len(console_errors), len(console_warnings)))


def _write_md(rep, path):
    v = rep["verdict"]
    lines = [
        "# Epic 149 Story 320 自动化 E2E 验证报告",
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
        "## Story 320 检查项（色板收口 scope）",
        "",
        "### 外壳 palette",
        "- 侧边栏背景: %s（期望 navy rgb(16,36,62)）" % rep["checks"].get("shell_palette", {}).get("sidebarBg"),
        "",
        "### 暗色主题切换",
        "- dark_active: %s" % rep["checks"].get("dark_theme", {}).get("dark_active"),
        "- 亮度(亮→暗): %s → %s" % (rep["checks"].get("dark_theme", {}).get("light_lum"), rep["checks"].get("dark_theme", {}).get("dark_lum")),
        "",
        "### indigo 残留扫描（亮色）",
        "- 扫描节点数: %s" % rep["checks"].get("indigo_scan_light", {}).get("scanned"),
        "- indigo 命中(应=0): %s" % rep["checks"].get("indigo_scan_light", {}).get("indigo_count"),
        "- 图表调色板命中(允许): %s" % len(rep["checks"].get("indigo_scan_light", {}).get("chart_hits", [])),
        "",
        "## 视图渲染",
        "",
    ]
    for vw in rep["views"]:
        lines.append("- %s (tab=%s): textLen=%s svgUse=%s selector=%s"
                     % (vw.get("label"), vw.get("tab"), vw.get("mainTextLen"),
                        vw.get("svgUse"), vw.get("viewSelectorPresent")))
    if rep["known_issues"]:
        lines += ["", "## 已知问题（不在 320 scope，已另立跟踪）", ""]
        for i in rep["known_issues"]:
            lines.append("- %s" % i)
    lines += ["", "## 截图", ""]
    for s in rep["screenshots"]:
        lines.append("- %s" % s)
    if rep["issues"]:
        lines += ["", "## 问题清单（影响 320 判定）", ""]
        for i in rep["issues"]:
            lines.append("- %s" % i)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
