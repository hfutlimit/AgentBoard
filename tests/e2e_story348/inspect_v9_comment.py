"""v9: 验证 /story/348 评论框是否存在(用合法选择器), 并复验 /projects 新建弹窗(open)。"""
import os, json, time, urllib.request
from playwright.sync_api import sync_playwright
BASE="http://127.0.0.1:4200"; OUT=os.path.dirname(os.path.abspath(__file__))
SHOT=os.path.join(OUT,"screenshots_v9"); os.makedirs(SHOT,exist_ok=True)
def login():
    req=urllib.request.Request(BASE+"/api/auth/login",data=json.dumps({"username":"admin","password":"admin123"}).encode(),headers={"Content-Type":"application/json"},method="POST")
    return json.loads(urllib.request.urlopen(req,timeout=20).read())["token"]
TOKEN=login(); res={}
with sync_playwright() as p:
    b=p.chromium.launch(); ctx=b.new_context(viewport={"width":1440,"height":900},locale="zh-CN"); page=ctx.new_page()
    page.goto(BASE+"/",wait_until="domcontentloaded"); page.evaluate("t=>localStorage.setItem('agentboard_token',t)",TOKEN)
    page.reload(wait_until="domcontentloaded"); page.wait_for_selector("app-root",timeout=20000); time.sleep(2)
    # 评论框
    page.goto(BASE+"/story/348",wait_until="domcontentloaded"); time.sleep(3)
    info=page.evaluate("""()=>{
        const out={};
        const selList=['textarea','input','[contenteditable=true]','[class*=comment]','app-comment','app-comment-editor','form'];
        for(const s of selList){ out[s]=document.querySelectorAll(s).length; }
        // 更宽松: 找含「评论」文本的可编辑区/按钮
        const allText=Array.from(document.querySelectorAll('*')).filter(e=>e.children.length===0 && /评论|发表|回复/.test(e.textContent||''));
        out['comment_text_nodes']=allText.slice(0,10).map(e=>e.tagName+':'+(e.textContent||'').trim().slice(0,20));
        // 是否有评论列表区
        out['has_comments_section']=!!document.querySelector('[class*=comment-list],[class*=comments],app-comment-list');
        return out;
    }""")
    page.screenshot(path=os.path.join(SHOT,"story348_comment.png"))
    res['story348_comment']=info
    print("[COMMENT348]",json.dumps(info,ensure_ascii=False))
    # 新建弹窗复验
    page.goto(BASE+"/projects",wait_until="domcontentloaded"); time.sleep(2.5)
    try:
        btn=page.locator("button", has_text="新建项目").first
        btn.click(timeout=5000); time.sleep(1.2)
        opened=page.evaluate("()=>{const m=document.querySelector('.modal-overlay,.modal,[role=dialog],.cdk-overlay-pane'); return !!m && m.offsetParent!==null;}")
        fields=page.evaluate("()=>{const m=document.querySelector('.modal-overlay,.modal,[role=dialog],.cdk-overlay-pane'); if(!m) return 0; return m.querySelectorAll('input,textarea,select').length;}")
        page.screenshot(path=os.path.join(SHOT,"create_dialog_v9.png"))
        res['create_dialog']={"opened":bool(opened),"fields":fields}
        print(f"[CREATE] opened={opened} fields={fields}")
        # 关闭
        try: page.keyboard.press("Escape"); time.sleep(0.5)
        except Exception: pass
    except Exception as e:
        res['create_dialog']={"error":str(e)[:150]}
        print("[CREATE-ERR]",str(e)[:120])
    b.close()
with open(os.path.join(OUT,"report_v9_comment.json"),"w",encoding="utf-8") as f: json.dump(res,f,ensure_ascii=False,indent=2)
print("[V9 DONE]")
