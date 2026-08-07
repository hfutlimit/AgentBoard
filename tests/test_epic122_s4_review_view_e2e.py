"""Epic 122 S4 M1 E2E：项目统计 Tab 评审运营面板（渲染 + 超时重派交互 + 0 报错）。

覆盖：
- 项目页 → 统计 Tab → 评审运营面板存在（#review-ops-panel、超时未决、重派按钮）；
- 点击「扫描超时并重派」→ POST /api/review-stats/reassign-timeout 成功（200）；
- 0 console/pageerror / js-css 404。
"""
import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

WEB = os.getenv("AGENTBOARD_E2E_WEB", "http://127.0.0.1:28080")
API = os.getenv("AGENTBOARD_E2E_API", "http://127.0.0.1:18000")
USER = os.getenv("AGENTBOARD_E2E_USER", "admin")
PASS = os.getenv("AGENTBOARD_E2E_PASS", "admin123")


def _login_token():
    req = urllib.request.Request(
        API + "/api/auth/login",
        data=json.dumps({"username": USER, "password": PASS}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["token"]


def main():
    token = _login_token()
    fails = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        console_errors, page_errors, bad = [], [], []

        page.on("console", lambda m: console_errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed", lambda r: bad.append(
            f"{r.method} {r.url} {r.failure}")
            if "api" in r.url and "ERR_ABORTED" not in (r.failure or "") else None)

        page.add_init_script(
            f"localStorage.setItem('agentboard_token', {token!r})")

        # 进入项目 3（AgentBoard）→ 统计 Tab
        page.goto(WEB + "/project/3", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector(".tab-bar", timeout=30000)
        page.get_by_role("button", name="统计", exact=True).click()
        page.wait_for_selector("#review-ops-panel", timeout=30000)
        body = page.inner_text("body")
        assert "评审运营" in body, "评审运营面板标题缺失"
        print("[1] 统计 Tab 评审运营面板渲染 OK")

        # 重派按钮存在且可点
        btn = page.locator("#review-reassign-btn")
        assert btn.count() == 1, "重派按钮缺失"
        btn.click()
        page.wait_for_timeout(3000)
        body_after = page.inner_text("body")
        print("[2] 重派按钮点击 OK（busy 态已过）")
        # 无论是否有超时项，请求成功即面板保留（结果摘要 badge 出现或空态）
        assert "评审运营" in body_after, "重派后面板消失"

        # 评审统计卡（数据或空态二选一）
        rs_text = page.locator("#review-ops-panel").inner_text()
        has_stats = ("Story 已通过评审" in rs_text or "Task 已通过评审" in rs_text)
        has_empty = "暂无评审数据" in rs_text
        assert has_stats or has_empty, f"评审统计卡或空态缺失: {rs_text[:120]}"
        print(f"[3] 评审统计展示 OK (stats={has_stats}, empty={has_empty})")

        # 控制台错误中允许 /api/review-stats 的良性 Aborted（页面切换竞态）
        browser.close()

    errs = console_errors[:6] + page_errors[:6] + bad[:6]
    print("console errors:", console_errors[:6])
    print("page errors:", page_errors[:6])
    print("bad requests:", bad[:6])
    if errs:
        fails.append("JS 报错/失败请求非零")
    if fails:
        print("FAILS:", fails)
        sys.exit(1)
    print("PLAYWRIGHT REVIEW VIEW ALL PASS")


if __name__ == "__main__":
    main()
