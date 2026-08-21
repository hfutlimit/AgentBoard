"""v10: 严格复验 /projects 新建项目弹窗(等待 CDK overlay 动画 + 多选择器)。"""
import os, json, time, urllib.request
from playwright.sync_api import sync_playwright
BASE="http://127.0.0.1:4200"; OUT=os.path.dirname(os.path.abspath(__file__))
SHOT=os.path.join(OUT,"screenshots_v10"); os.makedirs(SHOT,exist_ok=True)
def login():
    req=urllib.request.Request(BASE+"/api/auth/login",data=json.dumps({"username":"admin","password":"admin123"}).encode(),headers={"Content-Type":"application/json"},method="POST")
    return json.loads(urllib.request.urlopen(req,timeout=20).read())["token"]
TOKEN=login(); res={}
with sync_playwright() as p:
    b=p.chromium.launch(); ctx=b.new_context(viewport={"width":1440,"height":900},locale="zh-CN"); page=ctx.new_page()
    page.goto(BASE+"/",wait_until="domcontentloaded"); page.evaluate("t=>localStorage.setItem('agentboard_token',t)",TOKEN)
    page.reload(wait_until="domcontentloaded"); page.wait_for_selector("app-root",timeout=20000); time.sleep(2)
    page.goto(BASE+"/projects",wait_until="domcontentloaded"); time.sleep(2.5)
    try:
        btn=page.locator("button", has_text="新建项目").first
        btn.wait_for(state="visible",timeout=5000)
        btn.click(timeout=5000)
        # 等待 overlay 出现并可见(最多 5s)
        visible=False; modexp=None
        for sel in [".cdk-overlay-pane",".modal",".modal-overlay","app-modal","[role=dialog]"]:
            try:
                page.wait_for_selector(sel, state="visible", timeout=4000)
                visible=True; modexp=sel; break
            except Exception: pass
        time.sleep(1.0)
        info=page.evaluate("""()=>{
            const cands=['.cdk-overlay-pane','.modal','.modal-overlay','app-modal','[role=dialog]','.modal-create'];
            for(const s of cands){ const m=document.querySelector(s); if(m && m.offsetParent!==null){
                return {sel:s, fields:m.querySelectorAll('input,textarea,select').length, text:(m.innerText||'').slice(0,120)}; } }
            return {sel:null, fields:0, text:''};
        }""")
        page.screenshot(path=os.path.join(SHOT,"create_dialog_v10.png"))
        res['create_dialog']={"visible_after_click":visible,"matched_selector":modexp,**info}
        print("[CREATE]",json.dumps(res['create_dialog'],ensure_ascii=False))
    except Exception as e:
        res['create_dialog']={"error":str(e)[:160]}; print("[CREATE-ERR]",str(e)[:120])
    b.close()
with open(os.path.join(OUT,"report_v10_createdialog.json"),"w",encoding="utf-8") as f: json.dump(res,f,ensure_ascii=False,indent=2)
print("[V10 DONE]")
