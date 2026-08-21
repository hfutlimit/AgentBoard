"""
AGB v8 聚焦复核 (第 28 次执行)
- 对 v6 初判 txt=0 的 5 个页面(tasks/settings/agents/admin/project/3/overview)做轮询稳定测量, 判定是否真空白。
- 复测 /api/stories/27/tasks 500(直接带 token, 3 次重试) 判定真 bug vs 瞬时。
- /story/348 评论框存在性(枚举 comment 相关选择器)。
- /tasks 404 歧义澄清(txt=0 但 is_404=False)。
每环节独立 try/except, 末尾强制写 JSON。
"""
import os, json, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_v8")
os.makedirs(SHOT, exist_ok=True)

def login_token():
    for _ in range(8):
        try:
            req = urllib.request.Request(BASE + "/api/auth/login",
                data=json.dumps({"username":"admin","password":"admin123"}).encode(),
                headers={"Content-Type":"application/json"}, method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=25).read())["token"]
        except Exception as e:
            time.sleep(6)
    raise RuntimeError("login failed")

TOKEN = login_token()
res = {"warm_recheck": [], "api_27_tasks": {}, "story348_comment": {}, "tasks_404": {}}

def settle_measure(page, path):
    try:
        page.goto(BASE+path, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        return {"path":path,"error":str(e)[:120]}
    prev=-1; stable=0; last=0
    for _ in range(40):
        time.sleep(0.5)
        try:
            last = page.evaluate("()=>{const m=document.querySelector('main')||document.body; return (m.innerText||'').trim().length;}")
        except Exception:
            last=0
        if last==prev:
            stable+=1
            if stable>=3: break
        else:
            stable=0
        prev=last
    h1 = page.evaluate("()=>{const h=document.querySelector('h1'); return h?h.innerText.trim():'';}")
    is404 = page.evaluate("""()=>{const t=(document.body.innerText||''); const nf=document.querySelector('.not-found,[class*=not-found]'); return t.includes('页面不存在')||!!nf;}""")
    overflow = page.evaluate("()=>{const de=document.documentElement,b=document.body; return Math.max(de.scrollWidth-de.clientWidth,b.scrollWidth-b.clientWidth);}")
    try:
        page.screenshot(path=os.path.join(SHOT, f"warm{path.replace('/','_')}.png"))
    except Exception:
        pass
    return {"path":path,"final_text_len":last,"h1":h1,"is_404":bool(is404),"overflow_px":overflow,
            "verdict":"BLANK" if (last<30 and not is404) else ("404" if is404 else "OK")}

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width":1440,"height":900}, locale="zh-CN")
    page = ctx.new_page()
    page.set_default_navigation_timeout(30000)
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
        page.evaluate("t=>localStorage.setItem('agentboard_token',t)", TOKEN)
        page.reload(wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("app-root", timeout=20000)
        time.sleep(2)
    except Exception as e:
        res["boot_error"] = str(e)[:200]

    for path in ["/tasks","/settings","/agents","/admin","/project/3/overview"]:
        try:
            m = settle_measure(page, path)
            res["warm_recheck"].append(m)
            print(f"[WARM] {path:24s} txt={m.get('final_text_len')} h1={str(m.get('h1',''))[:20]!r} 404={m.get('is_404')} -> {m['verdict']}")
        except Exception as e:
            res["warm_recheck"].append({"path":path,"error":str(e)[:120]})
            print(f"[WARM-ERR] {path}: {e}")

    # /tasks 404 歧义: 二次 settle 后若仍 txt<30 且非 404, 截图肉眼复核
    try:
        page.goto(BASE+"/tasks", wait_until="domcontentloaded", timeout=30000); time.sleep(3)
        body = page.evaluate("()=>document.body.innerText.slice(0,200)")
        res["tasks_404"] = {"body_preview": body, "has_404_text": "页面不存在" in body}
        print(f"[TASKS] body preview: {body[:80]!r}")
    except Exception as e:
        res["tasks_404"] = {"error": str(e)[:120]}

    # /api/stories/27/tasks 500 复测
    for attempt in range(3):
        try:
            req = urllib.request.Request(BASE + "/api/stories/27/tasks",
                headers={"Authorization": f"Bearer {TOKEN}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
                res["api_27_tasks"] = {"attempt":attempt+1,"status":r.status,"count":len(data) if isinstance(data,list) else "n/a"}
                print(f"[API27] attempt {attempt+1}: 200, count={len(data) if isinstance(data,list) else '?'}")
                break
        except urllib.error.HTTPError as e:
            res["api_27_tasks"] = {"attempt":attempt+1,"status":e.code,"body":e.read().decode('utf-8','ignore')[:200]}
            print(f"[API27] attempt {attempt+1}: HTTP {e.code} -> {e.read().decode('utf-8','ignore')[:100]!r}")
            time.sleep(2)
        except Exception as e:
            res["api_27_tasks"] = {"attempt":attempt+1,"error":str(e)[:120]}
            time.sleep(2)

    # /story/348 评论框枚举
    try:
        page.goto(BASE+"/story/348", wait_until="domcontentloaded", timeout=30000); time.sleep(3)
        found = page.evaluate("""()=>{
            const sels=['textarea','input[placeholder*=评论]','input[placeholder*=comment]','.comment-editor','app-comment-editor','[class*=comment-box]','[class*=comment-editor]','[contenteditable=true]','button:has-text("评论")'];
            const out={};
            for(const s of sels){ const els=document.querySelectorAll(s); out[s]=els.length; }
            return out;
        }""")
        res["story348_comment"] = found
        print("[COMMENT348]", found)
    except Exception as e:
        res["story348_comment"] = {"error": str(e)[:150]}

    b.close()

with open(os.path.join(OUT,"report_v8_focused.json"),"w",encoding="utf-8") as f:
    json.dump(res,f,ensure_ascii=False,indent=2)
print("\n[V8 DONE]")
