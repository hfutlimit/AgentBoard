"""Epic 122 S3 M2 E2E：Playwright 页面冒烟（登录/首页/项目页 0 错误）。"""
import os
import sys

from playwright.sync_api import sync_playwright

WEB = os.getenv("AGENTBOARD_E2E_WEB", "http://127.0.0.1:28080")

fails = []


def main():
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

        # 登录：注入 token（E2E 备用登录 admin/admin123）
        import urllib.request
        import json
        req = urllib.request.Request(
            WEB.replace("28080", "18000") + "/api/auth/login",
            data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            token = json.loads(r.read())["token"]
        page.add_init_script(
            f"localStorage.setItem('agentboard_token', {token!r})")
        page.goto(WEB + "/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        print("[1] 首页 loaded, title:", page.title())

        # 进入项目 3（AgentBoard）
        page.goto(WEB + "/project/3", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        body = page.inner_text("body")
        assert "AgentBoard" in body or "Epic" in body or "Dashboard" in body, \
            f"项目页内容异常: {body[:120]}"
        print("[2] 项目页 OK（含 Epic/看板内容）")

        # Story 页回归（S3 评审闭环展示）—— 用本机真实 story（本机 DB ≠ 远程生产）
        import urllib.request
        import json as _json
        req2 = urllib.request.Request(
            WEB.replace("28080", "18000") + "/api/stories?project_id=3&limit=1",
            headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req2, timeout=15) as r:
            _stories = _json.loads(r.read())
        _sid = _stories["items"][0]["id"]
        page.goto(WEB + f"/story/{_sid}", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        print(f"[3] Story 页 OK (story={_sid})")

        browser.close()

    print("console errors:", console_errors[:5])
    print("page errors:", page_errors[:5])
    print("bad requests:", bad[:5])
    if console_errors or page_errors or bad:
        fails.append("JS 报错/失败请求非零")
    if fails:
        print("FAILS:", fails)
        sys.exit(1)
    print("PLAYWRIGHT ALL PASS")


if __name__ == "__main__":
    main()
