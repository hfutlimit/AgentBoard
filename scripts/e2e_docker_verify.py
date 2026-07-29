# -*- coding: utf-8 -*-
"""Docker 部署主功能 E2E 验证：项目/Epic/Story/Task/评论 创建与状态变更 + 文档创建更新。

用法: python e2e_docker_verify.py  (依赖 /tmp/e2e_setup.json 中的 admin_token)
"""
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:28080"
API = "http://127.0.0.1:18000/api"
SHOT_DIR = Path(r"E:\Projects\WorkBuddy\AgentBoard\screenshots\e2e-docker-0729")
SHOT_DIR.mkdir(parents=True, exist_ok=True)

setup = json.loads(Path(r"E:\Projects\WorkBuddy\AgentBoard\scripts\e2e_setup.json").read_text(encoding="utf-8"))
TOKEN = setup["admin_token"]
TS = time.strftime("%H%M%S")

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode() or "")


def unwrap(x):
    """兼容分页结构 {"items": [...]} 与裸列表。"""
    if isinstance(x, dict) and isinstance(x.get("items"), list):
        return x["items"]
    return x if isinstance(x, list) else []


def wait_ready(page, timeout=60000):
    page.wait_for_function("!document.querySelector('.skeleton')", timeout=timeout)
    page.wait_for_timeout(400)


def shot(page, name):
    page.screenshot(path=str(SHOT_DIR / f"{name}.png"), full_page=False)


