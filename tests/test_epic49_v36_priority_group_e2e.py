"""
Epic 49 (v3.6) 任务列表分组新增「按优先级」维度 —— 端到端验证
- 登录 admin -> 进入 story 50（6 个任务，优先级 high/medium/low 各 2）
- 清除所有快速筛选 + 分组偏好（避免历史 localStorage 污染）
- 选择「按优先级」分组 -> 校验：
    * 渲染 3 个分组头（high / medium / low）
    * 分组顺序遵循优先级工作流（high -> medium -> low）
    * 每个分组头带 .badge.priority.priority--{x} 色徽章，文案匹配 priorityLabel
    * 各分组计数之和 == 任务总数
- 断言：0 pageerror / console error / .js+.css 404
- 测试末恢复分组为「不分组」（不影响人类默认偏好）
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:28080"
API = "http://127.0.0.1:18000"
STORY_ID = 50
USER = "admin"
PASS = "admin123"

PRIORITY_ORDER = ["highest", "high", "medium", "low", "lowest"]
PRIORITY_LABEL = {"highest": "最高", "high": "高", "medium": "中", "low": "低", "lowest": "最低"}


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
            # 清除所有筛选/分组/排序偏好，隔离历史 localStorage 污染
            page.add_init_script(
                "localStorage.removeItem('agentboard_quick_priority');"
                "localStorage.removeItem('agentboard_quick_status');"
                "localStorage.removeItem('agentboard_quick_type');"
                "localStorage.removeItem('agentboard_quick_assignee');"
                "localStorage.removeItem('agentboard_quick_due');"
                "localStorage.removeItem('agentboard_story_group');"
                "localStorage.removeItem('agentboard_collapsed_groups');"
                "localStorage.removeItem('agentboard_sort_key');"
                "localStorage.removeItem('agentboard_sort_order');"
                f"localStorage.setItem('agentboard_token','{token}');"
                f"localStorage.setItem('agentboard_user','{username}');"
            )
            page.goto(WEB + f"/story/{STORY_ID}", wait_until="networkidle")
            page.wait_for_selector(".entity-item--rich", timeout=15000)

            # 不分组时的任务总数（校验基准）
            total = page.locator(".entity-item--rich").count()
            print("total tasks (ungrouped):", total)
            assert total >= 4, f"story {STORY_ID} should have >=4 tasks, got {total}"

            # 选择「按优先级」分组
            page.select_option(".task-group-select", "priority")
            page.wait_for_selector(".task-group-header", timeout=8000)
            page.wait_for_timeout(300)

            headers = page.locator(".task-group-header")
            hcount = headers.count()
            print("group header count:", hcount)
            assert hcount == 3, f"expected 3 priority groups, got {hcount}"

            # 提取每个分组头的 key（优先级）与计数
            keys = []
            counts = []
            labels = []
            for i in range(hcount):
                h = headers.nth(i)
                badge = h.locator(".badge.priority")
                assert badge.count() == 1, f"group header #{i} must have exactly 1 priority badge"
                cls = badge.first.get_attribute("class")
                key = None
                for pk in PRIORITY_ORDER:
                    if f"priority--{pk}" in (cls or ""):
                        key = pk
                        break
                assert key is not None, f"cannot resolve priority key from class '{cls}'"
                keys.append(key)
                counts.append(int(h.locator(".task-group-count").inner_text().strip()))
                labels.append(badge.first.inner_text().strip())
            print("group keys (DOM order):", keys)
            print("group counts:", counts)
            print("group labels:", labels)

            # 顺序遵循优先级工作流（高 -> 低）
            expected_order = [k for k in PRIORITY_ORDER if k in set(keys)]
            assert keys == expected_order, f"priority group order must be {expected_order}, got {keys}"
            # 文案匹配 priorityLabel
            for k, lab in zip(keys, labels):
                assert lab == PRIORITY_LABEL[k], f"badge label for {k} should be {PRIORITY_LABEL[k]}, got {lab}"
            # 计数之和 == 任务总数
            assert sum(counts) == total, f"sum of group counts {sum(counts)} != total {total}"

            # 无分组时不可见的分组头在切换后应可见；截图留档
            page.screenshot(path=r"E:\Projects\WorkBuddy\AgentBoard\scripts\_ab_epic49.png")
            print("screenshot saved")

            # 恢复「不分组」
            page.select_option(".task-group-select", "none")
            page.wait_for_timeout(150)

            browser.close()
    finally:
        pass

    real_errors = [e for e in errors if "ERR_ABORTED" not in e and "ABORTED" not in e]
    if real_errors:
        print("ERRORS:", real_errors)
        sys.exit(1)
    print("E2E PASSED: 0 pageerror/console/.js+.css 404 — 按优先级分组验证通过")


if __name__ == "__main__":
    main()
