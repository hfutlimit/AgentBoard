"""
Run37 focused ground-truth recheck (第 37 次巡检暖机复核).
Target: v6 首测报告为 0 字符的 8 个全局页(projects/epics/stories/tasks/bugs/
settings/agents/proposals)，用 settle 轮询(最长 25s)判定真实空白 vs 冷启
lazy-chunk 时序误报。同时复验已知 Bug:
  #1430 全局路由 /epics /stories /tasks /bugs /dashboard 404 (往轮报 FIXED)
  #1431 主题切换控件缺失 (往轮报 STILL)
模式沿用 run36_focused_recheck.py(v6 等价 auth + 长 settle + 截图)。
"""
import json, os, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(OUT, "screenshots_run37")
os.makedirs(SHOT, exist_ok=True)

def login():
    req = urllib.request.Request(BASE + "/api/auth/login",
        data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["token"]

TOKEN = login()
print("token len:", len(TOKEN))

# 本轮 v6 初判 P1 0 字符的 8 个路由 + #1430 复核所需的 5 路由
TARGETS = ["/projects", "/epics", "/stories", "/tasks", "/bugs",
           "/settings", "/agents", "/proposals",
           "/dashboard"]  # dashboard 用于 #1430 复核

def is_404(page):
    return page.evaluate("""()=>{
        const h1=document.querySelector('h1'); const h2=document.querySelector('h2');
        const ht=((h1&&h1.innerText)||'')+' '+((h2&&h2.innerText)||'');
        const nf=document.querySelector('.not-found,[class*=not-found],.error-404,[class*=error-404]');
        const vis=el=>el&&getComputedStyle(el).display!=='none'&&el.offsetParent!==null;
        const bodyHas=(document.body.innerText||'').includes('页面不存在');
        return {h1:h1?h1.innerText.trim():'',h2:h2?h2.innerText.trim():'',
                heading_match:(ht.includes('页面不存在')||ht.includes('找不到')),
                component_match:!!nf&&vis(nf),body_match:bodyHas};
    }""")

def settle(page, url, max_wait=25):
    page.goto(BASE + url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("app-root", timeout=15000)
    except Exception:
        pass
    last = -1; txt = 0; h1 = ""
    for _ in range(int(max_wait / 0.5)):
        d = page.evaluate("""()=>{const m=document.querySelector('main')||document.querySelector('app-root');
            const h=document.querySelector('h1');
            return {t:(m?m.innerText:'').trim().length, h:h?h.innerText.trim():''};}""")
        txt = d["t"]; h1 = d["h"]
        if txt == last and txt > 20:
            break
        last = txt
        page.wait_for_timeout(500)
    return txt, h1

report = {"targets": [], "theme_check": None}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
    page = ctx.new_page()
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.evaluate("t=>localStorage.setItem('agentboard_token', t)", TOKEN)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("app-root", timeout=20000)
    time.sleep(2)

    # 暖机复核 8 个 P1 finding + #1430 路由
    for url in TARGETS:
        try:
            txt, h1 = settle(page, url)
            nf = is_404(page)
            blank = txt < 30
            name = url.strip("/").replace("/", "_") or "root"
            page.screenshot(path=os.path.join(SHOT, name + ".png"), full_page=False)
            rec = {"url": url, "txt": txt, "h1": h1, "blank": blank,
                   "is_404": nf["heading_match"] or nf["component_match"] or nf["body_match"],
                   "screenshot": os.path.relpath(os.path.join(SHOT, name + ".png"), OUT)}
            report["targets"].append(rec)
            print(f"[RECHECK] {url} txt={txt} h1={h1!r} blank={blank} is404={rec['is_404']}")
        except Exception as e:
            report["targets"].append({"url": url, "error": repr(e)[:200]})
            print(f"[RECHECK-ERR] {url} {repr(e)[:200]}")

    # #1431 主题切换控件专项：枚举 header 内所有 button + 用户菜单展开 + dataset.theme
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded")
        time.sleep(2)
        theme_info = page.evaluate("""()=>{
            const header=document.querySelector('header')||document.querySelector('.topbar')||document.querySelector('app-topbar');
            const btns=header?Array.from(header.querySelectorAll('button')).map(b=>(b.innerText||'').trim()+''+(b.getAttribute('aria-label')||'')):[];
            const themeCandidates=Array.from(document.querySelectorAll('button')).filter(b=>{
                const t=(b.innerText||'')+(b.getAttribute('aria-label')||'');
                return /主题|暗色|亮色|theme|dark|light|☀|🌙|🌞|🌚/.test(t);
            }).length;
            const datasetTheme=document.documentElement.dataset?document.documentElement.dataset.theme:null;
            const themeTextEls=Array.from(document.querySelectorAll('*')).filter(e=>{
                const t=e.innerText||''; return t==='主题'||t==='暗色'||t==='亮色';
            }).length;
            return {header_buttons:btns, theme_button_count:themeCandidates,
                    dataset_theme:datasetTheme, theme_text_elements:themeTextEls};
        }""")
        # 尝试展开用户菜单看是否有主题项
        menu_items = []
        try:
            avatar = page.query_selector("header button:has(svg), .user-avatar, [class*=avatar]")
            if avatar:
                avatar.click()
                time.sleep(1)
                menu_items = page.evaluate("""()=>{
                    const menus=Array.from(document.querySelectorAll('.menu, .dropdown, [class*=menu], [class*=dropdown]'));
                    const items=[];
                    menus.forEach(m=>Array.from(m.querySelectorAll('a,button,li')).forEach(li=>{
                        const t=(li.innerText||'').trim(); if(t) items.push(t);
                    }));
                    return items;
                }""")
        except Exception as e:
            menu_items = ["menu_open_err:" + repr(e)[:80]]
        theme_info["user_menu_items"] = menu_items
        report["theme_check"] = theme_info
        print(f"[THEME] candidates={theme_info['theme_button_count']} dataset_theme={theme_info['dataset_theme']} "
              f"text_els={theme_info['theme_text_elements']} menu_items={menu_items}")
    except Exception as e:
        report["theme_check"] = {"error": repr(e)[:200]}
        print(f"[THEME-ERR] {repr(e)[:200]}")

    b.close()

with open(os.path.join(OUT, "report_run37_focused.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print("DONE")
