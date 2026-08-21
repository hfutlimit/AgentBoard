"""
Run 32 专项验证：/settings 与 /agents 主内容区空白确认。
- 30s 轮询稳定测量
- 捕获 console errors / failed requests / 实际 DOM 文本与标签
- 截图 + 判断是否为 404 / loading skeleton / 真实空白
"""
import sys, os, json, time, urllib.request
BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_verify_run32")
os.makedirs(SHOT, exist_ok=True)

def login():
    req = urllib.request.Request(BASE + "/api/auth/login",
        data=json.dumps({"username":"admin","password":"admin123"}).encode(),
        headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["token"]
TOKEN = login()

from playwright.sync_api import sync_playwright

def settle_full(page, path, max_wait=30):
    page.goto(BASE + path, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("app-root", timeout=15000)
    console_errs = []
    failed_reqs = []
    page.on("console", lambda msg: console_errs.append({"type":msg.type,"text":msg.text}) if msg.type in ("error","warning") else None)
    page.on("pageerror", lambda err: console_errs.append({"type":"pageerror","text":str(err)}))
    page.on("response", lambda resp: failed_reqs.append({"url":resp.url,"status":resp.status}) if resp.status >= 400 else None)
    start = time.time(); last = -1; stable_count = 0; readings = []
    while time.time() - start < max_wait:
        txt = page.evaluate("()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length;}")
        readings.append(txt)
        if txt == last:
            stable_count += 1
            if stable_count >= 3 and txt > 30: break
        else:
            stable_count = 0; last = txt
        time.sleep(1.0)
    final_txt = page.evaluate("()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length;}")
    h1 = page.evaluate("()=>{const h=document.querySelector('h1'); return h?h.innerText.trim():'';}")
    is_404 = page.evaluate("()=>{const t=(document.body.innerText||''); return t.includes('页面不存在') || !!(document.querySelector('.not-found,[class*=not-found],.error-404'));}")
    # 查找 skeleton / loading 标志
    has_skeleton = page.evaluate("()=>{return !!document.querySelector('.skeleton,[class*=skeleton],.loading,[class*=loading],mat-progress-spinner,mat-spinner');}")
    body_len = page.evaluate("()=>{return (document.body.innerText||'').trim().length;}")
    component_tags = page.evaluate("()=>{const names=['app-agents','app-settings','app-agent-list','app-settings-page','app-global-agents','app-global-settings']; return names.filter(n=>!!document.querySelector(n));}")
    sp = os.path.join(SHOT, path.replace('/','_') + ".png")
    page.screenshot(path=sp)
    return {
        "path": path, "readings": readings, "final_txt": final_txt, "body_len": body_len,
        "h1": h1, "is_404": bool(is_404), "has_skeleton": bool(has_skeleton),
        "component_tags": component_tags, "console_errors": console_errs, "failed_requests": failed_reqs,
        "screenshot": sp, "elapsed": time.time()-start
    }

report = {}
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width":1440,"height":900}, locale="zh-CN")
    page = ctx.new_page()
    page.set_default_navigation_timeout(60000)
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t=>localStorage.setItem('agentboard_token', t)", TOKEN)
    page.reload(wait_until="domcontentloaded"); time.sleep(2)

    for path in ["/settings", "/agents"]:
        report[path] = settle_full(page, path)
    browser.close()

out_file = os.path.join(OUT, "verify_settings_agents_run32.json")
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print(json.dumps(report, ensure_ascii=False, indent=2))
