"""
Epic 40 (v2.8) 截止日期快速筛选 chips —— 端到端验证
- 登录 admin -> 进入 story 25（任务列表为项目级全量，共 T 个任务）
- 临时注入 4 个带 due_date 的任务（逾期todo / 逾期done / 今天 / 本周），验证后删除
- 自洽验证：每个分桶 chip 计数 == 过滤后可见行数；分区不变量；逾期桶排除已完成
- 断言：刷新后偏好持久化；0 pageerror / console error / .js+.css 404
"""
import datetime
import json
import sys
import time
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8080"
API = "http://127.0.0.1:58125"
STORY_ID = 25
PROJECT_ID = 3
NONCE = str(int(time.time()))
TEMP_TITLES = [
    f"__E2E_DUE_{NONCE}_OVERDUE__",
    f"__E2E_DUE_{NONCE}_DONE_OVERDUE__",
    f"__E2E_DUE_{NONCE}_TODAY__",
    f"__E2E_DUE_{NONCE}_WEEK__",
]


def api(method, path, token=None, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def login():
    st, u = api("POST", "/api/auth/login", body={"username": "admin", "password": "admin123"})
    assert st == 200, f"login failed {st}"
    return u["token"], u["username"]


def due_str(delta_days):
    return (datetime.date.today() + datetime.timedelta(days=delta_days)).isoformat()


def set_status_chain(token, tid, target):
    for s in {"todo": ["todo"], "done": ["todo", "in_progress", "done"]}[target]:
        st, _ = api("PUT", f"/api/tasks/{tid}/status", token, {"status": s})
        assert st == 200, f"set_status {s} failed for {tid}: {st}"


def create_temp_tasks(token):
    specs = [
        (TEMP_TITLES[0], due_str(-2), "todo"),
        (TEMP_TITLES[1], due_str(-5), "done"),
        (TEMP_TITLES[2], due_str(0), "todo"),
        (TEMP_TITLES[3], due_str(3), "todo"),
    ]
    ids = []
    for title, dd, status in specs:
        st, t = api("POST", f"/api/stories/{STORY_ID}/tasks", token, {
            "project_id": PROJECT_ID, "title": title, "type": "task",
            "priority": "medium", "due_date": dd,
        })
        assert st == 201, f"create task failed {title}: {st} {t}"
        ids.append(t["id"])
    set_status_chain(token, ids[0], "todo")
    set_status_chain(token, ids[1], "done")  # 逾期但已完成 -> 不应计入「逾期」chip
    set_status_chain(token, ids[2], "todo")
    set_status_chain(token, ids[3], "todo")
    return ids


def delete_temp_tasks(token, ids):
    # 主清理：按 id 删除（忽略连接重置等良性错误）
    for tid in ids:
        try:
            api("DELETE", f"/api/tasks/{tid}", token)
        except Exception:
            pass
    # 兜底清扫：删除任何残留的同 nonce 临时任务（按标题搜索）
    try:
        _, rows = api("GET", f"/api/tasks/search?q=__E2E_DUE_{NONCE}&limit=200", token)
        for t in rows or []:
            try:
                api("DELETE", f"/api/tasks/{t['id']}", token)
            except Exception:
                pass
    except Exception:
        pass


def main():
    token, username = login()
    temp_ids = create_temp_tasks(token)
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

            page.add_init_script(f"localStorage.setItem('agentboard_token','{token}');localStorage.setItem('agentboard_user','{username}');")
            page.goto(WEB + "/story/25", wait_until="networkidle")
            page.wait_for_selector(".task-quickfilter-bar", timeout=15000)

            due_bar = page.locator(".task-quickfilter-bar").filter(has_text="无截止")
            due_bar.wait_for(timeout=10000)
            chips = due_bar.locator("button.qf-chip")
            assert chips.count() == 5, f"due bar should have 5 chips, got {chips.count()}"

            def chip_count(label):
                return int(due_bar.locator("button.qf-chip", has_text=label).locator(".qf-count").inner_text())

            def row_count():
                return page.locator(".entity-item--rich").count()

            def click_chip(label):
                due_bar.locator("button.qf-chip", has_text=label).click()
                page.wait_for_timeout(400)

            total = chip_count("全部")  # tasks().length
            print("total tasks (全部):", total)

            # 各分桶：chip 计数 == 过滤后可见行数，且 chip 高亮
            for label in ["逾期", "今天", "本周", "无截止"]:
                c = chip_count(label)
                click_chip(label)
                assert "active" in (due_bar.locator("button.qf-chip", has_text=label).get_attribute("class") or ""), f"{label} chip not active"
                rc = row_count()
                assert rc == c, f"{label}: chip count {c} != visible rows {rc}"
                print(f"{label}: count={c} rows={rc} OK")

            # 分区不变量：dated-done 任务不计入任何分桶 -> 四桶之和 <= 总数
            s = chip_count("逾期") + chip_count("今天") + chip_count("本周") + chip_count("无截止")
            assert s <= total, f"partition sum {s} > total {total}"
            print(f"partition invariant OK (sum={s} <= total={total})")

            # 逾期排除已完成：临时「逾期done」任务在逾期过滤下不可见，但在「全部」可见
            click_chip("逾期")
            assert page.locator(".entity-item--rich", has_text=TEMP_TITLES[1]).count() == 0, "done-overdue must be hidden under 逾期"
            due_bar.locator("button.qf-chip", has_text="全部").click()
            page.wait_for_timeout(400)
            assert page.locator(".entity-item--rich", has_text=TEMP_TITLES[1]).count() == 1, "done-overdue must show under 全部"
            print("overdue-excludes-done OK")

            # 持久化：选「本周」后刷新仍生效
            click_chip("本周")
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".task-quickfilter-bar", timeout=10000)
            due_bar2 = page.locator(".task-quickfilter-bar").filter(has_text="无截止")
            due_bar2.wait_for(timeout=10000)
            assert "active" in (due_bar2.locator("button.qf-chip", has_text="本周").get_attribute("class") or ""), "week chip not persisted"
            assert row_count() == chip_count("本周"), "week rows mismatch after reload"
            print("persistence OK")

            # 清除全部筛选 -> 恢复全部 T 行
            page.locator("button").filter(has_text="清除").first.click()
            page.wait_for_timeout(400)
            assert row_count() == total, f"after clear-all expected {total} rows, got {row_count()}"
            print("clear-all OK")

            browser.close()
    finally:
        delete_temp_tasks(token, temp_ids)

    real_errors = [e for e in errors if "net::ERR_ABORTED" not in e and "ABORTED" not in e]
    if real_errors:
        print("ERRORS:", real_errors)
        sys.exit(1)
    print("E2E PASSED: 0 pageerror/console/.js+.css 404")


if __name__ == "__main__":
    main()
