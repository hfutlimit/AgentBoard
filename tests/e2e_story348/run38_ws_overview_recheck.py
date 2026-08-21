"""
Run38 暖机复核 — ws_overview (/project/3/overview) 冷启 0 字符专项.
v6 首测 ws_overview txt=0, 与历史轮次(ws_overview=492)矛盾 → 疑为 lazy-chunk 冷启时序误报.
用 settle 轮询(最长 25s)判定真实空白 vs 冷启误报, 截图存证.
"""
import json, os, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_run38")
os.makedirs(SHOT, exist_ok=True)

def login():
    req = urllib.request.Request(BASE + "/api/auth/login",
        data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["token"]

TOKEN = login()
print("token len:", len(TOKEN))
report = {}

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    page = ctx.new_page()
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t=>localStorage.setItem('agentboard_token', t)", TOKEN)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("app-root", timeout=20000)
    time.sleep(1)

    url = "/project/3/overview"
    page.goto(BASE + url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("app-root", timeout=15000)
    except Exception:
        pass
    last = -1
    for _ in range(int(25 / 0.5)):
        d = page.evaluate("""()=>{const m=document.querySelector('main')||document.querySelector('app-root');
            const h=document.querySelector('h1');
            return {t:(m?m.innerText:'').trim().length, h:h?h.innerText.trim():''};}""")
        txt = d["t"]; h1 = d["h"]
        if txt == last and txt >= 30:
            break
        last = txt
        time.sleep(0.5)
    is404 = page.evaluate("()=>{const t=(document.body.innerText||''); return t.includes('页面不存在');}")
    rep = {"page": "ws_overview", "path": url, "final_text_len": txt, "h1": h1, "is_404": bool(is404),
           "verdict": "REAL_BLANK" if (txt < 30 and not is404) else ("404" if is404 else "OK_RENDERED")}
    page.screenshot(path=os.path.join(SHOT, "ws_overview_recheck.png"))
    report["ws_overview"] = rep
    print("ws_overview:", rep)

with open(os.path.join(OUT, "report_run38_ws_overview.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print("DONE")
