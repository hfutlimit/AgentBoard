"""
Run38 探针: 列出 /projects 页面所有 button / a 的可点击元素(文本+class),
确认是否存在『新建项目』入口及其真实标签/选择器, 截图存证。
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
report = {}

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    page = ctx.new_page()
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t=>localStorage.setItem('agentboard_token', t)", TOKEN)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("app-root", timeout=20000)
    time.sleep(2)
    page.goto(BASE + "/projects", wait_until="domcontentloaded")
    page.wait_for_selector("app-root", timeout=15000)
    time.sleep(3)

    # 滚动到顶确保 header 可见
    page.evaluate("()=>window.scrollTo(0,0)")
    time.sleep(1)

    buttons = page.evaluate("""()=>{
        const out=[];
        document.querySelectorAll('button, a, [role=button]').forEach(el=>{
            const t=(el.innerText||'').trim().replace(/\\s+/g,' ');
            if(t.length>0 && t.length<40){
                out.push({tag:el.tagName.toLowerCase(), text:t, cls:(el.className&&el.className.toString?el.className.toString():'').slice(0,60)});
            }
        });
        return out;
    }""")
    report["clickable_count"] = len(buttons)
    report["all_buttons"] = buttons
    # 过滤含『新』或『项目』的
    report["new_related"] = [b for b in buttons if ('新' in b['text'] or '项目' in b['text'] or '创建' in b['text'])]
    page.screenshot(path=os.path.join(SHOT, "projects_probe.png"), full_page=False)
    print("clickable:", len(buttons))
    for b in report["new_related"]:
        print("  ", b)
    print("ALL:", json.dumps(buttons, ensure_ascii=False)[:2000])

with open(os.path.join(OUT, "report_run38_projects_probe.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print("DONE")
