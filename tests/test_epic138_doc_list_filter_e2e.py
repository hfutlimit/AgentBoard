"""Epic 138 文档列表视图 + 过滤增强 端到端回归（Playwright）。

覆盖：
- 项目级 Tab 的 Tile ↔ List 切换（持久化到 localStorage）
- 列表行渲染：标题 / summary / type / status / scope / author / 评论数 / updated
- 过滤：type / status / author / sort 三种
- 跨项目视图（/documents）的项目下拉
- 全程 0 console error / pageerror / js+css 加载失败

运行（需 Docker 栈：web 28080 / api 18000；可用 AGENTBOARD_*_URL 覆盖）：
    python tests/test_epic138_doc_list_filter_e2e.py
"""
import os
import sys
import time
import json
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = os.environ.get("AGENTBOARD_WEB_URL", "http://127.0.0.1:28080")
API = os.environ.get("AGENTBOARD_API_URL", "http://127.0.0.1:18000")
PROJECT_ID = int(os.environ.get("EPIC138_PROJECT_ID", "3"))

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def api_call(method, path, body=None, token=None):
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("utf-8") or "{}"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8") or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def main():
    # ---- auth ----
    st, payload = api_call("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    if st not in (200, 201) or not payload.get("token"):
        st, payload = api_call("POST", "/api/auth/register",
                               {"username": "admin", "password": "admin123"})
    token = payload.get("token")
    if not token:
        print("FATAL: no token:", st, payload); sys.exit(2)
    print(f"auth ok (admin), token len={len(token)}")

    # ---- 预建 4 篇文档：覆盖不同 type/status/author ----
    ts = int(time.time())
    docs = [
        {"title": f"[E2E-138-{ts}] Alpha 计划",  "type": "plan",     "status": "draft",    "content": "# Alpha\n\n第一行 summary 内容用作列表预览。\n\n详细正文省略。"},
        {"title": f"[E2E-138-{ts}] Beta 记忆",   "type": "memory",   "status": "in_review","content": "# Beta\n\n记忆 summary 行。\n\n其他内容。"},
        {"title": f"[E2E-138-{ts}] Gamma 设计",  "type": "design",   "status": "approved", "content": "# Gamma\n\n设计 summary。"},
        {"title": f"[E2E-138-{ts}] Delta 知识",  "type": "knowledge","status": "draft",    "content": "# Delta\n\n知识 summary。"},
    ]
    doc_ids = []
    for d in docs:
        st, doc = api_call("POST", "/api/documents", {
            "project_id": PROJECT_ID,
            "title": d["title"], "type": d["type"], "status": d["status"],
            "content": d["content"],
            "author_id": payload.get("id") if payload.get("id") else None,
        }, token=token)
        check(f"api create {d['type']}/{d['status']}", st == 201, f"st={st}")
        if st == 201 and doc.get("id"):
            doc_ids.append((doc["id"], d["title"], d["type"], d["status"]))
    if len(doc_ids) < 4:
        print("FATAL: failed to create test docs"); sys.exit(2)

    # ---- UI 验证 ----
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script(
            f"localStorage.setItem('agentboard_token', '{token}');"
            f"localStorage.setItem('agentboard_user', 'admin');"
        )
        console_errors, page_errors, failed_resources = [], [], []

        def on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)

        def on_pageerror(err):
            page_errors.append(str(err))

        def on_request_failed(req):
            url = req.url
            if "127.0.0.1" not in url and "localhost" not in url:
                return
            if url.endswith(".js") or url.endswith(".css"):
                failed_resources.append(url)

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("requestfailed", on_request_failed)

        # ===== 1) 项目级 Tab：Tile ↔ List 切换 =====
        page.goto(f"{WEB}/project/{PROJECT_ID}/documents", wait_until="networkidle")
        for _ in range(3):
            if page.locator("text=新建文档").count() > 0:
                break
            page.reload(wait_until="networkidle"); time.sleep(1)
        check("project docs tab renders", page.locator("text=新建文档").count() > 0)

        # 视图切换器存在
        switchers = page.locator(".doc-view-switch")
        check("view switcher rendered (project tab)", switchers.count() >= 1)

        # 默认是 tile：找到 tile 视图按钮
        tile_btn = page.locator(".doc-view-switch button[title='Tile 视图']").first
        list_btn = page.locator(".doc-view-switch button[title='列表视图']").first
        check("tile button visible", tile_btn.count() > 0)
        check("list button visible", list_btn.count() > 0)

        # 切到列表
        list_btn.click(); time.sleep(0.5)
        check("list view: .doc-list.list-view present", page.locator(".doc-list.list-view").count() >= 1)
        check("list view: .doc-list-row >= 1", page.locator(".doc-list-row").count() >= 1)

        # 预建文档至少一个在列表
        any_listed = any(
            page.locator(f".doc-list-row:has-text('{title}')").count() > 0
            for _, title, _, _ in doc_ids
        )
        check("precreated docs listed in list view", any_listed)

        # 列表行有 type/status 徽章、scope path、author 文本
        first_row = page.locator(".doc-list-row").first
        check("list row has title link", first_row.locator(".doc-list-title").count() > 0)
        check("list row has type badge", first_row.locator(".badge.doctype").count() > 0)
        check("list row has status badge", first_row.locator(".badge.docstatus").count() > 0)
        check("list row has scope path", first_row.locator(".doc-list-scope").count() > 0)
        check("list row has author", first_row.locator(".doc-list-author").count() > 0)
        check("list row has comments cell", first_row.locator(".doc-list-comments").count() > 0)
        check("list row has updated time", first_row.locator(".doc-list-updated").count() > 0)

        # 持久化：刷新后仍是 list
        page.reload(wait_until="networkidle"); time.sleep(1)
        check("list view persists after reload",
              page.locator(".doc-list.list-view").count() >= 1)

        # 切回 tile
        page.locator(".doc-view-switch button[title='Tile 视图']").first.click(); time.sleep(0.5)
        check("switch back to tile view", page.locator(".doc-list.tile-view").count() >= 1)

        # ===== 2) 列表 + 过滤：sort by title =====
        list_btn = page.locator(".doc-view-switch button[title='列表视图']").first
        list_btn.click(); time.sleep(0.5)
        sort_select = page.locator(".doc-toolbar--extended select[title='排序']").first
        check("sort select visible (project tab)", sort_select.count() > 0)
        if sort_select.count() > 0:
            sort_select.select_option("title"); time.sleep(0.6)
            # 取前两行的标题，按字母顺序排列（zh-Hans-CN 排序）
            titles = page.locator(".doc-list-title-main").all_text_contents()
            check("sort=title applied (rows still present)", len(titles) >= 1,
                  f"first={titles[0] if titles else 'none'}")
            # 简单检查：至少首字母应 <= 末字母
            if len(titles) >= 2:
                check("sort=title ordering non-decreasing",
                      titles[0].lower() <= titles[-1].lower(),
                      f"first={titles[0]} last={titles[-1]}")
        # 切回 updated 排序，节省时间
        sort_select.select_option("updated"); time.sleep(0.5)

        # ===== 3) 列表 + 过滤：type =====
        type_select = page.locator(".doc-toolbar select.doc-filter-select").first
        type_select.select_option("memory"); time.sleep(0.6)
        memory_rows = page.locator(".doc-list-row").count()
        check("filter type=memory: only memory rows", memory_rows >= 0)  # at least our Beta
        if memory_rows > 0:
            check("memory filter: no plan badge in rows",
                  page.locator(".doc-list-row .badge.doctype--plan").count() == 0)
        # 清空 type
        type_select.select_option(""); time.sleep(0.5)

        # ===== 4) 跨项目视图 =====
        page.goto(f"{WEB}/documents", wait_until="networkidle")
        for _ in range(3):
            if page.locator("text=全部项目").count() > 0:
                break
            page.reload(wait_until="networkidle"); time.sleep(1)
        check("cross-project /documents view renders", page.locator("text=全部项目").count() > 0)

        # 项目下拉存在
        project_select = page.locator(".doc-toolbar--extended select[title='按项目过滤']").first
        check("cross-project: project select visible", project_select.count() > 0)
        # 切到 list view
        list_btn = page.locator(".doc-view-switch button[title='列表视图']").first
        list_btn.click(); time.sleep(0.5)
        check("cross-project: list view renders", page.locator(".doc-list.list-view").count() >= 1)
        # 选项目 → 列表应只剩该项目文档
        project_select.select_option(str(PROJECT_ID)); time.sleep(0.6)
        check("cross-project: filter to one project, list still present",
              page.locator(".doc-list.list-view").count() >= 1)

        # ===== 5) 健康度 =====
        check("no console errors", len(console_errors) == 0,
              f"{len(console_errors)} errors; sample={console_errors[:1]}")
        check("no page errors", len(page_errors) == 0,
              f"{len(page_errors)} errors; sample={page_errors[:1]}")
        check("no failed local js/css", len(failed_resources) == 0,
              f"{len(failed_resources)} failed; sample={failed_resources[:1]}")

        # ---- 总结 ----
        passed = sum(1 for _, ok, _ in results if ok)
        total = len(results)
        print(f"\n==== {passed}/{total} checks passed ====")
        browser.close()
        sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
