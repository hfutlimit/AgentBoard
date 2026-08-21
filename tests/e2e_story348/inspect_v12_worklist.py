"""
AGB v12 工作项列表专项 (第 29 次执行补充)
- 正确路由: 工作项 tab -> /project/3/backlog (v6 ws_tabs 已确认)
- 覆盖: 列表渲染(tasks/stories 行数) + 搜索输入 + 分页器 + 暗色主题 DOM 钩子探测
"""
import os, json, time, urllib.request
from playwright.sync_api import sync_playwright

BASE="http://127.0.0.1:4200"
OUT=os.path.dirname(os.path.abspath(__file__))
SHOT=os.path.join(OUT,"screenshots_v12"); os.makedirs(SHOT,exist_ok=True)

def login():
    for _ in range(8):
        try:
            req=urllib.request.Request(BASE+"/api/auth/login",data=json.dumps({"username":"admin","password":"admin123"}).encode(),headers={"Content-Type":"application/json"},method="POST")
            return json.loads(urllib.request.urlopen(req,timeout=25).read())["token"]
        except Exception: time.sleep(6)
    raise RuntimeError("login failed")

T=login(); res={}
with sync_playwright() as p:
    b=p.chromium.launch()
    ctx=b.new_context(viewport={"width":1440,"height":900},locale="zh-CN")
    pg=ctx.new_page(); pg.set_default_navigation_timeout(30000)
    pg.goto(BASE+"/",wait_until="domcontentloaded"); pg.evaluate("t=>localStorage.setItem('agentboard_token',t)",T)
    pg.reload(wait_until="domcontentloaded"); pg.wait_for_selector("app-root"); time.sleep(2)
    # 工作项列表
    pg.goto(BASE+"/project/3/backlog",wait_until="domcontentloaded"); time.sleep(4)
    info=pg.evaluate("""()=>{
        const txt=(document.querySelector('main')||document.body).innerText.trim().length;
        const h1=document.querySelector('h1'); 
        const inputs=[...document.querySelectorAll('input')].map(i=>(i.placeholder||i.getAttribute('aria-label')||'').trim()).filter(Boolean);
        const pag=document.querySelectorAll('.mat-paginator, [class*=paginator], .pagination, [class*=pagination]').length;
        // 行/卡片计数: 兼容 mat-row / .row / [class*=item] / [class*=card]
        const rows=document.querySelectorAll('.mat-row, tr.mat-row, [class*=task-row], [class*=item-row], .cdk-row').length;
        const cards=document.querySelectorAll('[class*=card]').length;
        return {main_txt:txt, h1:h1?h1.innerText.trim():'', inputs:inputs.slice(0,12), paginator:pag, rows, cards};
    }""")
    res["workitems_backlog"]=info
    pg.screenshot(path=os.path.join(SHOT,"backlog.png"))
    # 暗色主题 DOM 钩子探测: 是否任何元素声明 dark class / 主题变量
    theme_dom=pg.evaluate("""()=>{
        const htmlCls=document.documentElement.className;
        const hasDarkClass=/dark|theme-dark/i.test(htmlCls);
        const bodyCls=document.body.className;
        // 查找含 --bg / background 且 navy 的设计令牌是否存在(间接证明暗色可切换)
        const cs=getComputedStyle(document.documentElement);
        const navy=cs.getPropertyValue('--navy')||cs.getPropertyValue('--color-navy')||'';
        return {html_class:htmlCls, body_class:bodyCls, has_dark_class:hasDarkClass, navy_token:navy.trim()};
    }""")
    res["theme_dom_probe"]=theme_dom
    b.close()
with open(os.path.join(OUT,"report_v12_worklist.json"),"w",encoding="utf-8") as f: json.dump(res,f,ensure_ascii=False,indent=2)
print(json.dumps(res,ensure_ascii=False,indent=2))
print("[V12 DONE]")
