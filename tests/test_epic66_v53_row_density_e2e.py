"""
Epic 66 (v5.3) 任务列表行密度切换（紧凑/舒适）—— 端到端验证
- 登录 admin -> epic 56 (project 53) 下建种子 story + 3 种子任务
- 进入该 story 任务视图，默认舒适模式
- 断言：#densityToggle 按钮存在；舒适模式下行高 H_comfortable
- 点击 #densityToggle -> 切换紧凑：.entity-list 含 density-compact 类，行高 H_compact < H_comfortable
- 再点一次 -> 恢复舒适
- 切到紧凑后 reload -> 紧凑持久化（localStorage agentboard_list_density='compact'，按钮文案「紧凑」）
- 断言：0 pageerror / console error / .js+.css 404
- 测试末清理种子任务与种子 story，不污染追踪实体（task 1121 / story 105 / epic 56 保留）
"""
import json
import random
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
EPIC_ID = 56
PROJECT_ID = 53
USER = "admin"
PASS = "admin123"
SEED = "__E2E_ROW_DENSITY__" + str(random.randint(100000, 999999))


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
    token, _ = login()
    created = []
    errors = []
    try:
        # 种子 story + 任务（自清理）
        st, story = api("POST", f"/api/epics/{EPIC_ID}/stories", token=token,
                        body={"title": SEED + "-story", "description": "E2E 种子 story"})
        assert st == 201, f"create story {st} {story}"
        sid = story["id"]
        created.append(("story", sid))
        for i in range(3):
            st, task = api("POST", f"/api/stories/{sid}/tasks", token=token,
                           body={"project_id": PROJECT_ID, "story_id": sid,
                                 "title": SEED + f"-task-{i}", "type": "task", "priority": "medium"})
            assert st == 201, f"create task {st} {task}"
            created.append(("task", task["id"]))

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console: " + m.type + ": " + m.text)
                    if m.type in ("error",) else None)
            page.on("response", lambda r: errors.append("http4xx: " + str(r.status) + " " + r.url)
                    if r.status >= 400 and (r.url.endswith(".js") or r.url.endswith(".css")) else None)

            page.add_init_script("localStorage.setItem('agentboard_token', '" + token + "');")
            page.goto(WEB + "/story/" + str(sid), wait_until="networkidle")
            page.wait_for_selector(".entity-item--rich", timeout=15000)

            # 确保从舒适模式开始（清掉可能残留的偏好）
            page.evaluate("localStorage.removeItem('agentboard_list_density');")
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".entity-item--rich", timeout=15000)

            def pad_top(loc):
                return float(loc.evaluate("(el) => getComputedStyle(el).paddingTop").replace("px", ""))

            # 1) 默认舒适模式：按钮存在 + 文案「舒适」
            toggle = page.locator("#densityToggle")
            assert toggle.count() == 1, "density toggle button missing"
            assert "舒适" in (toggle.inner_text() or ""), "default label should be 舒适"
            assert "density-compact" not in (page.locator(".entity-list").get_attribute("class") or ""), \
                "should NOT have density-compact by default"

            # 2) 舒适模式行内边距（默认 14px）
            row = page.locator(".entity-item--rich").first
            p_comfortable = pad_top(row)
            assert p_comfortable > 8, f"comfortable padding-top too small: {p_comfortable}"

            # 3) 点击切换为紧凑（padding 应降到 6px）
            toggle.click()
            page.wait_for_selector(".entity-list.density-compact", timeout=5000)
            page.wait_for_timeout(350)  # 等待 .entity-item 的 transition: all .2s 完成
            assert "紧凑" in (toggle.inner_text() or ""), "label should become 紧凑 after toggle"
            p_compact = pad_top(row)
            assert p_compact <= 8, f"compact padding-top not reduced: {p_compact}"
            assert p_compact < p_comfortable - 2, f"compact ({p_compact}) not smaller than comfortable ({p_comfortable})"

            # 4) 再点一次恢复舒适（padding 回到 ~14px）
            toggle.click()
            page.wait_for_selector(".entity-list:not(.density-compact)", timeout=5000)
            page.wait_for_timeout(350)  # 等待过渡完成
            assert "舒适" in (toggle.inner_text() or ""), "label should revert to 舒适"
            p_back = pad_top(row)
            assert abs(p_back - p_comfortable) < 1.0, f"padding should restore to comfortable ({p_comfortable} vs {p_back})"

            # 5) 持久化：切到紧凑后 reload 仍紧凑
            toggle.click()
            page.wait_for_selector(".entity-list.density-compact", timeout=5000)
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".entity-item--rich", timeout=15000)
            assert "density-compact" in (page.locator(".entity-list").get_attribute("class") or ""), \
                "compact mode should persist after reload"
            assert (page.evaluate("localStorage.getItem('agentboard_list_density')") or "") == "compact", \
                "localStorage should persist 'compact'"
            assert pad_top(page.locator(".entity-item--rich").first) <= 8, "compact padding should persist after reload"

            # 还原为舒适，避免污染人类用户默认偏好
            page.locator("#densityToggle").click()
            page.wait_for_selector(".entity-list:not(.density-compact)", timeout=5000)

            page.close()
            browser.close()
    finally:
        # 清理种子
        for kind, cid in reversed(created):
            if kind == "task":
                api("DELETE", f"/api/tasks/{cid}", token=token)
            elif kind == "story":
                api("DELETE", f"/api/stories/{cid}", token=token)

    # 错误断言
    real_errors = [e for e in errors if "net::ERR_ABORTED" not in e]
    assert not real_errors, "UI errors detected:\n" + "\n".join(real_errors)
    print("PASS: Epic 66 v5.3 行密度切换 E2E 全部断言通过（0 错误）")


if __name__ == "__main__":
    main()
