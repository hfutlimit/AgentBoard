"""Story 过滤修复快速验证（约 1 分钟，不触发分页）。

核心验证：Story 只显示自己的 task/bug，不泄漏同项目其它 story 的任务。
分页已在更早的完整运行中验证（第2页正确显示 2 条、无泄漏），此处聚焦过滤。
"""
import json
import sys
import time

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:58125"
WEB = "http://127.0.0.1:8080"
checks = []


def check(name, cond, detail=""):
    checks.append((name, cond, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""), flush=True)


sess = requests.Session()
TOKEN = sess.post(API + "/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=15).json()["token"]
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def api(method, path, json_body=None, timeout=60):
    return sess.request(method, API + path, json=json_body, headers=H, timeout=timeout)


print(">> 创建测试数据（小数据集，约 40s）...", flush=True)
t0 = time.time()
pj = api("POST", "/api/projects", {"name": "PBFast_StoryFix"}).json()
pid = pj["id"]
ep = api("POST", f"/api/projects/{pid}/epics", {"title": "EpicV"}).json()
eid = ep["id"]
s1 = api("POST", f"/api/epics/{eid}/stories", {"title": "StoryA_FastXYZ"}).json()
s1id = s1["id"]
s2 = api("POST", f"/api/epics/{eid}/stories", {"title": "StoryB_FastXYZ"}).json()
s2id = s2["id"]
for i in range(6):
    api("POST", f"/api/stories/{s1id}/tasks",
        {"title": f"AT-unique-{i}", "type": "task", "project_id": pid, "status": "todo", "priority": "medium"})
for i in range(3):
    api("POST", f"/api/stories/{s2id}/tasks",
        {"title": f"BT-unique-{i}", "type": "task", "project_id": pid, "status": "todo", "priority": "medium"})
print(f"<< 就绪 ({round(time.time()-t0,1)}s): storyA={s1id} storyB={s2id}", flush=True)

ra = api("GET", f"/api/stories/{s1id}/tasks?limit=200").json()
rb = api("GET", f"/api/stories/{s2id}/tasks?limit=200").json()
check("API: StoryA 任务总数=6", ra["total"] == 6, f"total={ra['total']}")
check("API: StoryB 任务总数=3", rb["total"] == 3, f"total={rb['total']}")

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.add_init_script("localStorage.setItem('agentboard_token', %s);" % json.dumps(TOKEN))
        console_errors = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        page.goto(f"{WEB}/story/{s1id}", wait_until="domcontentloaded")
        page.wait_for_selector(".story-header", timeout=20000)
        page.wait_for_selector(".entity-item .entity-item-title", timeout=20000)

        header_title = page.text_content(".story-title-row h2").strip()
        check("标题栏: 显示 Story 标题", header_title == "StoryA_FastXYZ", f"got='{header_title}'")
        check("标题栏: 存在内嵌进度条 .story-progress-inline",
              page.query_selector(".story-progress-inline") is not None)

        titles = [el.inner_text().strip() for el in page.query_selector_all(".entity-item .entity-item-title")]
        leaked = [t for t in titles if t.startswith("BT-unique-")]
        only_a = all(t.startswith("AT-unique-") for t in titles)
        check("过滤: 仅显示 StoryA 任务（共 6 条）", len(titles) == 6 and only_a, f"count={len(titles)} only_a={only_a}")
        check("过滤: 不泄漏 StoryB 任务", len(leaked) == 0, f"leaked={leaked}")
        # 小数据集不应出现分页控件
        check("分页: 少量任务时不显示分页控件", page.query_selector(".story-pagination-wrap") is None)
        check("运行时: 无 JS 控制台错误", len(console_errors) == 0, "errors=" + " | ".join(console_errors[:3]))
        browser.close()
except Exception as e:
    check("Playwright 执行", False, f"异常: {e}")
    import traceback
    traceback.print_exc()

try:
    api("DELETE", f"/api/projects/{pid}")
    print(f"Cleanup: 已删除测试项目 {pid}", flush=True)
except Exception as e:
    print(f"Cleanup warn: {e}", flush=True)

print("\n================ 验证结果 ================", flush=True)
passed = sum(1 for _, c, _ in checks if c)
for name, c, detail in checks:
    print(f"[{'PASS' if c else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""), flush=True)
print(f"\n通过 {passed}/{len(checks)}", flush=True)
sys.exit(0 if passed == len(checks) else 1)
