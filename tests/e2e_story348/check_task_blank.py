from playwright.sync_api import sync_playwright
import sys
BASE="http://127.0.0.1:4200"
import urllib.request, json
token=json.loads(urllib.request.urlopen(urllib.request.Request(BASE+"/api/auth/login",data=json.dumps({"username":"admin","password":"admin123"}).encode(),headers={"Content-Type":"application/json"},method="POST"),timeout=10).read())["token"]
ids=[1339,1342,1341,1340]
with sync_playwright() as p:
    b=p.chromium.launch(); c=b.new_context(viewport={"width":1440,"height":900},locale="zh-CN"); page=c.new_page()
    page.goto(BASE+"/", wait_until="domcontentloaded")
    page.evaluate("t=>localStorage.setItem('agentboard_token',t)", token); page.reload(wait_until="domcontentloaded")
    for tid in ids:
        page.goto(BASE+f"/task/{tid}", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        txt=page.evaluate("()=>document.body.innerText.length")
        main=page.evaluate("""()=>{const m=document.querySelector('.layout,.content,main'); return m?m.innerText.length:0}""")
        print(f"task {tid}: bodyTextLen={txt} mainTextLen={main}")
        page.screenshot(path=f"tests/e2e_story348/screenshots/task_{tid}_check.png")
    b.close()
