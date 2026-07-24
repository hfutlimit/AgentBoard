"""
Epic 50 (v3.7) 任务列表分组新增「按截止日期」维度 —— 端到端验证
- 登录 admin -> 临时创建 story + 跨桶任务（逾期/今天/本周/更晚/无截止日期）
- 清除所有快速筛选 + 分组偏好（隔离历史 localStorage 污染）
- 选择「按截止日期」分组 -> 校验：
    * 渲染 5 个分组头（逾期 / 今天到期 / 本周内 / 更晚 / 无截止日期）
    * 分组顺序固定 overdue -> today -> week -> later -> none
    * 每个分组头带 .badge.due.due--{bucket} 色徽章，文案匹配 dueBucketLabels
    * 各分组计数之和 == 任务总数
- 断言：0 pageerror / console error / .js+.css 404
- 测试末删除临时 story（级联清理任务）
"""
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:28080"
API = "http://127.0.0.1:18000"
USER = "admin"
PASS = "admin123"
PROJECT_ID = 113  # AUTODEV50
EPIC_ID = 121     # Epic 50 v3.7 (tracking) — temp story created under it

DUE_ORDER = ["overdue", "today", "week", "later", "none"]
DUE_LABEL = {"overdue": "逾期", "today": "今天到期", "week": "本周内", "later": "更晚", "none": "无截止日期"}


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


def iso(offset_days):
    # 后端仅接受 YYYY-MM-DD；本地时区 +8（UTC midnight -> 本地 +8h），
    # 用 date-only 即可稳定落入对应分桶（overdue<-1 / today==0 / 1..7 week / >7 later）
    d = datetime.now() + timedelta(days=offset_days)
    return d.strftime("%Y-%m-%d")


def main():
    token, username = login()
    errors = []
    story_id = None
    try:
        # 临时数据：5 桶各 1 个任务（today/week 用午间时间，避免 UTC 偏移误判）
        buckets = {
            "overdue": iso(-10),
            "today": iso(0),
            "week": iso(3),
            "later": iso(20),
            "none": None,
        }
        st, story = api("POST", f"/api/epics/{EPIC_ID}/stories", token,
                        {"title": "AB_v37_temp_group_due", "description": "temp"})
        assert st == 201, f"create story failed {st} {story}"
        story_id = story["id"]
        for b in DUE_ORDER:
            st, t = api("POST", f"/api/stories/{story_id}/tasks", token,
                        {"project_id": PROJECT_ID, "title": f"due-{b}", "type": "task",
                         "priority": "medium", "due_date": buckets[b]})
            assert st == 201, f"create task {b} failed {st} {t}"

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console:" + m.type + ":" + m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: (
                errors.append("404:" + r.url) if (r.url.endswith(".js") or r.url.endswith(".css")) else None
            ))
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
            page.goto(WEB + f"/story/{story_id}", wait_until="networkidle")
            page.wait_for_selector(".entity-item--rich", timeout=15000)

            total = page.locator(".entity-item--rich").count()
            print("total tasks (ungrouped):", total)
            assert total == 5, f"expected 5 temp tasks, got {total}"

            # 选择「按截止日期」分组
            page.select_option(".task-group-select", "due")
            page.wait_for_selector(".task-group-header", timeout=8000)
            page.wait_for_timeout(300)

            headers = page.locator(".task-group-header")
            hcount = headers.count()
            print("group header count:", hcount)
            assert hcount == 5, f"expected 5 due buckets, got {hcount}"

            keys, counts, labels = [], [], []
            for i in range(hcount):
                h = headers.nth(i)
                badge = h.locator(".badge.due")
                assert badge.count() == 1, f"group header #{i} must have exactly 1 due badge"
                cls = badge.first.get_attribute("class")
                key = None
                for dk in DUE_ORDER:
                    if f"due--{dk}" in (cls or ""):
                        key = dk
                        break
                assert key is not None, f"cannot resolve due key from class '{cls}'"
                keys.append(key)
                counts.append(int(h.locator(".task-group-count").inner_text().strip()))
                labels.append(badge.first.inner_text().strip())
            print("group keys (DOM order):", keys)
            print("group counts:", counts)
            print("group labels:", labels)

            # 顺序固定 overdue -> today -> week -> later -> none
            assert keys == DUE_ORDER, f"due group order must be {DUE_ORDER}, got {keys}"
            # 文案匹配 dueBucketLabels
            for k, lab in zip(keys, labels):
                assert lab == DUE_LABEL[k], f"badge label for {k} should be {DUE_LABEL[k]}, got {lab}"
            # 计数之和 == 任务总数
            assert sum(counts) == total, f"sum {sum(counts)} != total {total}"
            # 每个桶计数 == 1（本测试每个桶恰好 1 任务）
            assert all(c == 1 for c in counts), f"each bucket should have 1 task, got {counts}"

            page.screenshot(path=r"E:\Projects\WorkBuddy\AgentBoard\scripts\_ab_epic50_v37.png")
            print("screenshot saved")

            # 恢复「不分组」
            page.select_option(".task-group-select", "none")
            page.wait_for_timeout(150)
            browser.close()
    finally:
        if story_id:
            st, _ = api("DELETE", f"/api/stories/{story_id}", token)
            print("cleanup story", story_id, "->", st)

    real_errors = [e for e in errors if "ERR_ABORTED" not in e and "ABORTED" not in e]
    if real_errors:
        print("ERRORS:", real_errors)
        sys.exit(1)
    print("E2E PASSED: 0 pageerror/console/.js+.css 404 — 按截止日期分组验证通过")


if __name__ == "__main__":
    main()
