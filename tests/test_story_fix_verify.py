"""Story 视图修复验证（Playwright 真实浏览器）。

验证 3 项修复：
1. 过滤：Story 只显示自己的 task/bug（不显示同项目其它 story 的任务）
2. 分页：任务总数 > 50 时显示分页控件，且翻页生效
3. 标题栏：story-header 精简布局存在，进度条内嵌

直接对运行中的真实服务（web 8080 + api 58125）做端到端验证，测试数据自建自清。
注意：本地 dev SQLite 单条写入约 5.5s，故任务顺序创建（并行会触发 DB lock 500）。
"""
import json
import sys
import time

import requests
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:58125"
WEB = "http://127.0.0.1:8080"

failures = []
checks = []


def check(name, cond, detail=""):
    checks.append((name, cond, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""), flush=True)


sess = requests.Session()
TOKEN = sess.post(API + "/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=15).json()["token"]
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def api(method, path, json_body=None, timeout=60):
    return sess.request(method, API + path, json=json_body, headers=H, timeout=timeout)


# ---------------- 1. 准备测试数据（顺序创建，避免 DB lock）----------------
print(">> 创建测试数据（dev SQLite 约 5.5s/条，请稍候）...", flush=True)
t0 = time.time()
pj = api("POST", "/api/projects", {"name": "PBVerify_StoryFix"}).json()
pid = pj["id"]
ep = api("POST", f"/api/projects/{pid}/epics", {"title": "EpicV_StoryFix"}).json()
eid = ep["id"]
s1 = api("POST", f"/api/epics/{eid}/stories", {"title": "StoryA_MarkerXYZ"}).json()
s1id = s1["id"]
s2 = api("POST", f"/api/epics/{eid}/stories", {"title": "StoryB_MarkerXYZ"}).json()
s2id = s2["id"]

A_N = 52  # 触发分页（pageSize=50 -> 2 页）
for i in range(A_N):
    api("POST", f"/api/stories/{s1id}/tasks",
        {"title": f"AT-unique-{i}", "type": "task", "project_id": pid, "status": "todo", "priority": "medium"})
    if (i + 1) % 10 == 0:
        print(f"   已创建 StoryA 任务 {i+1}/{A_N}", flush=True)
for i in range(3):
    api("POST", f"/api/stories/{s2id}/tasks",
        {"title": f"BT-unique-{i}", "type": "task", "project_id": pid, "status": "todo", "priority": "medium"})
print(f"<< 测试数据就绪 ({round(time.time()-t0,1)}s): project={pid} epic={eid} storyA={s1id} storyB={s2id}", flush=True)

# 后端计数交叉校验
ra = api("GET", f"/api/stories/{s1id}/tasks?limit=200").json()
rb = api("GET", f"/api/stories/{s2id}/tasks?limit=200").json()
check("API: StoryA 任务总数=52", ra["total"] == 52, f"total={ra['total']}")
check("API: StoryB 任务总数=3", rb["total"] == 3, f"total={rb['total']}")

# ---------------- 2. Playwright 端到端 ----------------
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
        page.wait_for_selector(".story-pagination-wrap", timeout=20000)

        # --- 标题栏布局 ---
        header_title = page.text_content(".story-title-row h2").strip()
        check("标题栏: 显示 Story 标题", header_title == "StoryA_MarkerXYZ", f"got='{header_title}'")
        check("标题栏: 存在内嵌进度条 .story-progress-inline",
              page.query_selector(".story-progress-inline") is not None)
        check("标题栏: 面包屑存在 .crumb-bar", page.query_selector(".story-header .crumb-bar") is not None)

        # --- 过滤：只显示自己的任务 ---
        titles = [el.inner_text().strip() for el in page.query_selector_all(".entity-item .entity-item-title")]
        page1_count = len(titles)
        leaked = [t for t in titles if t.startswith("BT-unique-")]
        only_a = all(t.startswith("AT-unique-") for t in titles)
        check("过滤: 第1页不泄漏 StoryB 任务", len(leaked) == 0, f"leaked={leaked}")
        check("过滤: 第1页任务全部属于 StoryA", only_a, f"page1_count={page1_count}")
        check("分页: 第1页数量=50（pageSize）", page1_count == 50, f"count={page1_count}")

        summary_text = page.text_content(".task-list-summary .summary-text").strip()
        check("分页: 汇总栏显示共 52 项", "52" in summary_text, f"summary='{summary_text}'")

        # --- 分页翻页（分页器用「下一页 ›」按钮，无数字页码按钮）---
        page2_btn = page.query_selector(".story-pagination-wrap app-pagination button:has-text('下一页')")
        check("分页: 找到「下一页」按钮", page2_btn is not None)
        if page2_btn is not None:
            page2_btn.click()
            # 等待页码切换到第 2 页（分页器文本变化），再读取渲染后的列表
            page.wait_for_function(
                "document.querySelector('.story-pagination-wrap .pagination-current') && "
                "document.querySelector('.story-pagination-wrap .pagination-current').textContent.includes('第 2')",
                timeout=10000)
            page.wait_for_timeout(400)
            titles2 = [el.inner_text().strip() for el in page.query_selector_all(".entity-item .entity-item-title")]
            check("分页: 第2页数量=2", len(titles2) == 2, f"count={len(titles2)}")
            leaked2 = [t for t in titles2 if t.startswith("BT-unique-")]
            check("分页: 第2页也不泄漏 StoryB 任务", len(leaked2) == 0, f"leaked={leaked2}")
            check("分页: 第2页与第1页任务集合不同", set(titles2).isdisjoint(set(titles)),
                  f"p1={titles[:3]}... p2={titles2}")

        check("运行时: 无 JS 控制台错误", len(console_errors) == 0,
              "errors=" + " | ".join(console_errors[:3]))

        browser.close()
except Exception as e:
    check("Playwright 执行", False, f"异常: {e}")
    import traceback
    traceback.print_exc()

# ---------------- 3. 清理测试数据 ----------------
try:
    api("DELETE", f"/api/projects/{pid}")
    print(f"Cleanup: 已删除测试项目 {pid}", flush=True)
except Exception as e:
    print(f"Cleanup warn: {e}", flush=True)

# ---------------- 汇总 ----------------
print("\n================ 验证结果 ================", flush=True)
passed = sum(1 for _, c, _ in checks if c)
total = len(checks)
for name, c, detail in checks:
    print(f"[{'PASS' if c else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""), flush=True)
print(f"\n通过 {passed}/{total}", flush=True)
sys.exit(0 if passed == total else 1)
