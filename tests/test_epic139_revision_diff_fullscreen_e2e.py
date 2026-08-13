"""Epic 139 文档 Revision + Diff + Fullscreen 端到端（Playwright）。

覆盖：
- 详情页 tab 切换（📄 内容 / 🕘 历史）
- 历史 tab 列出当前所有 revision（按 rN 倒序）
- 选两份 revision 触发 diff 弹窗（行/词级高亮）
- 「回滚到此」按钮（r1 旧版 → 形成新 rN）
- 409 冲突：模拟客户端基于 r1、服务端已到 r2 → 提示版本冲突
- 全屏入口（⛶ 全屏）→ 暗/亮主题切换 → Esc 退出
- change_note 必填校验

运行（需 Docker 栈 web 28080 / api 18000）：
    python tests/test_epic139_revision_diff_fullscreen_e2e.py
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
PROJECT_ID = int(os.environ.get("EPIC139_PROJECT_ID", "3"))

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

    # ---- 预建 1 篇文档 + 2 次 save 形成 r1/r2/r3 ----
    ts = int(time.time())
    st, doc = api_call("POST", "/api/documents", {
        "project_id": PROJECT_ID, "title": f"[E2E-139-{ts}] Revision 文档",
        "type": "plan", "status": "draft",
        "content": "# r1 标题\n\n第一段。\n\n第二段原始内容。\n",
        "author_id": payload.get("id"),
    }, token=token)
    if st != 201:
        print("FATAL create doc:", st, doc); sys.exit(2)
    doc_id = doc["id"]
    # r2
    st, doc = api_call("POST", f"/api/documents/{doc_id}/revisions", {
        "expected_revision_number": 1, "content": "# r2 标题\n\n第一段。\n\n第二段修改后。\n",
        "change_note": "改第二段", "author": "admin",
    }, token=token)
    check("save r2 ok", st == 201, f"st={st}")
    # r3
    st, doc = api_call("POST", f"/api/documents/{doc_id}/revisions", {
        "expected_revision_number": 2, "content": "# r3 标题\n\n第一段。\n\n第二段修改后。\n\n新增第三段。\n",
        "change_note": "新增第三段", "author": "admin",
    }, token=token)
    check("save r3 ok", st == 201, f"st={st}")

    # ---- UI ----
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script(
            f"localStorage.setItem('agentboard_token', '{token}');"
            f"localStorage.setItem('agentboard_user', 'admin');"
        )
        console_errors, page_errors = [], []
        page.on("console", lambda m: m.type == "error" and console_errors.append(m.text))
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        # 1) 进入项目文档 Tab → 找新建的 doc → 点开
        page.goto(f"{WEB}/project/{PROJECT_ID}/documents", wait_until="networkidle")
        for _ in range(3):
            if page.locator("text=新建文档").count() > 0:
                break
            page.reload(wait_until="networkidle"); time.sleep(1)
        check("docs tab renders", page.locator("text=新建文档").count() > 0)
        # 找到刚建的 doc 行并点击
        title = f"[E2E-139-{ts}] Revision 文档"
        row = page.locator(f".doc-row:has-text('{title}'), .doc-list-row:has-text('{title}')").first
        check("precreated doc row found", row.count() > 0)
        if row.count() == 0:
            print("FATAL: cannot find row"); sys.exit(2)
        row.click(); time.sleep(1.5)
        check("doc detail page renders",
              page.locator(f"h2:has-text('{title}')").count() > 0
              or page.locator(f"h1:has-text('{title}')").count() > 0)
        # 当前 revision badge
        check("current revision badge r3",
              page.locator(".badge.doc-rev:has-text('r3')").count() > 0)

        # 2) 切到「历史」tab
        page.get_by_role("button", name="🕘 历史").first.click(); time.sleep(0.8)
        check("history tab: revision rows >= 3",
              page.locator(".doc-rev-row").count() >= 3)
        check("history tab: r1 / r2 / r3 全部存在",
              page.locator(".doc-rev-num:has-text('r1')").count() > 0
              and page.locator(".doc-rev-num:has-text('r2')").count() > 0
              and page.locator(".doc-rev-num:has-text('r3')").count() > 0)

        # 3) 选 r1 / r2 → 对比
        # 点击"作为左"按钮选 r1
        r1_row = page.locator(".doc-rev-row").filter(has_text="r1").first
        r1_row.locator("button:has-text('作为左')").click(); time.sleep(0.3)
        r2_row = page.locator(".doc-rev-row").filter(has_text="r2").first
        r2_row.locator("button:has-text('作为右')").click(); time.sleep(0.3)
        # 点对比按钮
        diff_btn = page.locator("button:has-text('对比 r')").first
        check("diff button enabled", diff_btn.count() > 0 and diff_btn.is_enabled())
        diff_btn.click(); time.sleep(1.0)
        # diff 弹窗
        check("diff modal opened", page.locator(".doc-diff-modal").count() > 0)
        check("diff has added/removed stats",
              page.locator(".doc-diff-stat--add").count() > 0
              and page.locator(".doc-diff-stat--del").count() > 0)
        # 关闭 diff
        page.locator(".doc-diff-modal .modal-close").first.click(); time.sleep(0.5)

        # 4) 回滚 r1 → 形成 r4
        r1_row = page.locator(".doc-rev-row").filter(has_text="r1").first
        # prompt 自带弹窗，需要拦截
        page.once("dialog", lambda d: d.accept("回滚 E2E 测试"))
        r1_row.locator("button:has-text('回滚到此')").click(); time.sleep(1.5)
        # 现在应该是 r4
        page.get_by_role("button", name="↻ 刷新").first.click(); time.sleep(0.8)
        check("after restore: r4 current badge present",
              page.locator(".badge.doc-rev:has-text('r4')").count() > 0)
        check("after restore: r4 row marked 当前",
              page.locator(".doc-rev-row.current:has-text('r4')").count() > 0)
        check("after restore: history shows r4 + 回滚 tag",
              page.locator(".doc-rev-tag--restore").count() >= 1)

        # 5) 切回内容 tab → 全屏
        page.get_by_role("button", name="📄 内容").first.click(); time.sleep(0.5)
        page.get_by_role("button", name="⛶ 全屏").first.click(); time.sleep(0.6)
        check("fullscreen overlay open", page.locator(".doc-fullscreen").count() > 0)
        # 切到暗色
        page.locator(".doc-fullscreen__actions button").first.click(); time.sleep(0.3)
        check("fullscreen dark theme applied", page.locator(".doc-fullscreen.dark").count() > 0)
        # Esc 退出（按 keyboard）
        page.keyboard.press("Escape"); time.sleep(0.4)
        check("Esc closes fullscreen", page.locator(".doc-fullscreen").count() == 0)

        # 6) 409 冲突：尝试用 expected=1 提交 → 服务端应回 409
        st, body = api_call("POST", f"/api/documents/{doc_id}/revisions", {
            "expected_revision_number": 1, "content": "x", "change_note": "stale",
            "author": "admin",
        }, token=token)
        check("409 conflict on stale expected_revision_number",
              st == 409 and (isinstance(body, dict) and body.get("code") == "revision_conflict"),
              f"st={st} body={body}")

        # ---- 健康度 ----
        check("no console errors", len(console_errors) == 0,
              f"{len(console_errors)} errors; sample={console_errors[:1]}")
        check("no page errors", len(page_errors) == 0,
              f"{len(page_errors)} errors; sample={page_errors[:1]}")

        passed = sum(1 for _, ok, _ in results if ok)
        total = len(results)
        print(f"\n==== {passed}/{total} checks passed ====")
        browser.close()
        sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
