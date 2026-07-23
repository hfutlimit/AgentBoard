"""
Epic 46 (v3.3) 任务列表排序维度增强 —— 端到端验证
- 登录 admin -> 进入 story 25（任务列表）
- 验证排序下拉含 7 个选项，含「截止日期」「指派人」
- 按截止日期排序：升序 dated 行单调不增(ISO 字典序) 且 无日期行全部置后；
  降序反转（无日期行全部置前、dated 行单调不降）
- 按指派人排序：升序 已指派行置前、未指派(?) 置后；降序反转
- 持久化：选「截止日期」后刷新，下拉仍显示「截止日期」且 localStorage.agentboard_sort_key=='due_date'
- 测试末清理自建任务，恢复默认排序（创建时间），不污染数据
- 断言：0 pageerror / console error / .js+.css 404
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:28080"
API = "http://127.0.0.1:18000"
STORY_ID = 25
PROJECT_ID = 3  # story 25 所属 epic 11 -> project 3
USER = "admin"
PASS = "admin123"

# 受控测试任务：覆盖 due_date / assignee 组合
SEED = [
    {"due_date": "2026-07-20", "assignee_id": 54},
    {"due_date": "2026-08-01", "assignee_id": 54},
    {"due_date": "2026-07-25", "assignee_id": 54},
    {"due_date": "2026-09-10", "assignee_id": 54},
    {"due_date": None, "assignee_id": 54},
    {"due_date": "2026-08-15", "assignee_id": None},
    {"due_date": None, "assignee_id": None},
]


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
        # 自建受控任务
        for s in SEED:
            body = {
                "project_id": PROJECT_ID,
                "story_id": STORY_ID,
                "title": "__E2E_SORT_DIM__",
                "type": "task",
                "priority": "low",
            }
            if s["due_date"]:
                body["due_date"] = s["due_date"]
            if s["assignee_id"] is not None:
                body["assignee_id"] = s["assignee_id"]
            st, t = api("POST", f"/api/stories/{STORY_ID}/tasks", token=token, body=body)
            assert st == 201, f"create task failed {st} {t}"
            created.append(t["id"])
        print("created test tasks:", created)

        # API 真值：story 25 全部任务 due_date 映射
        due_map = {}
        for off in (0, 200):
            st, arr = api("GET", f"/api/tasks?story_id={STORY_ID}&limit=200&offset={off}", token=token)
            arr = arr if isinstance(arr, list) else arr.get("tasks", arr.get("items", []))
            for t in arr:
                due_map[t["id"]] = t.get("due_date")
        print("api due_map size:", len(due_map))

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
            page.wait_for_selector(".task-sort-select", timeout=15000)
            page.wait_for_selector(".entity-item--rich", timeout=15000)

            opts = page.locator(".task-sort-select option")
            n = opts.count()
            print("sort options count:", n)
            assert n == 7, f"expected 7 sort options, got {n}"
            labels = [opts.nth(i).inner_text() for i in range(n)]
            print("sort labels:", labels)
            assert "截止日期" in labels, "missing 截止日期 option"
            assert "指派人" in labels, "missing 指派人 option"

            def row_ids_order():
                rows = page.locator(".entity-item--rich")
                out = []
                for i in range(rows.count()):
                    r = rows.nth(i)
                    href = r.locator("a.entity-item-link").first.get_attribute("href") or ""
                    tid = int(href.rstrip("/").split("/")[-1])
                    has_due = r.locator(".due-badge").count() > 0
                    unassigned = r.locator(".assignee-avatar-sm.unassigned").count() > 0
                    out.append((tid, has_due, unassigned))
                return out

            def set_sort(key):
                page.locator(".task-sort-select").select_option(value=key)
                page.wait_for_timeout(500)

            def dir_is_asc():
                return page.locator(".task-sort-dir-btn").inner_text().strip() == "↑"

            def set_dir(target_asc):
                if dir_is_asc() != target_asc:
                    page.locator(".task-sort-dir-btn").click()
                    page.wait_for_timeout(400)

            # 默认排序方向为 desc（localStorage 为空时回落 'desc'），先显式置 asc
            set_dir(True)

            # ---------- 按截止日期 升序（nulls 置后、dated 单调不增）----------
            set_sort("due_date")
            assert dir_is_asc(), "direction should be asc for due_date asc test"
            rows = row_ids_order()
            dated = [(i, tid, due_map.get(tid)) for i, (tid, hd, _ua) in enumerate(rows) if hd]
            undated = [(i, tid, _ua) for i, (tid, hd, _ua) in enumerate(rows) if not hd]
            print(f"due asc: dated={len(dated)} undated={len(undated)}")
            if dated and undated:
                last_dated = max(i for i, _t, _d in dated)
                first_undated = min(i for i, _t, _ua in undated)
                assert first_undated > last_dated, "null-due rows must appear AFTER dated rows (asc)"
            seq = [d for (_i, _tid, d) in dated if d]
            assert seq == sorted(seq), f"due dates not non-decreasing asc: {seq}"

            # ---------- 按截止日期 降序（nulls 置前、dated 单调不降）----------
            set_dir(False)
            rows = row_ids_order()
            dated = [(i, tid, due_map.get(tid)) for i, (tid, hd, _ua) in enumerate(rows) if hd]
            undated = [(i, tid, _ua) for i, (tid, hd, _ua) in enumerate(rows) if not hd]
            print(f"due desc: dated={len(dated)} undated={len(undated)}")
            if dated and undated:
                first_dated = min(i for i, _t, _d in dated)
                last_undated = max(i for i, _t, _ua in undated)
                assert last_undated < first_dated, "null-due rows must appear BEFORE dated rows (desc)"
            seq = [d for (_i, _tid, d) in dated if d]
            assert seq == sorted(seq, reverse=True), f"due dates not non-increasing desc: {seq}"

            # ---------- 按指派人 降序（nulls 置前）----------
            set_sort("assignee")
            rows = row_ids_order()
            assigned = [i for i, (_t, _d, ua) in enumerate(rows) if not ua]
            unassigned = [i for i, (_t, _d, ua) in enumerate(rows) if ua]
            print(f"assignee desc: assigned={len(assigned)} unassigned={len(unassigned)}")
            if assigned and unassigned:
                first_assigned = min(assigned)
                last_unassigned = max(unassigned)
                assert last_unassigned < first_assigned, "unassigned rows must appear BEFORE assigned rows (desc)"

            # ---------- 按指派人 升序（nulls 置后）----------
            set_dir(True)
            rows = row_ids_order()
            assigned = [i for i, (_t, _d, ua) in enumerate(rows) if not ua]
            unassigned = [i for i, (_t, _d, ua) in enumerate(rows) if ua]
            print(f"assignee asc: assigned={len(assigned)} unassigned={len(unassigned)}")
            if assigned and unassigned:
                last_assigned = max(assigned)
                first_unassigned = min(unassigned)
                assert first_unassigned > last_assigned, "unassigned rows must appear AFTER assigned rows (asc)"

            # ---------- 持久化 ----------
            set_sort("due_date")  # 当前方向 asc
            assert page.locator(".task-sort-select").input_value() == "due_date", "select should show due_date"
            assert dir_is_asc(), "direction should persist asc"
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".task-sort-select", timeout=15000)
            assert page.locator(".task-sort-select").input_value() == "due_date", "sort not persisted after reload"
            assert dir_is_asc(), "direction not persisted after reload"
            ls = page.evaluate("localStorage.getItem('agentboard_sort_key')")
            assert ls == "due_date", f"localStorage sort_key should be due_date, got {ls}"
            print("persistence OK")
            # 恢复默认排序（创建时间 + desc），避免污染人类用户默认偏好
            set_sort("created_at")
            set_dir(False)
            page.wait_for_timeout(200)

            browser.close()
    finally:
        # 清理自建任务
        for tid in created:
            api("DELETE", f"/api/tasks/{tid}", token=token)

    real_errors = [e for e in errors if "ERR_ABORTED" not in e and "ABORTED" not in e]
    if real_errors:
        print("ERRORS:", real_errors)
        sys.exit(1)
    print("E2E PASSED: 0 pageerror/console/.js+.css 404")


if __name__ == "__main__":
    main()
