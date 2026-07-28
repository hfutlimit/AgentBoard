"""快照：Epic 76 v6.3 激活筛选条件 chips 条（列表视图）。"""
import json, random, urllib.request, urllib.error
from playwright.sync_api import sync_playwright

WEB="http://127.0.0.1:8080"; API="http://127.0.0.1:58125"; EPIC_ID=66; PROJECT_ID=65
USER="admin"; PASS="admin123"; SEED="__SHOT76_" + str(random.randint(100000,999999))
def _login():
    r=urllib.request.Request(API+"/api/auth/login",data=json.dumps({"username":USER,"password":PASS}).encode(),method="POST")
    r.add_header("Content-Type","application/json")
    with urllib.request.urlopen(r,timeout=20) as x: return json.loads(x.read().decode())["token"]
TOKEN=_login()
def api(m,p,b=None):
    r=urllib.request.Request(API+p,data=json.dumps(b).encode() if b else None,method=m)
    r.add_header("Content-Type","application/json"); r.add_header("Authorization","Bearer "+TOKEN)
    try:
        with urllib.request.urlopen(r,timeout=20) as x: return x.status,json.loads(x.read().decode() or "{}")
    except urllib.error.HTTPError as e: return e.code,json.loads(e.read().decode() or "{}")
st,story=api("POST",f"/api/epics/{EPIC_ID}/stories",{"title":SEED+"-story"})
sid=story["id"]
for suf,pri,typ in [("-A","high","task"),("-B","medium","bug"),("-C","low","test_execution")]:
    api("POST",f"/api/stories/{sid}/tasks",{"project_id":PROJECT_ID,"title":SEED+suf,"type":typ,"priority":pri})
with sync_playwright() as p:
    b=p.chromium.launch(args=["--no-proxy-server"]); pg=b.new_page()
    pg.route("**://127.0.0.1:58124/**", lambda r: r.continue_(url=r.request.url.replace("58124","58125")))
    pg.add_init_script("localStorage.setItem('agentboard_token','%s');localStorage.setItem('agentboard_user','admin');localStorage.setItem('agentboard_story_view','list');" % TOKEN)
    pg.goto(WEB+f"/story/{sid}", wait_until="domcontentloaded")
    pg.wait_for_selector("#boardToggle", timeout=20000)
    pg.locator(".chips .chip").nth(1).click()           # 状态筛选
    pg.locator('input[aria-label="搜索任务"]').fill(SEED)  # 搜索筛选
    pg.wait_for_selector(".active-filter-bar", state="visible", timeout=5000)
    pg.wait_for_timeout(400)
    pg.screenshot(path="screenshots/epic76_v63_active_filter_chips.png", full_page=False)
    print("screenshot saved")
    api("DELETE",f"/api/stories/{sid}",token=TOKEN)
    b.close()
