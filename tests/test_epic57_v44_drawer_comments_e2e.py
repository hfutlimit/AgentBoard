"""
Epic 57 (v4.4) 快速查看抽屉评论区 —— 端到端验证
- 登录 admin -> 进入 story 25（任务列表）
- 创建种子任务并经由 API 预置 1 条评论（验证「查看评论」渲染）
- 打开抽屉：评论区渲染、评论列表(作者/时间/Markdown 内容)、计数徽标
- 行内添加评论：textarea 输入 + 发送 -> API 复核新增 + 列表即时更新
- 行内删除评论：点删除 -> API 复核移除 + 列表即时更新
- 断言：0 pageerror / console error / .js+.css 404
- 测试末清理自建评论与任务，不污染数据
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:28080"
API = "http://127.0.0.1:18000"
STORY_ID = 25
PROJECT_ID = 3
USER = "admin"
PASS = "admin123"

SEED = "__E2E_QV_COMMENT__" + str(1784951400)


def api(method, path, token=None, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def login():
    st, u = api("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    assert st == 200, f"login failed {st}"
    return u["token"], u["username"]


def main():
    token, username = login()
    created = []
    created_comments = []
    errors = []
    try:
        # 创建种子任务
        st, t = api(
            "POST",
            f"/api/stories/{STORY_ID}/tasks",
            token=token,
            body={
                "project_id": PROJECT_ID,
                "story_id": STORY_ID,
                "title": SEED,
                "type": "task",
                "priority": "medium",
                "status": "backlog",
                "description": "E2E 种子任务：用于验证快速查看抽屉评论区。",
            },
        )
        assert st == 201, f"create task failed {st} {t}"
        tid = t["id"]
        created.append(tid)
        print("created seed task:", tid)

        # 经由 API 预置 1 条评论（验证「查看评论」渲染）
        st, c = api(
            "POST",
            f"/api/tasks/{tid}/comments",
            token=token,
            body={"author": "tester", "content": "**种子评论** 这是 `markdown` 内容"},
        )
        assert st == 201, f"create comment failed {st} {c}"
        created_comments.append(c["id"])
        print("seeded comment:", c["id"])

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console:" + m.type + ":" + m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: (
                errors.append("404:" + r.url) if (r.url.endswith(".js") or r.url.endswith(".css")) else None
            ))
            page.add_init_script(
                f"localStorage.setItem('agentboard_token','{token}');"
                f"localStorage.setItem('agentboard_user','{username}');"
            )
            page.goto(WEB + f"/story/{STORY_ID}", wait_until="networkidle")
            page.wait_for_selector(".entity-item--rich", timeout=15000)

            row = page.locator(".entity-item--rich", has_text=SEED)
            assert row.count() == 1, "seed task row not found"

            # ---------- 打开快速查看抽屉 ----------
            row.locator(".task-quick-view-btn").click()
            page.wait_for_selector(".quick-view-drawer", timeout=8000)
            print("drawer opened")

            # ---------- 评论区渲染 ----------
            assert page.locator(".quick-view-drawer .qv-comments").count() == 1, "comments section should render"
            page.wait_for_selector(".quick-view-drawer .qv-comment", timeout=8000)
            assert page.locator(".quick-view-drawer .qv-comment").count() == 1, "should show 1 seeded comment"

            # Markdown 渲染校验
            body_html = page.locator(".quick-view-drawer .qv-comment-body").first.inner_html()
            assert "<strong>" in body_html, "markdown strong should render"
            assert "tester" in page.locator(".quick-view-drawer .qv-comment-author").first.inner_text()

            # 计数徽标
            assert page.locator(".quick-view-drawer .qv-comments-count").inner_text().strip() == "1", "count badge should be 1"

            # ---------- 行内添加评论 ----------
            page.locator(".quick-view-drawer .qv-comment-input").fill("UI 添加的评论 *italic text*")
            page.locator(".quick-view-drawer .qv-comment-actions .btn--primary").click()
            page.wait_for_timeout(1000)
            assert page.locator(".quick-view-drawer .qv-comment").count() == 2, "should have 2 comments after UI add"
            stt, lst = api("GET", f"/api/tasks/{tid}/comments", token=token)
            assert stt == 200 and len(lst) == 2, f"api should return 2 comments, got {len(lst)}"
            print("UI add comment verified via API")

            # ---------- 行内删除评论 ----------
            page.locator(".quick-view-drawer .qv-comment").first.hover()
            page.locator(".quick-view-drawer .qv-comment-del").first.click()
            page.wait_for_timeout(900)
            assert page.locator(".quick-view-drawer .qv-comment").count() == 1, "should have 1 comment after delete"
            stt, lst = api("GET", f"/api/tasks/{tid}/comments", token=token)
            assert stt == 200 and len(lst) == 1, f"api should return 1 comment after delete, got {len(lst)}"
            print("UI delete comment verified via API")

            # ---------- Esc 关闭抽屉 ----------
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            assert page.locator(".quick-view-drawer").count() == 0, "drawer should close on Escape"

            browser.close()
    finally:
        for cid in created_comments:
            api("DELETE", f"/api/comments/{cid}", token=token)
        for tid in created:
            api("DELETE", f"/api/tasks/{tid}", token=token)

    real_errors = [e for e in errors if "ERR_ABORTED" not in e and "ABORTED" not in e]
    if real_errors:
        print("ERRORS:", real_errors)
        sys.exit(1)
    print("E2E PASSED: 0 pageerror/console/.js+.css 404")


if __name__ == "__main__":
    main()
