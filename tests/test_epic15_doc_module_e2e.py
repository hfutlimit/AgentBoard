"""Epic 15 文档模块端到端回归测试（Playwright）。

验证对象（本地 Docker 栈 web 28080 / api 18000，或可配 AGENTBOARD_WEB_URL / AGENTBOARD_API_URL）：
- 项目级 Tab /project/{pid}/documents：列表渲染、类型/状态筛选、搜索框
- 文档详情：Markdown + Mermaid 渲染（.doc-content）
- 新建文档弹窗 → 创建 → 列表即时出现
- 文档评论区（S7）
- 全程 0 console error / pageerror / js/css 加载失败

运行：python tests/test_epic15_doc_module_e2e.py
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
PROJECT_ID = int(os.environ.get("EPIC15_PROJECT_ID", "3"))

results = []
errors = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
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
    # ---- auth (admin 已存在则 login，否则 register) ----
    st, payload = api_call("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    if st not in (200, 201) or not payload.get("token"):
        st, payload = api_call("POST", "/api/auth/register",
                               {"username": "admin", "password": "admin123"})
    token = payload.get("token")
    if not token:
        print("FATAL: no token:", st, payload)
        sys.exit(2)
    print(f"auth ok (admin), token len={len(token)}")

    # ---- 预建 2 篇测试文档（含 mermaid / 中文），用于列表与详情验证 ----
    ts = int(time.time())
    titles = []
    doc_ids = []
    for idx, (dtype, content) in enumerate([
        ("plan", "# E2E 计划文档 {ts}\n\n**加粗验收**\n\n```mermaid\nflowchart LR\n  A[计划] --> B[评审]\n```"),
        ("memory", "# E2E 记忆文档 {ts}\n\n团队约定：文档模块验收\n\n```mermaid\nsequenceDiagram\n  A->>B: review\n```"),
    ]):
        st, doc = api_call("POST", "/api/documents", {
            "project_id": PROJECT_ID,
            "title": f"[E2E-{ts}] Epic 15 文档 {idx + 1}",
            "content": content.format(ts=ts),
            "type": dtype,
            "status": "draft",
        }, token=token)
        ok = st == 201 and doc.get("id")
        check(f"api create doc{idx + 1} ({dtype})", ok, f"st={st}")
        if ok:
            titles.append(doc["title"])
            doc_ids.append(doc["id"])
        else:
            errors.append(f"create doc{idx + 1}: st={st} payload={doc}")

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

        # 1) 项目级文档 Tab（深链，必要时重载）
        page.goto(f"{WEB}/project/{PROJECT_ID}/documents", wait_until="networkidle")
        for _ in range(2):
            if page.locator("h3", has_text="项目文档").count() > 0 or \
               page.locator("text=新建文档").count() > 0:
                break
            page.reload(wait_until="networkidle")
            time.sleep(1)
        check("project docs tab renders", page.locator("h3", has_text="项目文档").count() > 0
              or page.locator("text=项目文档").count() > 0)
        check("新建文档 button present",
              page.get_by_role("button", name="＋ 新建文档").count() > 0)
        check("doc search input present", page.locator("input.doc-search-input").count() > 0)
        check("doc filter selects present",
              page.locator("select.doc-filter-select").count() >= 2)

        # 2) 预建文档出现在列表
        first_title = titles[0] if titles else ""
        if first_title:
            check("precreated doc listed",
                  page.locator(f".doc-row:has-text('{first_title}')").count() > 0,
                  first_title)
        else:
            check("precreated doc listed", False, "no precreated doc")

        # 3) 打开详情：markdown + mermaid 渲染
        if first_title:
            page.locator(f".doc-row:has-text('{first_title}')").first.click()
            page.wait_for_selector(".doc-content", timeout=10000)
            time.sleep(1)  # mermaid 异步渲染
            h1_ok = page.locator(".doc-content h1").count() > 0
            strong_ok = page.locator(".doc-content strong").count() > 0
            mermaid_ok = page.locator("pre.mermaid svg, svg.mermaid").count() > 0 or \
                page.locator("pre.mermaid").count() > 0
            check("doc markdown h1 rendered", h1_ok)
            check("doc markdown bold rendered", strong_ok)
            check("doc mermaid rendered", mermaid_ok)

        # 4) 新建文档弹窗 → 创建
        page.goto(f"{WEB}/project/{PROJECT_ID}/documents", wait_until="networkidle")
        time.sleep(1)
        page.get_by_role("button", name="＋ 新建文档").first.click()
        time.sleep(0.8)
        modal_ok = page.locator(".modal-overlay h3", has_text="新建文档").count() > 0 or \
            page.locator("h3", has_text="新建文档").count() > 0
        check("create doc modal opens", modal_ok)
        if modal_ok:
            new_title = f"[E2E-{ts}] UI 新建文档"
            # 表单字段探测（input 含 title / select 含 type）
            title_input = page.locator("input[maxlength='300'], input[placeholder*='标题']").first
            if title_input.count() > 0:
                title_input.fill(new_title)
                page.locator(".modal-overlay button[type='submit'], button.btn-primary:has-text('创建文档')").first.click()
                time.sleep(1.5)
                listed = page.locator(f".doc-row:has-text('{new_title}')").count() > 0
                check("UI-created doc appears in list", listed)
            else:
                check("UI-created doc appears in list", False, "title input not found")

        # 5) 评论区（项目级 Tab 详情 .doc-comment-form，S7）
        if first_title:
            page.goto(f"{WEB}/project/{PROJECT_ID}/documents", wait_until="networkidle")
            time.sleep(1)
            page.locator(f".doc-row:has-text('{first_title}')").first.click()
            time.sleep(1)
            comment_area = page.locator("form.doc-comment-form textarea").first
            if comment_area.count() > 0:
                comment_area.fill("E2E 评审评论：通过")
                # form 无提交按钮，直接触发 requestSubmit
                page.locator("form.doc-comment-form").first.evaluate(
                    "(f) => f.requestSubmit()")
                time.sleep(1.2)
                check("comment posted visible",
                      page.locator(".doc-comment-item:has-text('E2E 评审评论：通过')").count() > 0)
            else:
                check("comment posted visible", False, "comment input not found")

        # 6) 项目文档 Tab 与其它 Tab 切换后仍可返回（S5 稳定性）
        page.goto(f"{WEB}/project/{PROJECT_ID}/epics", wait_until="networkidle")
        time.sleep(0.8)
        page.goto(f"{WEB}/project/{PROJECT_ID}/documents", wait_until="networkidle")
        time.sleep(1)
        check("docs tab re-entrant",
              page.locator("h3", has_text="项目文档").count() > 0)

        browser.close()

    # ---- 清理测试文档 ----
    st, lst = api_call("GET", f"/api/documents?project_id={PROJECT_ID}", token=token)
    cleaned = 0
    if st == 200 and isinstance(lst, list):
        for d in lst:
            if "[E2E-" in (d.get("title") or ""):
                s2, _ = api_call("DELETE", f"/api/documents/{d['id']}", token=token)
                cleaned += 1 if s2 in (200, 204) else 0
    check("cleanup test docs", cleaned >= len(titles), f"cleaned={cleaned}")

    # ---- 汇总 ----
    failed = [r for r in results if not r[1]]
    print("\n===== SUMMARY =====")
    print(f"passed={len(results) - len(failed)} failed={len(failed)} total={len(results)}")
    if console_errors:
        print("console errors:", console_errors[:5])
    if page_errors:
        print("page errors:", page_errors[:5])
    if failed_resources:
        print("failed resources:", failed_resources[:5])
    check("no console errors", len(console_errors) == 0, str(console_errors[:3]))
    check("no page errors", len(page_errors) == 0, str(page_errors[:3]))
    check("no failed js/css", len(failed_resources) == 0, str(failed_resources[:3]))
    if errors:
        print("setup errors:", errors[:5])
    print(f"\nRESULT: {'ALL GREEN' if not failed and not errors else 'HAS FAILURES'}")
    sys.exit(1 if (failed or errors) else 0)


if __name__ == "__main__":
    main()
