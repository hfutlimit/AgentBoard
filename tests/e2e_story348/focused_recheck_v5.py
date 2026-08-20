"""
AGB Story 348 暖机长等待复核脚本
目的: 在 ng serve 已暖机后, 对 v5 巡检中可疑的空白页/交互假阳性做二次验证。
"""
import sys, os, json, time, urllib.request

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_v5_recheck")
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

from playwright.sync_api import sync_playwright

report = {"checks": [], "console_errors": []}

def wait_for_content(page, timeout_ms=20000, min_len=30):
    """等待 main/body 文本长度 >= min_len, 或 timeout。"""
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        try:
            txtlen = page.evaluate(
                "()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length;}")
            if txtlen >= min_len:
                return txtlen
        except Exception:
            pass
        time.sleep(0.3)
    return page.evaluate(
        "()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length;}")

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    page = ctx.new_page()
    page.set_default_navigation_timeout(60000)
    page.on("console", lambda msg: report["console_errors"].append({"type": msg.type, "text": msg.text}) if msg.type == "error" else None)

    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t => localStorage.setItem('agentboard_token', t)", TOKEN)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("app-root", timeout=20000)
    time.sleep(2)

    # ─── 详情页长等待复核 ───────────────────────────────────────────
    for name, path, min_len in [
        ("story_330", "/story/330", 50),
        ("task_1342", "/task/1342", 20),
        ("task_1339", "/task/1339", 20),
        ("task_1429", "/task/1429", 20),  # 已知存在的新 Bug
    ]:
        page.goto(BASE + path, wait_until="domcontentloaded")
        time.sleep(1)
        txtlen = wait_for_content(page, timeout_ms=20000, min_len=min_len)
        h1 = page.evaluate("()=>{const h=document.querySelector('h1'); return h?h.innerText.trim():'';}")
        is404 = page.evaluate("""()=>{const t=(document.body.innerText||''); return t.includes('页面不存在') || t.includes('404') || !!document.querySelector('.not-found,[class*=not-found],.error-404');}""")
        # 额外: 找任何错误提示
        has_error = page.evaluate("""()=>{const t=(document.body.innerText||''); return t.includes('出错了') || t.includes('加载失败') || t.includes('错误');}""")
        sp = os.path.join(SHOT, f"{name}_recheck.png")
        page.screenshot(path=sp, full_page=False)
        report["checks"].append({
            "name": name, "path": path, "txtlen": txtlen, "h1": h1,
            "is404": bool(is404), "has_error_hint": bool(has_error),
            "screenshot": sp, "blank": txtlen < min_len
        })
        print(f"[{name}] txt={txtlen} h1={h1!r} 404={is404} err_hint={has_error}")

    # ─── 主题切换复核(精确定位太阳/月亮图标) ────────────────────────
    page.goto(BASE + "/", wait_until="domcontentloaded")
    time.sleep(2)
    # 尝试多种选择器: aria-label / SVG use / data-theme 关联按钮
    theme_info = {"tried_selectors": []}
    for sel in [
        'button[aria-label*="主题"]',
        'button[aria-label*="theme"]',
        'button[title*="主题"]',
        'header button:has(svg.sun)',
        'header button:has(svg.moon)',
        '[data-testid="theme-toggle"]',
        'button[class*="theme"]',
    ]:
        el = page.query_selector(sel)
        theme_info["tried_selectors"].append({"sel": sel, "found": el is not None})
        if el:
            before = page.evaluate("()=>{return {theme: document.documentElement.dataset.theme, cls: document.documentElement.className, bodyCls: document.body.className}};")
            el.click(); time.sleep(1.0)
            after = page.evaluate("()=>{return {theme: document.documentElement.dataset.theme, cls: document.documentElement.className, bodyCls: document.body.className}};")
            sp = os.path.join(SHOT, "theme_toggle_recheck.png")
            page.screenshot(path=sp, full_page=False)
            theme_info.update({"selector_used": sel, "before": before, "after": after, "screenshot": sp})
            print(f"[theme] sel={sel} before={before} after={after}")
            break
    else:
        # 如果没找到, 拍张 header 特写看有什么按钮
        sp = os.path.join(SHOT, "theme_toggle_notfound.png")
        page.screenshot(path=sp, full_page=False)
        theme_info["screenshot"] = sp
    report["checks"].append({"name": "theme_toggle", "info": theme_info})

    # ─── 新建项目弹窗复核 ────────────────────────────────────────────
    page.goto(BASE + "/projects", wait_until="domcontentloaded")
    time.sleep(2)
    dialog_info = {}
    for sel in ["button:has-text('新建项目')", "button:has-text('+ 新建项目')", "[data-testid='new-project-btn']"]:
        el = page.query_selector(sel)
        dialog_info[f"sel_{sel}"] = el is not None
        if el:
            el.click(); time.sleep(1.2)
            break
    # 检查弹窗 / overlay
    modal_sel = '.modal-overlay,.modal,[role=dialog],.cdk-overlay-container,.dialog,.create-project-modal,.new-project-modal'
    modal = page.query_selector(modal_sel)
    dialog_info["modal_found"] = modal is not None
    dialog_info["modal_html"] = page.evaluate("""(sel)=>{const m=document.querySelector(sel); return m ? m.outerHTML.slice(0,800) : ''}""", modal_sel)
    sp = os.path.join(SHOT, "create_project_recheck.png")
    page.screenshot(path=sp, full_page=False)
    dialog_info["screenshot"] = sp
    report["checks"].append({"name": "create_dialog", "info": dialog_info})
    print(f"[create_dialog] {dialog_info}")

    browser.close()

with open(os.path.join(OUT, "focused_recheck_v5.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print("\n==== RECHECK DONE ====")
for c in report["checks"]:
    print(c)
