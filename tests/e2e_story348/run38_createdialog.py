"""
Run38 新建项目弹窗严格复验 (P3 finding from v6: 点击新建项目按钮未打开弹窗).
沿用 v10 模式: 导航 /projects → 点击『新建项目』按钮 → 等待 .modal/.cdk-overlay 出现 → 截图 + 记录 opened.
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
    try:
        page.wait_for_selector("app-root", timeout=15000)
    except Exception:
        pass
    time.sleep(2)

    # 找到新建项目按钮
    btn = page.locator("button:has-text('新建项目')").first
    found = btn.count() > 0
    report["button_found"] = found
    opened = False
    if found:
        try:
            btn.click(timeout=8000)
            time.sleep(1.5)
            # 等待弹窗容器
            for sel in [".modal", "[role=dialog]", ".cdk-overlay-container .modal", "app-modal", ".dialog"]:
                try:
                    page.wait_for_selector(sel, timeout=4000, state="visible")
                    opened = True
                    report["opened_selector"] = sel
                    break
                except Exception:
                    continue
            # 检查是否有表单字段
            fields = page.evaluate("""()=>{
                const d=document.querySelector('.cdk-overlay-container')||document.body;
                const els=d.querySelectorAll('input,textarea,select,[formcontrolname]');
                let labels=[];
                els.forEach(e=>{const l=e.getAttribute('placeholder')||e.getAttribute('formcontrolname')||''; if(l) labels.push(l);});
                return labels.slice(0,10);
            }""")
            report["form_fields"] = fields
            page.screenshot(path=os.path.join(SHOT, "createdialog.png"))
        except Exception as e:
            report["click_err"] = repr(e)[:200]
    report["dialog_opened"] = opened
    report["verdict"] = "OK_OPENS" if opened else ("BUTTON_MISSING" if not found else "NO_MODAL")
    print("createdialog:", report)

with open(os.path.join(OUT, "report_run38_createdialog.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print("DONE")
