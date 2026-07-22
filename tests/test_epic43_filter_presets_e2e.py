"""
Epic 43 (v3.1) 筛选预设 —— 端到端验证
- 登录 admin -> 进入 story 25（任务列表）
- 组合一个筛选（状态=完成 + 指派人=未指派），保存为预设「P1」
- 验证：面板出现「P1」、数量徽标 +1
- 清除全部筛选 -> 行数恢复全部
- 应用「P1」-> 两个 chip 同时高亮、行数 == 交集行数
- 刷新 -> 预设持久化（localStorage），筛选仍生效
- 删除「P1」-> 面板清空
- 断言：0 pageerror / console error / .js+.css 404
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:28080"
API = "http://127.0.0.1:58125"
STORY_ID = 25
PRESET = "__E2E_PRESET_P1__"


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

            # 全新浏览器上下文 => localStorage 本就为空；仅注入登录态（不清除预设，否则刷新时会再次清空破坏持久化验证）
            page.add_init_script(
                f"localStorage.setItem('agentboard_token','{token}');"
                f"localStorage.setItem('agentboard_user','{username}');"
            )
            page.goto(WEB + "/story/25", wait_until="networkidle")
            page.wait_for_selector(".task-quickfilter-bar", timeout=15000)

            def row_count():
                return page.locator(".entity-item--rich").count()

            def open_panel():
                if page.locator(".preset-panel").count() == 0:
                    page.locator("button.preset-toggle").click()
                page.wait_for_selector(".preset-panel", timeout=5000)

            def close_panel():
                if page.locator(".preset-panel").count() > 0:
                    page.locator("button.preset-toggle").click()
                    page.wait_for_selector(".preset-panel", state="detached", timeout=5000)

            bars = page.locator(".task-quickfilter-bar")
            status_bar = bars.nth(0)
            assignee_bar = bars.nth(1)

            total = row_count()
            print("total rows:", total)
            assert total > 0, "story 25 should have tasks"

            # --- 组合筛选：状态=完成 + 指派人=未指派(count>0) ---
            status_bar.locator("button.qf-chip", has_text="完成").click()
            page.wait_for_timeout(400)
            r_done = row_count()
            assert r_done > 0 and r_done < total, f"done filter rows {r_done} unexpected"
            print("after status=完成 rows:", r_done)

            assignee_chips = assignee_bar.locator("button.qf-chip")
            assignee_label = None
            for i in range(assignee_chips.count()):
                c = assignee_chips.nth(i)
                txt = c.inner_text()
                if "全部" in txt:
                    continue
                cnt = int("".join(ch for ch in txt if ch.isdigit()) or "0")
                if cnt > 0:
                    assignee_label = c.get_attribute("title") or txt.split()[0]
                    c.click()
                    break
            assert assignee_label, "no assignee chip with count>0 found"
            page.wait_for_timeout(400)
            r_inter = row_count()
            assert r_inter > 0 and r_inter <= r_done, f"intersection rows {r_inter} unexpected"
            print(f"after +assignee={assignee_label} rows:", r_inter)

            # --- 打开预设面板，保存「P1」 ---
            open_panel()
            page.locator(".preset-name-input").fill(PRESET)
            page.locator("button", has_text="保存当前").click()
            page.wait_for_timeout(400)
            assert page.locator(".preset-item").count() == 1, "preset not listed"
            badge = page.locator(".preset-toggle .preset-count").inner_text()
            assert badge.strip() == "1", f"badge should be 1, got {badge}"
            print("save preset OK, badge=1")
            close_panel()

            # --- 清除全部筛选 ---
            page.locator("button", has_text="清除筛选").first.click()
            page.wait_for_timeout(400)
            assert row_count() == total, f"after clear-all expected {total}, got {row_count()}"
            assert "active" not in (status_bar.locator("button.qf-chip", has_text="完成").get_attribute("class") or ""), "status chip still active"
            print("clear-all OK, rows back to total")

            # --- 应用预设 P1 ---
            open_panel()
            page.locator(".preset-apply", has_text=PRESET).click()
            page.wait_for_timeout(500)
            assert "active" in (status_bar.locator("button.qf-chip", has_text="完成").get_attribute("class") or ""), "status chip not active after apply"
            assert "active" in (assignee_bar.locator("button.qf-chip", has_text=assignee_label).get_attribute("class") or ""), "assignee chip not active after apply"
            assert row_count() == r_inter, f"after apply rows {row_count()} != {r_inter}"
            print("apply preset OK, rows match intersection")

            # --- 刷新：持久化 + 筛选仍生效 ---
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".preset-toggle", timeout=10000)
            open_panel()
            assert page.locator(".preset-item").count() == 1, "preset not persisted after reload"
            close_panel()
            assert row_count() == r_inter, f"after reload rows {row_count()} != {r_inter}"
            print("persistence OK")

            # --- 删除预设 ---
            open_panel()
            page.locator(".preset-del").first.click()
            page.wait_for_timeout(300)
            assert page.locator(".preset-item").count() == 0, "preset not deleted"
            badge = page.locator(".preset-toggle .preset-count").inner_text()
            assert badge.strip() == "0", f"badge should be 0 after delete, got {badge}"
            print("delete preset OK")

            browser.close()
    finally:
        pass

    real_errors = [e for e in errors if "net::ERR_ABORTED" not in e and "ABORTED" not in e]
    if real_errors:
        print("ERRORS:", real_errors)
        sys.exit(1)
    print("E2E PASSED: 0 pageerror/console/.js+.css 404")


if __name__ == "__main__":
    main()
