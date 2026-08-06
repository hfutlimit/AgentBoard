"""Epic 122 S1 M1 E2E 验证：登录 → Dashboard → 项目 3 → 无 JS/控制台错误 → 截图。

运行：
    venv python tests/shot_epic122_s1_m1.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://localhost:28080"
API = "http://localhost:18000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tmp")
os.makedirs(OUT, exist_ok=True)

TOKEN = None


def login_token() -> str:
    import urllib.request

    req = urllib.request.Request(
        f"{API}/api/auth/login",
        data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)["token"]


def main():
    global TOKEN
    TOKEN = login_token()
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script(f"localStorage.setItem('agentboard_token', {TOKEN!r});")
        page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: errors.append(f"PAGEERROR: {e}"))
        failed = []
        page.on("requestfailed", lambda r: failed.append(r.url) if "/api/" not in r.url else None)

        # 1. Dashboard 首页
        page.goto(f"{BASE}/", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        page.screenshot(path=os.path.join(OUT, "ep122_dashboard.png"), full_page=False)

        # 2. 进入项目 3（AgentBoard）
        page.goto(f"{BASE}/project/3", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        page.screenshot(path=os.path.join(OUT, "ep122_project3.png"), full_page=False)

        # 3. 打开 Epic 122 Story 列表
        page.goto(f"{BASE}/epic/122", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)
        page.screenshot(path=os.path.join(OUT, "ep122_epic122.png"), full_page=False)

        # 4. Story 评审态渲染验证（本机 DB 存在 story 211 = pending_review）
        page.goto(f"{BASE}/story/211", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)
        if page.url == f"{BASE}/":
            page.goto(f"{BASE}/story/211", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        page.screenshot(path=os.path.join(OUT, "ep122_story_review.png"), full_page=False)

        body = page.inner_text("body")
        assert "待评审" in body, "Story 评审态 chips 未渲染（pending_review → 待评审）"
        page.wait_for_timeout(500)
        browser.close()

    print("=== E2E RESULT ===")
    print(f"console errors/warnings: {len(errors)}")
    for e in errors[:10]:
        print("  ", e[:200])
    print(f"non-api failed requests: {len(failed)}")
    for f in failed[:5]:
        print("  ", f[:200])
    if errors:
        print("RESULT: FAIL (console errors)")
        sys.exit(1)
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
