"""
Task 809 (Epic 16 / Story 48): 任务详情页显示 Epic/Story 面包屑 —— 端到端验证
- 登录 admin -> 进入项目 117 (AgentBoard) 的 epics 视图（同时装载 epics()/stories() 数组）
- 钻取 项目 -> Epic 126(架构设计) -> Story 199(实现 Story 任务视图界面) -> Task 1032
- 任务详情抽屉(.crumb-bar)应依次渲染：项目名 › Epic 名(.crumb-epic) › Story 名 › 任务标题(.crumb-current)
- 断言：0 pageerror / console error / .js+.css 404
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:28080"
API = "http://127.0.0.1:18000"
PROJECT_ID = 117
EPIC_ID = 126
STORY_ID = 199
TASK_ID = 1032
USER = "admin"
PASS = "admin123"


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
    errors = []
    try:
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

            # 1) 项目视图（初始加载，可靠触发 loadRoute，装载 epics()/stories() 数组）
            print("step1: goto /project/117")
            page.goto(WEB + f"/project/{PROJECT_ID}", wait_until="networkidle")
            page.wait_for_selector("a[href*='/epic/']", timeout=15000)
            page.wait_for_selector(f"a[href='/epic/{EPIC_ID}']", timeout=10000)

            # 2) 钻取 Epic 126
            print("step2: click epic", EPIC_ID)
            page.click(f"a[href='/epic/{EPIC_ID}']")
            page.wait_for_selector(f"a[href='/story/{STORY_ID}']", timeout=12000)

            # 3) 钻取 Story 199
            print("step3: click story", STORY_ID)
            page.click(f"a[href='/story/{STORY_ID}']")
            page.wait_for_selector(f"a[href='/task/{TASK_ID}']", timeout=12000)

            # 4) 打开 Task 详情抽屉
            print("step4: click task", TASK_ID)
            page.click(f"a[href='/task/{TASK_ID}']")
            page.wait_for_selector(".crumb-bar", timeout=12000)
            page.wait_for_timeout(500)

            # ---------- 断言面包屑 ----------
            crumb = page.locator(".crumb-bar")
            assert crumb.count() == 1, "crumb-bar should render exactly once"
            full = crumb.inner_text()
            print("CRUMB TEXT:", repr(full))

            # 项目名
            assert "AgentBoard" in full, f"crumb should contain project name 'AgentBoard', got: {full}"
            # Epic 名（专用 .crumb-epic 节点）
            epic_txt = crumb.locator(".crumb-epic").inner_text().strip()
            print("CRUMB-EPIC:", repr(epic_txt))
            assert epic_txt == "架构设计", f"crumb-epic should be '架构设计', got: {epic_txt}"
            # Story 名
            assert "实现 Story 任务视图界面" in full, f"crumb should contain story title, got: {full}"
            # 当前任务标题
            cur_txt = crumb.locator(".crumb-current").inner_text().strip()
            print("CRUMB-CURRENT:", repr(cur_txt))
            assert cur_txt.startswith("[199/10]"), f"crumb-current should be task title, got: {cur_txt}"
            # 面包屑层级顺序：项目 › Epic › Story › 任务
            seq = [x for x in [full.split("›")[0], epic_txt] if x]
            assert full.index("AgentBoard") < full.index("架构设计") < full.index("实现 Story"), \
                "crumb order should be project › epic › story › task"

            print("BREADCRUMB OK")
            browser.close()
    except Exception as e:
        print("TEST ERROR:", repr(e))
        errors.append("TEST_EXCEPTION:" + str(e))
        try:
            browser.close()
        except Exception:
            pass

    # ---------- 错误汇总 ----------
    real_errors = [e for e in errors if not (
        e.startswith("404:") and ("favicon" in e or "ERR_ABORTED" in e)
    )]
    print("ERRORS:", json.dumps(real_errors, ensure_ascii=False))
    if real_errors:
        print("RESULT: FAIL")
        sys.exit(1)
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
