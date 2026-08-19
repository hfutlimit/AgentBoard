import json, urllib.request
from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:4200"
API = "http://124.220.44.12"
CHARS = ["\u25a6", "\u25c7", "\u2699", "\u25a4", "\u25aa", "\u25ab"]

def login():
    req = urllib.request.Request(API + "/api/auth/login",
        data=json.dumps({"username":"admin","password":"admin123"}).encode(), method="POST")
    req.add_header("Content-Type","application/json")
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.loads(r.read().decode())["token"]

def main():
    token = login()
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-proxy-server"])
        ctx = b.new_context(viewport={"width":1440,"height":900})
        page = ctx.new_page()
        page.add_init_script(
            "localStorage.setItem('agentboard_token','%s');"
            "localStorage.setItem('agentboard_user','admin');" % token)
        page.goto(WEB + "/project/3", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        page.screenshot(path="tests/e2e_epic149/screenshots/pinpoint_workspace.png", full_page=False)
        findings = page.evaluate("""(CHARS)=>{
            const out=[]; const all=document.querySelectorAll('body *');
            for(const ch of CHARS){
                const hits=[];
                for(const el of all){
                    if(!el.children||el.children.length>3) continue;
                    const t=(el.textContent||'').trim();
                    if(!t||t.length>100) continue;
                    if(t.includes(ch)){
                        const cls=(el.className||'').toString();
                        const inSide=!!el.closest('aside,.sidebar,[class*=sidebar],nav');
                        const inTop=!!el.closest('header,.topbar,[class*=topbar]');
                        const sec=inSide?'SHELL(sidebar)':(inTop?'SHELL(topbar)':'INTERNAL');
                        hits.push({tag:el.tagName,cls:cls.slice(0,90),text:t.slice(0,90),sec});
                        if(hits.length>=8) break;
                    }
                }
                if(hits.length) out.push({ch,count:hits.length,sample:hits});
            }
            return {findings:out,
                aside:document.querySelectorAll('aside,.sidebar,[class*=sidebar]').length,
                top:document.querySelectorAll('header,.topbar,[class*=topbar]').length};
        }""", CHARS)
        print(json.dumps(findings, ensure_ascii=False, indent=2))
        b.close()

if __name__=="__main__": main()
