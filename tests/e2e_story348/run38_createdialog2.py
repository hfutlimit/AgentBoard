"""
Run38 新建项目弹窗最终确认 (用精确 class 选择器, 规避 has-text 冷启时机误判).
点击 heading-action-btn(『＋ 新建项目』) → 等待 .modal 出现 → 截图 + 记录 opened + 表单字段.
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

# 带重试的 login(规避生产后端偶发不可达)
def login_retry():
    for i in range(6):
        try:
            return login()
        except Exception as e:
            print(f"login retry {i+1}: {e}")
            time.sleep(2)
    raise RuntimeError("login failed after retries")

TOKEN = login_retry()
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
    # 二次导航 + 更长等待确保 heading-action-btn 渲染
    page.goto(BASE + "/projects", wait_until="domcontentloaded")
    page.wait_for_selector("app-root", timeout=15000)
    page.wait_for_selector("button.heading-action-btn", timeout=15000)
    time.sleep(1)

    btn = page.locator("button.heading-action-btn").first
    report["button_text"] = btn.inner_text().strip()
    btn.click(timeout=8000)
    time.sleep(1.5)
    opened = False
    for sel in [".modal", "[role=dialog]", ".cdk-overlay-container .modal", "app-modal"]:
        try:
            page.wait_for_selector(sel, timeout=4000, state="visible")
            opened = True
            report["opened_selector"] = sel
            break
        except Exception:
            continue
    report["form_fields"] = page.evaluate("""()=>{
        const d=document.querySelector('.cdk-overlay-container')||document.body;
        const els=d.querySelectorAll('input,textarea,select');
        return Array.from(els).map(e=>e.getAttribute('placeholder')||e.getAttribute('formcontrolname')||e.type).slice(0,12);
    }""")
    page.screenshot(path=os.path.join(SHOT, "createdialog2.png"))
    report["dialog_opened"] = opened
    report["verdict"] = "OK_OPENS" if opened else "NO_MODAL"
    print("createdialog2:", report)

with open(os.path.join(OUT, "report_run38_createdialog2.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print("DONE")
