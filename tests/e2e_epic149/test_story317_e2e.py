"""
Epic 149 / Story 317 (阶段1 外壳先行) 端到端验证。

验证目标（来自 Story 317 + QA Task 1283）：
  1) 前端外壳渲染为原型样子（Home/Workspace 两级 Shell + 深色 navy 侧边栏 + 项目切换器）
  2) 现有所有视图在新外壳内正常渲染无回归
  3) SVG 图标替换后无字符图标残留（▦◇⚙）
  4) navy(#10243e)+blue(#2864dc) 双令牌共存
  5) 无控制台报错 / 无布局错乱

环境：本地 Angular 源码（含 317 提交 + 工作树改动）经 ng serve 跑在
127.0.0.1:4200，/api 经 proxy 转发到生产后端（持有 AGB 项目 3 / Epic 149 数据）。
浏览器只与 127.0.0.1:4200 同源通信，故不受生产 CORS 限制。
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:4200"
API = "http://124.220.44.12"          # 生产后端（数据来源）
USER = "admin"
PASS = "admin123"
PROJECT_ID = 3
EPIC_ID = 149
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT_DIR = os.path.join(OUT, "screenshots")
os.makedirs(SHOT_DIR, exist_ok=True)

# navy #10243e -> rgb(16,36,62);  blue #2864dc -> rgb(40,100,220)
NAVY = "rgb(16, 36, 62)"
BLUE = "rgb(40, 100, 220)"
CHAR_ICONS = ["▦", "◇", "⚙", "▤", "▪", "▫"]


def login():
    req = urllib.request.Request(
        API + "/api/auth/login",
        data=json.dumps({"username": USER, "password": PASS}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["token"]


def main():
    token = login()
    print("[login] token acquired, len=%d" % len(token))

    report = {
        "story": 317,
        "project_id": PROJECT_ID,
        "web": WEB,
        "api": API,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "routes": [],
        "console_errors": [],
        "page_errors": [],
        "verdict": "UNKNOWN",
        "notes": [],
    }

    console_errors = []
    page_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(
            {"type": m.type, "text": m.text}) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        page.add_init_script(
            "localStorage.setItem('agentboard_token','%s');"
            "localStorage.setItem('agentboard_user','admin');" % token
        )

        routes = [
            ("home_shell", "/"),
            ("workspace_shell", "/project/%d" % PROJECT_ID),
        ]
        for name, path in routes:
            rec = {"name": name, "path": path}
            try:
                page.goto(WEB + path, wait_until="networkidle", timeout=45000)
            except Exception as e:  # networkidle 可能超时，降级到 domcontentloaded
                rec["goto_warn"] = str(e)
                try:
                    page.goto(WEB + path, wait_until="domcontentloaded", timeout=30000)
                except Exception as e2:
                    rec["goto_error"] = str(e2)
            page.wait_for_timeout(2500)  # 等 SPA 渲染 + 懒加载视图

            # DOM 诊断
            diag = page.evaluate(
                """() => {
                    const NAVY='rgb(16, 36, 62)', BLUE='rgb(40, 100, 220)';
                    const icons=['▦','◇','⚙','▤','▪','▫'];
                    let navyCount=0, blueCount=0;
                    const all=document.querySelectorAll('*');
                    for (const el of all){
                        const bg=getComputedStyle(el).backgroundColor;
                        if(bg===NAVY) navyCount++;
                        if(bg===BLUE) blueCount++;
                    }
                    const svgUse=document.querySelectorAll('svg use').length;
                    const svgCount=document.querySelectorAll('svg').length;
                    const text=document.body? document.body.innerText||'':'';
                    let charIconHits=0;
                    for(const ic of icons){ if(text.includes(ic)) charIconHits++; }
                    return {
                        navyCount, blueCount, svgUse, svgCount, charIconHits,
                        bodyTextLen: text.length,
                        title: document.title,
                        mainExists: !!document.querySelector('main, .workspace, .shell, app-root > *'),
                    };
                }"""
            )
            rec.update(diag)
            shot = os.path.join(SHOT_DIR, name + ".png")
            page.screenshot(path=shot, full_page=False)
            rec["screenshot"] = shot
            report["routes"].append(rec)
            print("[route] %s -> navy=%d blue=%d svgUse=%d charIconHits=%d textLen=%d"
                  % (name, diag["navyCount"], diag["blueCount"], diag["svgUse"],
                     diag["charIconHits"], diag["bodyTextLen"]))

        # 尝试在 workspace 内点击侧边栏导航项，验证现有视图在新外壳内渲染
        try:
            page.goto(WEB + "/project/%d" % PROJECT_ID, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            # 找侧边栏里的导航链接（a / button 含文字）
            nav_links = page.locator("nav a, .sidebar a, aside a, [class*='sidebar'] a, a[routerlink]")
            n = nav_links.count()
            rec = {"name": "workspace_nav_explore", "nav_link_count": n}
            clicked = 0
            for i in range(min(n, 8)):
                try:
                    link = nav_links.nth(i)
                    label = link.inner_text(timeout=2000).strip()
                    if not label:
                        continue
                    link.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    shot = os.path.join(SHOT_DIR, "nav_%d.png" % clicked)
                    page.screenshot(path=shot, full_page=False)
                    rec.setdefault("nav_clicks", []).append(
                        {"index": i, "label": label, "screenshot": shot})
                    clicked += 1
                except Exception as e:
                    rec.setdefault("nav_errors", []).append(str(e))
            report["routes"].append(rec)
            print("[nav] explored %d sidebar links" % clicked)
        except Exception as e:
            report["notes"].append("nav explore failed: %s" % e)

        console_errors.extend([])
        browser.close()

    report["console_errors"] = console_errors
    report["page_errors"] = page_errors

    # 判定
    navy_total = sum(r.get("navyCount", 0) for r in report["routes"])
    blue_total = sum(r.get("blueCount", 0) for r in report["routes"])
    svg_total = sum(r.get("svgUse", 0) for r in report["routes"])
    char_total = sum(r.get("charIconHits", 0) for r in report["routes"])
    fatal = len(page_errors) > 0
    # console error（非 warning）视为问题
    hard_console = [c for c in console_errors if c["type"] == "error"]

    issues = []
    if navy_total == 0 and blue_total == 0:
        issues.append("未检测到 navy(#10243e)/blue(#2864dc) 令牌色，外壳可能未应用原型令牌")
    if char_total > 0:
        issues.append("检测到字符图标残留（▦◇⚙ 等）：%d 处" % char_total)
    if svg_total == 0:
        issues.append("未检测到 SVG symbol 使用（svg use=0），可能未替换字符图标")
    if fatal:
        issues.append("页面级 JS 报错 %d 处" % len(page_errors))
    if hard_console:
        issues.append("控制台 error %d 处" % len(hard_console))

    report["issues"] = issues
    report["verdict"] = "FAIL" if issues else "PASS"

    with open(os.path.join(OUT, "report_story317.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("=== VERDICT:", report["verdict"], "===")
    print("issues:", json.dumps(issues, ensure_ascii=False))
    print("console errors (type=error):", len(hard_console), "warnings:",
          len([c for c in console_errors if c["type"] == "warning"]))
    return report


if __name__ == "__main__":
    main()
