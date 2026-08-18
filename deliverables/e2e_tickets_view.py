"""工单（Epic/Story/Task 统一视图）E2E 验证。

本地启动后运行：
    AGENTBOARD_API=http://127.0.0.1:18000 AGENTBOARD_WEB=http://localhost:28080 \
        python deliverables/e2e_tickets_view.py

验证点：
1) 项目 tab 栏出现「工单」。
2) 进入工单 tab，默认状态过滤为「未完成」，且列表不含 done 项、含未完项。
3) 切换「已完成」只显示 done 项；切换「全部」显示全部。
4) 切换排序按钮不报错，列表倒序（创建时间）有效。
5) 0 个 JS error / console error，0 个 js/css 资源加载失败。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

API = os.getenv("AGENTBOARD_API", "http://127.0.0.1:18000")
WEB = os.getenv("AGENTBOARD_WEB", "http://localhost:28080")
USER = "admin"
PASS = "admin123"


def api(method, path, token=None, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode() or "{}"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw}


def ensure_token():
    st, _ = api("POST", "/api/auth/register", body={"username": USER, "password": PASS})
    print(f"[auth] register -> {st}")
    st, data = api("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    assert st == 200, f"login failed {st}: {data}"
    token = data.get("token") or (data.get("data") or {}).get("token")
    assert token, f"no token: {data}"
    print("[OK] login")
    return token


def seed(token):
    import random
    key = "TKT%d" % int(time.time()) + str(random.randint(100, 999))
    st, p = api("POST", "/api/projects", token=token,
                body={"name": "工单视图验证", "key": key, "description": "Playwright 验证工单视图"})
    assert st in (200, 201), f"create project failed {st}: {p}"
    pid = p["id"]
    st, ep = api("POST", f"/api/projects/{pid}/epics", token=token,
                 body={"title": "E2E Epic", "description": "epic"})
    assert st in (200, 201), f"create epic {st}: {ep}"
    eid = ep["id"]
    st, stt = api("POST", f"/api/epics/{eid}/stories", token=token,
                  body={"title": "E2E Story", "needs_design": False})
    assert st in (200, 201), f"create story {st}: {stt}"
    sid = stt["id"]
    st, t1 = api("POST", f"/api/stories/{sid}/tasks", token=token,
                 body={"project_id": pid, "title": "未完成 Task", "type": "dev"})
    assert st in (200, 201), f"create task1 {st}: {t1}"
    t1id = t1["id"]
    st, t2 = api("POST", f"/api/stories/{sid}/tasks", token=token,
                 body={"project_id": pid, "title": "已完成 Task", "type": "dev"})
    assert st in (200, 201), f"create task2 {st}: {t2}"
    t2id = t2["id"]
    # 将 t2 置为 done（带必须的 status_reason）
    st, _ = api("PUT", f"/api/tasks/{t2id}/status", token=token,
                body={"status": "done", "status_reason": "completed", "reason": "e2e"})
    assert st == 200, f"set done failed {st}"
    # 将 story 置为 done 也可，但 story 有状态机约束；此处仅用 task 验证 done 分支
    print(f"[seed] project={pid} epic={eid} story={sid} t1={t1id} t2={t2id}(done)")
    return pid, eid, sid, t1id, t2id


def get_tickets(token, pid, status_filter="incomplete", sort="created_at", order="desc"):
    st, data = api("GET", f"/api/projects/{pid}/tickets?status_filter={status_filter}"
                   f"&sort={sort}&order={order}", token=token)
    assert st == 200, f"tickets {st}: {data}"
    return data


def main():
    token = ensure_token()
    pid, eid, sid, t1id, t2id = seed(token)
    inc = get_tickets(token, pid, "incomplete")["items"]
    done = get_tickets(token, pid, "complete")["items"]
    allt = get_tickets(token, pid, "all")["items"]
    print(f"[api] incomplete={len(inc)} complete={len(done)} all={len(allt)}")
    # 基于已建数据标题做存在性校验，避免历史数据干扰精确计数
    inc_t = [i["title"] for i in inc]
    done_t = [i["title"] for i in done]
    all_t = [i["title"] for i in allt]
    assert any("未完成 Task" in t for t in inc_t), "incomplete 应含「未完成 Task」"
    assert all("已完成 Task" not in t for t in inc_t), "incomplete 不应含「已完成 Task」"
    assert any("已完成 Task" in t for t in done_t), "complete 应含「已完成 Task」"
    assert all("未完成 Task" not in t for t in done_t), "complete 不应含「未完成 Task」"
    assert any("未完成 Task" in t for t in all_t) and any("已完成 Task" in t for t in all_t), "all 应同时含两项"

    errors = []
    js_css_fail = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
        page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
        page.on("requestfailed", lambda r: (
            js_css_fail.append(r.url)
            if (r.url.endswith(".js") or r.url.endswith(".css")) else None))

        page.goto(WEB + "/login", wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        page.evaluate(
            f"() => {{ localStorage.setItem('agentboard_token', '{token}');"
            f"           localStorage.setItem('agentboard_user', '{USER}'); }}")
        page.goto(f"{WEB}/project/{pid}", wait_until="domcontentloaded")
        page.wait_for_timeout(6000)

        labels = page.eval_on_selector_all(
            ".tab-bar .tab-btn .tab-label", "els => els.map(e => e.textContent.trim())")
        print("[tabs]", labels)
        assert "工单" in labels, f"缺少「工单」tab: {labels}"

        # 进入工单 tab
        page.locator(".tab-bar .tab-btn").filter(has_text="工单").click()
        page.wait_for_selector(".ticket-list, .empty-state", timeout=8000)
        page.wait_for_timeout(600)

        # 默认状态过滤 = 未完成
        active_filter = page.eval_on_selector_all(
            ".seg-btn", "els => els.filter(e => e.classList.contains('active')).map(e => e.textContent.trim())")
        print("[filter] active =", active_filter)
        assert "未完成" in active_filter, f"默认应为未完成, 实际 {active_filter}"

        # 默认 incomplete：含「未完成 Task」，不含「已完成 Task」
        default_titles = page.eval_on_selector_all(".ticket-title", "els => els.map(e => e.textContent.trim())")
        print("[default titles]", default_titles)
        assert any("未完成 Task" in t for t in default_titles), "默认列表应含「未完成 Task」"
        assert all("已完成 Task" not in t for t in default_titles), "默认不应含「已完成 Task」"

        badge = page.eval_on_selector(".tab-btn .tab-count", "e => e.textContent.trim()") if page.locator(".tab-btn .tab-count").count() else "0"
        print("[badge]", badge)

        page.screenshot(path="deliverables/e2e_tickets_incomplete.png")

        # 切换「已完成」
        page.locator(".seg-btn").filter(has_text="已完成").click()
        page.wait_for_timeout(700)
        done_titles = page.eval_on_selector_all(".ticket-title", "els => els.map(e => e.textContent.trim())")
        print("[done titles]", done_titles)
        assert any("已完成 Task" in t for t in done_titles), "已完成应含「已完成 Task」"
        assert all("未完成 Task" not in t for t in done_titles), "已完成不应含「未完成 Task」"
        page.screenshot(path="deliverables/e2e_tickets_done.png")

        # 切换「全部」
        page.locator(".seg-btn").filter(has_text="全部").click()
        page.wait_for_timeout(700)
        all_titles = page.eval_on_selector_all(".ticket-title", "els => els.map(e => e.textContent.trim())")
        print("[all titles]", all_titles)
        assert any("未完成 Task" in t for t in all_titles) and any("已完成 Task" in t for t in all_titles), \
            f"全部应同时含两项: {all_titles}"

        # 切换排序方向（倒序/正序）不报错，且确实翻转顺序
        order_btn = page.locator(".ticket-sort button.ghost-sm")
        before = page.eval_on_selector_all(".ticket-row .ticket-date", "els => els.map(e => e.textContent.trim())")
        order_btn.click()
        page.wait_for_timeout(700)
        after = page.eval_on_selector_all(".ticket-row .ticket-date", "els => els.map(e => e.textContent.trim())")
        after_titles = page.eval_on_selector_all(".ticket-title", "els => els.map(e => e.textContent.trim())")
        print("[order] before=", before, "after=", after)
        # 倒序→正序：首个日期应由「最新」变为「最早」
        assert before and after and before[0] != after[0], f"排序方向未生效: {before} vs {after}"
        assert any("未完成 Task" in t for t in after_titles) and any("已完成 Task" in t for t in after_titles), \
            f"排序后仍应含两项: {after_titles}"

        page.screenshot(path="deliverables/e2e_tickets_all.png")
        browser.close()

    print(f"[E2E] pageerror/console errors: {len(errors)}")
    print(f"[E2E] js/css 失败: {len(js_css_fail)}")
    for e in errors[:8]:
        print("  ERR:", e)
    for f in js_css_fail[:8]:
        print("  FAIL:", f)
    assert not errors, f"存在 JS 错误: {errors[:3]}"
    assert not js_css_fail, f"存在 js/css 加载失败: {js_css_fail[:3]}"
    print("[PASS] 工单视图 E2E 通过")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("[FAIL] " + str(e))
        sys.exit(1)