def submit_create_modal(page, title, desc, key=None):
    page.wait_for_selector("#create-modal", timeout=10000)
    page.fill("#create-title", title)
    if key and page.locator("#create-key").count() > 0:
        page.fill("#create-key", key)
    if page.locator("#create-description").count() > 0:
        page.fill("#create-description", desc)
    page.click("#create-form button[type=submit]")
    page.wait_for_selector("#create-modal", state="detached", timeout=15000)
    page.wait_for_timeout(800)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(f"localStorage.setItem('agentboard_token', '{TOKEN}')")
        page = ctx.new_page()

        # ---------- 1. 项目创建 ----------
        page.goto(BASE + "/", wait_until="domcontentloaded")
        wait_ready(page)
        page.click('a.sidebar-nav-item[routerLink="/projects"]')
        wait_ready(page)
        proj_name = f"E2E-Docker验证-{TS}"
        page.click("#proj-new-btn")
        submit_create_modal(page, proj_name, "Docker 部署 E2E 自动验证项目", key=f"ED{TS[-4:]}")
        st, projs = api("GET", "/projects")
        proj = next((x for x in unwrap(projs) if x["name"] == proj_name), None) if st == 200 else None
        record("项目创建", proj is not None, f"id={proj and proj['id']}")
        if not proj:
            shot(page, "fail_project")
            browser.close()
            return
        pid = proj["id"]
        shot(page, "01_project_created")

        # ---------- 2. Epic 创建 ----------
        page.goto(f"{BASE}/project/{pid}", wait_until="domcontentloaded")
        wait_ready(page)
        if page.locator("#p-new-epic").count() > 0:
            page.click("#p-new-epic")
        else:
            page.click("text=创建第一个 Epic")
        epic_title = f"E2E-Epic-{TS}"
        submit_create_modal(page, epic_title, "E2E 验证 Epic")
        st, epics = api("GET", f"/projects/{pid}/epics")
        epic = next((x for x in unwrap(epics) if x["title"] == epic_title), None) if st == 200 else None
        record("Epic 创建", epic is not None, f"id={epic and epic['id']}")
        if not epic:
            shot(page, "fail_epic")
            browser.close()
            return
        eid = epic["id"]
        shot(page, "02_epic_created")

        # ---------- 3. Story 创建 ----------
        page.goto(f"{BASE}/epic/{eid}", wait_until="domcontentloaded")
        wait_ready(page)
        page.click("#e-new-story")
        story_title = f"E2E-Story-{TS}"
        submit_create_modal(page, story_title, "E2E 验证 Story")
        st, stories = api("GET", f"/epics/{eid}/stories")
        story = next((x for x in unwrap(stories) if x["title"] == story_title), None) if st == 200 else None
        record("Story 创建", story is not None, f"id={story and story['id']}")
        if not story:
            shot(page, "fail_story")
            browser.close()
            return
        sid = story["id"]
        shot(page, "03_story_created")

        # ---------- 4. Task 创建 ----------
        page.goto(f"{BASE}/story/{sid}", wait_until="domcontentloaded")
        wait_ready(page)
        page.click("button.btn--primary:has-text('新建')")
        task_title = f"E2E-Task-{TS}"
        submit_create_modal(page, task_title, "E2E 验证 Task")
        task = None
        for path in (f"/stories/{sid}/tasks", f"/projects/{pid}/tasks", "/tasks"):
            st, tasks = api("GET", path)
            if st == 200:
                task = next((x for x in unwrap(tasks) if x.get("title") == task_title), None)
                if task:
                    break
        record("Task 创建", task is not None, f"id={task and task['id']} status={task and task.get('status')}")
        if not task:
            shot(page, "fail_task")
            browser.close()
            return
        tid = task["id"]
        shot(page, "04_task_created")

        # ---------- 5. 评论创建 ----------
        page.goto(f"{BASE}/task/{tid}", wait_until="domcontentloaded")
        wait_ready(page)
        page.wait_for_selector("#comment-form", timeout=15000)
        page.fill("#comment-form input[name=author]", "E2E-Bot")
        comment_text = f"自动化验证评论 {TS}：部署与功能检查通过。"
        page.fill("#comment-form textarea[name=content]", comment_text)
        page.click("#comment-form button[type=submit]")
        page.wait_for_timeout(1200)
        st, comments = api("GET", f"/tasks/{tid}/comments")
        ok = st == 200 and any(comment_text in c.get("content", "") for c in unwrap(comments))
        record("评论创建", ok, f"count={len(comments) if isinstance(comments, list) else st}")
        shot(page, "05_comment_added")

        # ---------- 6. 状态变更（→ 下一状态 / ✓ 完成） ----------
        st, t0 = api("GET", f"/tasks/{tid}")
        s0 = t0.get("status") if st == 200 else None
        page.click("button:has-text('→ 下一状态')")
        page.wait_for_timeout(1200)
        st, t1 = api("GET", f"/tasks/{tid}")
        s1 = t1.get("status") if st == 200 else None
        record("任务状态变更(下一状态)", s1 is not None and s1 != s0, f"{s0} → {s1}")
        page.on("dialog", lambda d: d.accept())
        page.click("button:has-text('✓ 完成')")
        page.wait_for_timeout(1200)
        st, t2 = api("GET", f"/tasks/{tid}")
        s2 = t2.get("status") if st == 200 else None
        record("任务状态变更(完成)", str(s2).upper() == "DONE", f"{s1} → {s2}")
        shot(page, "06_status_changed")

        # ---------- 7. 文档创建 ----------
        page.goto(f"{BASE}/project/{pid}", wait_until="domcontentloaded")
        wait_ready(page)
        page.click("button.tab-btn:has-text('文档')")
        page.wait_for_timeout(600)
        page.click("button:has-text('＋ 新建文档')")
        page.wait_for_selector(".doc-modal", timeout=10000)
        # 项目下拉在项目 Tab 内应预选当前项目；若未选则手动选
        sel = page.locator(".doc-modal select").first
        if sel.input_value() in ("0", ""):
            sel.select_option(str(pid))
        doc_title = f"E2E-文档-{TS}"
        page.fill('.doc-modal input[placeholder="文档标题"]', doc_title)
        page.fill(".doc-modal textarea", f"# {doc_title}\n\n初始内容：Docker E2E 验证。")
        page.click(".doc-modal button:has-text('创建文档')")
        page.wait_for_selector(".doc-modal", state="detached", timeout=15000)
        page.wait_for_timeout(1000)
        st, docs = api("GET", f"/documents?project_id={pid}")
        doc = next((d for d in unwrap(docs) if d["title"] == doc_title), None) if st == 200 else None
        record("文档创建", doc is not None, f"id={doc and doc['id']}")
        shot(page, "07_doc_created")

        # ---------- 8. 文档更新 ----------
        if doc:
            did = doc["id"]
            page.click(f"text={doc_title}")
            page.wait_for_timeout(800)
            page.click("button:has-text('✎ 编辑')")
            page.wait_for_selector(".doc-modal", timeout=10000)
            updated = f"# {doc_title}\n\n更新内容：文档更新验证 OK（{TS}）。"
            page.fill(".doc-modal textarea", updated)
            page.click(".doc-modal button:has-text('保存')")
            page.wait_for_selector(".doc-modal", state="detached", timeout=15000)
            page.wait_for_timeout(1000)
            st, d2 = api("GET", f"/documents/{did}")
            ok = st == 200 and "更新内容：文档更新验证 OK" in d2.get("content", "")
            record("文档更新", ok)
            shot(page, "08_doc_updated")
        else:
            record("文档更新", False, "跳过：文档创建失败")

        browser.close()

    print("\n===== 汇总 =====")
    fails = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(f"  {'✅' if ok else '❌'} {name} {detail}")
    # 保存上下文供 MCP 测试复用
    Path(r"E:\Projects\WorkBuddy\AgentBoard\scripts\e2e_entities.json").write_text(
        json.dumps({"project_id": pid, "project_name": proj_name}, ensure_ascii=False), encoding="utf-8")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
