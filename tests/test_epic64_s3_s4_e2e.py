"""Epic 64 S3/S4 评论与描述图片渲染端到端回归测试（Playwright）。

验证对象（本地 Docker 栈 web 28080 / api 18000，或可配 AGENTBOARD_WEB_URL / AGENTBOARD_API_URL）：
S3 评论图片：
- 任务评论（.comments-card .md.text-pre）含 ![](https://...) 渲染为 <img>
- quick-view 抽屉评论区（.qv-comment-body）渲染为 <img>
- 抽屉行内添加评论（UI 输入 ![](https://...)）→ 渲染 <img>
- 危险协议（javascript:/data:/onerror 属性逃逸）保持纯文本
S4 描述图片：
- 任务详情描述（.card.md.task-md）、Story 描述（.story-description）、Epic 描述（.card.md.task-md）
  含 ![](https://...) 渲染为 <img>
- 危险协议保持纯文本
全程 0 console error / pageerror / js/css 加载失败。
截图产物写入 tmp/。测试自建 Epic/Story/Task/评论，结束清理。

运行：python tests/test_epic64_s3_s4_e2e.py
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
PROJECT_ID = int(os.environ.get("EPIC64_PROJECT_ID", "3"))
SHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "tmp")

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
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8") or "{}"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8") or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


# COS 预签名风格合法 URL + 危险协议（XSS 用例）
IMG_OK = "https://cos.ap-shanghai.myqcloud.com/demo-bucket/e2e-s34.png?q-sign-algorithm=sha1&q-sign-time=1700000000;1700003600"
DESC_IMG = (
    "# S3/S4 图片渲染验收\n\n"
    "## 合法图片\n\n"
    f"![架构图]({IMG_OK})\n\n"
    "## 危险协议（应保持纯文本）\n\n"
    "![x](javascript:alert(1))\n\n"
    "![x](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)\n\n"
    '![x](https://ok.com/a.png" onerror="alert(1))\n\n'
    "**加粗共存**：图片应与其它 markdown 元素共存。\n"
)
COMMENT_OK = f"评审图 ![评审截图]({IMG_OK}) 请确认。"
COMMENT_BAD = "危险 ![x](javascript:alert(2)) 与 ![x](data:image/gif;base64,R0lGOD) 保持文本。"


def main():
    ts = int(time.time())
    created_epic = created_story = created_task = None
    created_comments = []

    # ---- auth ----
    st, payload = api_call("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    if st not in (200, 201) or not payload.get("token"):
        st, payload = api_call("POST", "/api/auth/register",
                               {"username": "admin", "password": "admin123"})
    token = payload.get("token")
    if not token:
        print("FATAL: no token:", st, payload)
        sys.exit(2)
    print(f"auth ok (admin), token len={len(token)}")

    try:
        # ---- 建测试 Epic / Story / Task（描述均含合法+危险图片） ----
        st, epic = api_call("POST", f"/api/projects/{PROJECT_ID}/epics", {
            "title": f"[E2E-{ts}] S4 Epic 描述图片",
            "description": DESC_IMG,
        }, token=token)
        check("create test epic", st == 201 and epic.get("id"), f"st={st}")
        if st != 201:
            errors.append(f"create epic: st={st} payload={epic}")
            print("FATAL: cannot create epic"); sys.exit(2)
        created_epic = epic["id"]

        st, story = api_call("POST", f"/api/epics/{created_epic}/stories", {
            "project_id": PROJECT_ID,
            "title": f"[E2E-{ts}] S4 Story 描述图片",
            "description": DESC_IMG,
        }, token=token)
        check("create test story", st == 201 and story.get("id"), f"st={st}")
        if st != 201:
            errors.append(f"create story: st={st} payload={story}")
            print("FATAL: cannot create story"); sys.exit(2)
        created_story = story["id"]

        st, task = api_call("POST", f"/api/stories/{created_story}/tasks", {
            "project_id": PROJECT_ID,
            "title": f"[E2E-{ts}] S3/S4 图片渲染任务",
            "description": DESC_IMG,
            "type": "task",
            "priority": "medium",
            "status": "backlog",
        }, token=token)
        check("create test task", st == 201 and task.get("id"), f"st={st}")
        if st != 201:
            errors.append(f"create task: st={st} payload={task}")
            print("FATAL: cannot create task"); sys.exit(2)
        created_task = task["id"]

        # ---- 预置评论：任务 2 条（合法+危险）、Story 1 条（合法） ----
        for cid, content in [(None, COMMENT_OK), (None, COMMENT_BAD)]:
            st, c = api_call("POST", f"/api/tasks/{created_task}/comments",
                             {"author": "tester", "content": content}, token=token)
            check(f"seed task comment (ok={COMMENT_OK in content})", st == 201 and c.get("id"), f"st={st}")
            if st == 201:
                created_comments.append(c["id"])
        st, c = api_call("POST", f"/api/stories/{created_story}/comments",
                         {"author": "tester", "content": COMMENT_OK}, token=token)
        check("seed story comment", st == 201 and c.get("id"), f"st={st}")
        if st == 201:
            created_comments.append(c["id"])

        os.makedirs(SHOT_DIR, exist_ok=True)

        # ---- UI 验证 ----
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
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

            # ---------- S4-A 任务详情描述图片（.two-col .task-md） ----------
            page.goto(f"{WEB}/task/{created_task}", wait_until="networkidle")
            page.wait_for_selector(".two-col .task-md", timeout=15000)
            time.sleep(0.8)
            desc = page.locator(".two-col .task-md").first
            imgs = desc.locator("img")
            check("task desc https image -> <img>", imgs.count() >= 1, f"count={imgs.count()}")
            if imgs.count() >= 1:
                src = imgs.first.get_attribute("src") or ""
                check("task desc img src = COS URL", "cos.ap-shanghai.myqcloud.com" in src, src[:60])
            body = desc.inner_text()
            check("task desc javascript: 未渲染", "javascript:alert(1)" in body, "")
            check("task desc data: 未渲染", "data:image/svg+xml" in body, "")
            check("task desc 仅 1 个合法 <img>", desc.locator("img").count() == 1,
                  f"count={desc.locator('img').count()}")

            # ---------- S3-A 任务详情评论区图片（.comments-card .md.text-pre） ----------
            page.wait_for_selector(".comments-card .md.text-pre", timeout=10000)
            time.sleep(0.8)
            comment_imgs = page.locator(".comments-card .md.text-pre img")
            check("task comment https image -> <img>", comment_imgs.count() >= 1,
                  f"count={comment_imgs.count()}")
            cbody = page.locator(".comments-card").inner_text()
            check("task comment javascript: 未渲染", "javascript:alert(2)" in cbody, "")
            check("task comment data: 未渲染", "data:image/gif" in cbody, "")

            # ---------- S4-B Story 描述图片（.story-description） ----------
            page.goto(f"{WEB}/story/{created_story}", wait_until="networkidle")
            page.wait_for_selector(".story-description", timeout=15000)
            time.sleep(0.8)
            simgs = page.locator(".story-description img")
            check("story desc https image -> <img>", simgs.count() >= 1, f"count={simgs.count()}")
            sbody = page.locator(".story-description").inner_text()
            check("story desc javascript: 未渲染", "javascript:alert(1)" in sbody, "")
            check("story desc 仅 1 个合法 <img>", page.locator(".story-description img").count() == 1,
                  f"count={page.locator('.story-description img').count()}")

            # ---------- S3-B Story 评论区图片（seed story comment） ----------
            page.wait_for_selector(".comments-card", timeout=10000)
            time.sleep(0.8)
            scomment_imgs = page.locator(".comments-card .md.text-pre img")
            check("story comment https image -> <img>", scomment_imgs.count() >= 1,
                  f"count={scomment_imgs.count()}")

            # ---------- S4-C Epic 描述图片（.card.md.task-md in epic detail） ----------
            page.goto(f"{WEB}/epic/{created_epic}", wait_until="networkidle")
            page.wait_for_selector(".detail-panel .card.md.task-md", timeout=15000)
            time.sleep(0.8)
            eimgs = page.locator(".detail-panel .card.md.task-md img")
            check("epic desc https image -> <img>", eimgs.count() >= 1, f"count={eimgs.count()}")
            ebody = page.locator(".detail-panel .card.md.task-md").inner_text()
            check("epic desc javascript: 未渲染", "javascript:alert(1)" in ebody, "")
            check("epic desc 仅 1 个合法 <img>",
                  page.locator(".detail-panel .card.md.task-md img").count() == 1,
                  f"count={page.locator('.detail-panel .card.md.task-md img').count()}")

            # ---------- S3-C quick-view 抽屉：qv-desc 描述图 + qv-comment-body 评论图 + 行内添加 ----------
            page.goto(f"{WEB}/story/{created_story}", wait_until="networkidle")
            page.wait_for_selector(".tab-btn", timeout=15000)
            # 切到 Task 列表 tab（默认是详情 tab）
            page.locator(".tab-btn", has_text="Task 列表").click()
            page.wait_for_selector(".entity-item--rich", timeout=15000)
            row = page.locator(".entity-item--rich", has_text="S3/S4")
            check("task row listed", row.count() > 0)
            row.locator(".task-quick-view-btn").click()
            page.wait_for_selector(".quick-view-drawer", timeout=8000)
            time.sleep(0.8)

            qvimgs = page.locator(".quick-view-drawer .qv-desc img")
            check("drawer desc https image -> <img>", qvimgs.count() >= 1, f"count={qvimgs.count()}")
            qvbody = page.locator(".quick-view-drawer .qv-desc").inner_text()
            check("drawer desc javascript: 未渲染", "javascript:alert(1)" in qvbody, "")

            page.wait_for_selector(".quick-view-drawer .qv-comment", timeout=8000)
            qvcimgs = page.locator(".quick-view-drawer .qv-comment-body img")
            check("drawer comment https image -> <img>", qvcimgs.count() >= 1, f"count={qvcimgs.count()}")
            qvcbody = page.locator(".quick-view-drawer .qv-comments").inner_text()
            check("drawer comment javascript: 未渲染", "javascript:alert(2)" in qvcbody, "")

            # 行内添加图片评论（UI 提交）→ 渲染 <img>
            n_before = page.locator(".quick-view-drawer .qv-comment").count()
            page.locator(".quick-view-drawer .qv-comment-input").fill(f"UI 图片评论 ![新截图]({IMG_OK})")
            page.locator(".quick-view-drawer .qv-comment-actions .btn--primary").click()
            page.wait_for_timeout(1200)
            n_after = page.locator(".quick-view-drawer .qv-comment").count()
            check("drawer inline add comment ok", n_after == n_before + 1, f"{n_before}->{n_after}")
            check("drawer inline image rendered", page.locator(".quick-view-drawer .qv-comment-body img").count() >= 2,
                  f"count={page.locator('.quick-view-drawer .qv-comment-body img').count()}")

            # 截图
            page.screenshot(path=os.path.join(SHOT_DIR, f"epic64_s3_s4_drawer_{ts}.png"), full_page=True)

            # ---------- 错误汇总 ----------
            check("0 console error", len(console_errors) == 0, "; ".join(console_errors[:3]))
            check("0 pageerror", len(page_errors) == 0, "; ".join(page_errors[:3]))
            check("0 js/css load failure", len(failed_resources) == 0, "; ".join(failed_resources[:3]))

            browser.close()
    finally:
        # ---- 清理：评论 → 任务 → Story → Epic ----
        for cid in created_comments:
            api_call("DELETE", f"/api/comments/{cid}", token=token)
        if created_task:
            api_call("DELETE", f"/api/tasks/{created_task}", token=token)
        if created_story:
            api_call("DELETE", f"/api/stories/{created_story}", token=token)
        if created_epic:
            api_call("DELETE", f"/api/epics/{created_epic}", token=token)
        print("cleanup done")

    failed = [r for r in results if not r[1]]
    print(f"\n==== {len(results) - len(failed)}/{len(results)} passed ====")
    if failed:
        print("FAILED:", [r[0] for r in failed])
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
