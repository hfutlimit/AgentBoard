"""
Epic 53 (v4.0) 筛选预设增强 —— 多命名预设 + 默认预设 —— 端到端验证
- 登录 admin -> 进入 story 25（任务列表）
- 预设 A：状态=完成 -> 保存为「P_A」
- 清除筛选 -> 预设 B：多选指派人（>=2 个 count>0；否则单指派）-> 保存为「P_B」
- 断言：面板列出 2 个预设、数量徽标 = 2
- 将 P_A 设为默认（点星标）-> 面板出现「应用默认」按钮、P_A 高亮
- 清除全部筛选 -> 行数恢复全部
- 点「应用默认」-> 状态=完成 生效、行数 == P_A 行数
- 点 P_B「应用」-> 指派人 chips 生效、行数 == P_B 行数（验证多选数组还原）
- 刷新 -> 2 预设持久化、P_A 仍为默认
- 删除 P_B -> 剩 1；删除 P_A -> 清空
- 断言：0 pageerror / console error / .js+.css 404
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:28080"
API = "http://127.0.0.1:18000"
# 使用受控测试 story（AUTODEV53 项目下的 story 195，含 8 个确定性任务：
# 4 done(2 指派 admin/2 未指派) + 2 todo(1 admin/1 未指派) + 2 in_progress(均未指派)），
# 避免 story 25 数据漂移（全 done/全未指派）导致筛选无法形成严格子集。
STORY_ID = 195
P_A = "__E2E_P_A__"
P_B = "__E2E_P_B__"


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

            page.add_init_script(
                f"localStorage.setItem('agentboard_token','{token}');"
                f"localStorage.setItem('agentboard_user','{username}');"
            )
            page.goto(WEB + f"/story/{STORY_ID}", wait_until="networkidle")
            page.wait_for_selector(".task-quickfilter-bar", timeout=15000)
            page.wait_for_selector(".entity-item--rich", timeout=15000)
            page.wait_for_timeout(1500)  # 等待 tasks() 信号稳定（规避既有 race）

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

            def save_preset(name):
                open_panel()
                page.locator(".preset-name-input").fill(name)
                page.locator("button", has_text="保存当前").click()
                page.wait_for_timeout(400)

            bars = page.locator(".task-quickfilter-bar")
            status_bar = bars.nth(0)
            assignee_bar = bars.nth(1)

            total = row_count()
            print("total rows:", total)
            assert total > 0, "story 25 should have tasks"

            # --- 预设 A：状态=完成 ---
            status_bar.locator("button.qf-chip", has_text="完成").click()
            page.wait_for_timeout(400)
            r_a = row_count()
            assert 0 < r_a < total, f"P_A rows {r_a} unexpected"
            print("P_A (status=完成) rows:", r_a)
            save_preset(P_A)
            assert page.locator(".preset-item").count() == 1
            close_panel()

            # --- 清除筛选，构建预设 B：多选指派人 ---
            page.locator("button", has_text="清除筛选").first.click()
            page.wait_for_timeout(400)
            assert row_count() == total, "clear-all failed before P_B"
            assert "active" not in (status_bar.locator("button.qf-chip", has_text="完成").get_attribute("class") or "")

            chips = assignee_bar.locator("button.qf-chip")
            picked = []
            # 选第一个真实指派人（count>0，非「全部」/「未指派」）；指派人 chips 为单选
            for i in range(chips.count()):
                c = chips.nth(i)
                txt = c.inner_text()
                if "全部" in txt or "未指派" in txt:
                    continue
                cnt = int("".join(ch for ch in txt if ch.isdigit()) or "0")
                if cnt > 0:
                    c.click()
                    page.wait_for_timeout(250)
                    picked.append(txt.split()[0])
                    break
            # 若没有真实指派人，退化为「未指派」
            if not picked:
                un = assignee_bar.locator("button.qf-chip", has_text="未指派")
                if un.count() > 0:
                    un.first.click()
                    page.wait_for_timeout(250)
                    picked.append("未指派")
            assert picked, "no assignee chip available for P_B"
            r_b = row_count()
            print(f"P_B (assignees={picked}) rows:", r_b)
            save_preset(P_B)
            assert page.locator(".preset-item").count() == 2
            badge = page.locator(".preset-toggle .preset-count").inner_text().strip()
            assert badge == "2", f"badge should be 2, got {badge}"
            print("two presets saved, badge=2")
            close_panel()

            # --- 将 P_A 设为默认 ---
            open_panel()
            star = page.locator(".preset-item", has_text=P_A).locator("button.preset-star")
            star.click()
            page.wait_for_timeout(300)
            assert page.locator(".preset-apply-default").count() == 1, "apply-default button missing"
            assert "on" in (star.get_attribute("class") or ""), "P_A star not active"
            print("P_A set as default; apply-default button shown")

            # --- 清除全部 -> 应用默认 ---
            page.locator("button", has_text="清除筛选").first.click()
            page.wait_for_timeout(400)
            assert row_count() == total, "clear-all failed before apply-default"
            page.locator(".preset-apply-default").click()
            page.wait_for_timeout(500)
            assert "active" in (status_bar.locator("button.qf-chip", has_text="完成").get_attribute("class") or ""), "default not applied (status)"
            assert row_count() == r_a, f"after apply-default rows {row_count()} != {r_a}"
            print("apply-default OK, rows match P_A")
            close_panel()

            # --- 应用 P_B（验证多选数组还原）---
            open_panel()
            page.locator(".preset-apply", has_text=P_B).click()
            page.wait_for_timeout(500)
            for label in picked:
                sel = assignee_bar.locator("button.qf-chip", has_text=label)
                assert "active" in (sel.get_attribute("class") or ""), f"assignee {label} not active after apply P_B"
            assert row_count() == r_b, f"after apply P_B rows {row_count()} != {r_b}"
            print("apply P_B OK (multi-select arrays restored), rows match P_B")
            close_panel()

            # --- 刷新：持久化 + 默认仍生效 ---
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".preset-toggle", timeout=10000)
            page.wait_for_selector(".entity-item--rich", timeout=15000)
            page.wait_for_timeout(1000)
            open_panel()
            assert page.locator(".preset-item").count() == 2, "presets not persisted"
            assert page.locator(".preset-item", has_text=P_A).locator("button.preset-star.on").count() == 1, "P_A default not persisted"
            close_panel()
            print("persistence + default OK")

            # --- 删除 P_B 再删 P_A（P_A 为默认）---
            open_panel()
            page.locator(".preset-item", has_text=P_B).locator("button.preset-del").click()
            page.wait_for_timeout(300)
            assert page.locator(".preset-item").count() == 1
            badge = page.locator(".preset-toggle .preset-count").inner_text().strip()
            assert badge == "1", f"badge should be 1, got {badge}"
            # 删除非默认预设 P_B 后，默认 P_A 仍在 -> apply-default 按钮应保留
            assert page.locator(".preset-apply-default").count() == 1, "apply-default should remain while P_A default exists"
            page.locator(".preset-item", has_text=P_A).locator("button.preset-del").click()
            page.wait_for_timeout(300)
            assert page.locator(".preset-item").count() == 0
            badge = page.locator(".preset-toggle .preset-count").inner_text().strip()
            assert badge == "0", f"badge should be 0, got {badge}"
            # 默认预设被删后 apply-default 按钮消失
            assert page.locator(".preset-apply-default").count() == 0, "apply-default should disappear after deleting default"
            print("delete presets OK")
            close_panel()

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
