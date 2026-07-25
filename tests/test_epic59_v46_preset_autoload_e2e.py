"""
Epic 59 (v4.6) 筛选预设默认加载自动应用 —— 端到端验证
- 登录 admin -> 进入 story 98（含 2 个确定性种子任务，均为 backlog；另有追踪 task 1109 为 in_review）
- 点击「待规划」(backlog) 状态 chip -> 列表收窄为 2 行（仅 2 个 backlog 种子；in_review 追踪任务被排除）
- 打开预设面板，保存当前筛选为命名预设，并设为默认（星标）
- 刷新页面（同上下文，localStorage 持久化预设）
- 断言：刷新后「完成」chip 自动处于激活态（auto-apply 生效）、列表仍为 1 行、预设标记为默认
- 断言：0 pageerror / console error / .js+.css 404
- 测试末清理 2 个种子任务，不污染数据（保留追踪 task 1109 / story 98 / epic 49）
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
STORY_ID = 98
PROJECT_ID = 45
USER = "admin"
PASS = "admin123"

SEED = "__E2E_PRESET_AUTO__" + str(1785000100)


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
    errors = []
    try:
        # 创建 2 个确定性种子任务：1 个 done / 1 个 backlog，用于验证状态筛选收窄
        st, t_done = api(
            "POST", f"/api/stories/{STORY_ID}/tasks", token=token,
            body={"project_id": PROJECT_ID, "story_id": STORY_ID, "title": SEED + "-DONE",
                  "type": "task", "priority": "low", "status": "done",
                  "description": "E2E 种子：done 状态任务"},
        )
        assert st == 201, f"create done task {st} {t_done}"
        created.append(t_done["id"])
        st, t_back = api(
            "POST", f"/api/stories/{STORY_ID}/tasks", token=token,
            body={"project_id": PROJECT_ID, "story_id": STORY_ID, "title": SEED + "-BACK",
                  "type": "task", "priority": "low", "status": "backlog",
                  "description": "E2E 种子：backlog 状态任务"},
        )
        assert st == 201, f"create back task {st} {t_back}"
        created.append(t_back["id"])
        print("created seed tasks:", created)

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
                "localStorage.setItem('agentboard_sort_key','created_at');"
                "localStorage.setItem('agentboard_sort_order','asc');"
            )
            page.goto(WEB + f"/story/{STORY_ID}", wait_until="networkidle")
            page.wait_for_selector(".entity-item--rich", timeout=15000)

            # 初始应见 2 个种子任务 + 1 个追踪任务（1109, in_review）共 3 行
            rows0 = page.locator(".entity-item--rich").count()
            assert rows0 == 3, f"expected 3 rows (2 seeds + tracking task) before filter, got {rows0}"

            # 点击「待规划」(backlog) 状态 chip -> 列表收窄为 2 行（仅 2 个 backlog 种子；in_review 追踪任务被排除）
            backlog_chip = page.locator(".chips .chip", has_text="待规划")
            assert backlog_chip.count() == 1, "backlog chip not found in .chips"
            backlog_chip.click()
            page.wait_for_timeout(400)
            rows1 = page.locator(".entity-item--rich").count()
            assert rows1 == 2, f"after backlog filter expected 2 rows, got {rows1}"
            print("backlog filter -> 2 rows (seed tasks)")

            # 打开预设面板并保存当前筛选为命名预设
            page.locator(".preset-wrap .dropdown").click()
            page.wait_for_selector(".preset-save", timeout=5000)
            page.locator(".preset-name-input").fill("AutoDefault")
            page.locator(".preset-save .btn--primary").click()
            page.wait_for_selector(".preset-list .preset-item", timeout=5000)
            print("preset saved")

            # 设为默认（点击星标）
            star = page.locator(".preset-list .preset-item .preset-star").first
            star.click()
            page.wait_for_timeout(300)
            assert page.locator(".preset-list .preset-item.is-default").count() >= 1, "preset not marked default"
            print("preset marked default")

            # 刷新页面（同上下文，localStorage 持久化默认预设）
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".entity-item--rich", timeout=15000)
            page.wait_for_timeout(500)

            # 断言 1：刷新后「待规划」chip 自动激活（auto-apply 生效）
            active = page.locator(".chips .chip.is-active")
            assert active.count() == 1, f"expected exactly 1 active status chip after reload, got {active.count()}"
            assert "待规划" in active.first.inner_text(), "active chip should be 待规划 (default preset auto-applied)"
            print("after reload: 待规划 chip auto-active (default preset applied)")

            # 断言 2：列表仍为 2 行（种子任务），过滤确实生效
            rows2 = page.locator(".entity-item--rich").count()
            assert rows2 == 2, f"after reload backlog filter expected 2 rows, got {rows2}"
            print("after reload: list still 2 rows (filter applied)")

            # 断言 3：预设面板仍标记默认
            page.locator(".preset-wrap .dropdown").click()
            page.wait_for_selector(".preset-list .preset-item.is-default", timeout=5000)
            print("after reload: preset still marked default")

            browser.close()
    finally:
        for tid in created:
            api("DELETE", f"/api/tasks/{tid}", token=token)

    real_errors = [e for e in errors if "ERR_ABORTED" not in e and "ABORTED" not in e]
    if real_errors:
        print("ERRORS:", real_errors)
        sys.exit(1)
    print("E2E PASSED: 0 pageerror/console/.js+.css 404")


if __name__ == "__main__":
    main()
