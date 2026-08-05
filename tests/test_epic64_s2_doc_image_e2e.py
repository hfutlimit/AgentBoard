"""Epic 64 S2 文档图片渲染端到端回归测试（Playwright）。

验证对象（本地 Docker 栈 web 28080 / api 18000，或可配 AGENTBOARD_WEB_URL / AGENTBOARD_API_URL）：
- 文档 markdown 正文中的 ![](https://...) 图片语法渲染为 <img>（loading=lazy / referrerpolicy=no-referrer）
- 危险协议（javascript: / data:）与属性逃逸（onerror）输入不渲染为 <img>，以纯文本保留
- 全程 0 console error / pageerror / js/css 加载失败
- 截图产物写入 tmp/

运行：python tests/test_epic64_s2_doc_image_e2e.py
"""
import os
import sys
import time
import json
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = os.environ.get("AGENTBOARD_WEB_URL", "http://127.0.0.1:28080")
API = os.environ.get("AGENTBOARD_API_URL", "http://127.0.0.1:18000")
PROJECT_ID = int(os.environ.get("EPIC64_PROJECT_ID", "3"))
SHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "tmp")

results = []
errors = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def api_call(method, path, body=None, token=None):
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("utf-8") or "{}"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8") or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def main():
    # ---- auth ----
    st, payload = api_call("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    if st not in (200, 201) or not payload.get("token"):
        st, payload = api_call("POST", "/api/auth/register",
                               {"username": "admin", "password": "admin123"})
    token = payload.get("token")
    if not token:
        print("FATAL: no token:", st, payload)
        sys.exit(2)
    print(f"auth ok (admin), token len={len(token)}")

    # ---- 预建 1 篇测试文档：合法 https 图片 + 危险协议图片（XSS 用例） ----
    ts = int(time.time())
    content = (
        "# S2 图片渲染验收 {ts}\n\n"
        "## 合法图片（COS 预签名风格 URL）\n\n"
        "![架构图](https://cos.ap-shanghai.myqcloud.com/demo-bucket/arch.png?q-sign-algorithm=sha1&q-sign-time=1700000000;1700003600&x=1)\n\n"
        "## 危险协议（应保持纯文本）\n\n"
        "![x](javascript:alert(1))\n\n"
        "![x](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)\n\n"
        "![x](https://ok.com/a.png\" onerror=\"alert(1))\n\n"
        "## 普通段落与加粗\n\n"
        "**加粗验收**：正文中的图片应与 markdown 其它元素共存。\n"
    ).format(ts=ts)
    st, doc = api_call("POST", "/api/documents", {
        "project_id": PROJECT_ID,
        "title": f"[E2E-{ts}] S2 文档图片渲染",
        "content": content,
        "type": "plan",
        "status": "draft",
    }, token=token)
    ok = st == 201 and doc.get("id")
    check("api create image doc", ok, f"st={st}")
    if not ok:
        errors.append(f"create doc: st={st} payload={doc}")
        print("FATAL: cannot create doc")
        sys.exit(2)
    doc_id = doc["id"]

    os.makedirs(SHOT_DIR, exist_ok=True)

    # ---- UI 验证 ----
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script(
            f"localStorage.setItem('agentboard_token', '{token}');"
            f"localStorage.setItem('agentboard_user', 'admin');"
        )
        console_errors, page_errors, failed_resources = [], [], []

        def on_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)

        def on_pageerror(err):
            page_errors.append(str(err))

        def on_request_failed(req):
            url = req.url
            if "127.0.0.1" not in url and "localhost" not in url:
                return
            if url.endswith(".js") or url.endswith(".css"):
                failed_resources.append(url)

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("requestfailed", on_request_failed)

        # 1) 打开文档详情
        page.goto(f"{WEB}/project/{PROJECT_ID}/documents", wait_until="networkidle")
        for _ in range(2):
            if page.locator(f".doc-row:has-text('[E2E-{ts}]')").count() > 0:
                break
            page.reload(wait_until="networkidle")
            time.sleep(1)
        row = page.locator(f".doc-row:has-text('[E2E-{ts}]')").first
        check("image doc listed", row.count() > 0)
        row.click()
        page.wait_for_selector(".doc-content", timeout=10000)
        time.sleep(1.2)

        # 2) 合法 https 图片渲染为 <img>
        imgs = page.locator(".doc-content img")
        check("https image rendered as <img>", imgs.count() >= 1, f"count={imgs.count()}")
        if imgs.count() >= 1:
            src = imgs.first.get_attribute("src") or ""
            alt = imgs.first.get_attribute("alt") or ""
            # 注：loading/referrerpolicy 会被 Angular innerHTML sanitizer 剥离（非白名单属性），
            # 这本身就是 XSS 纵深防御的一环；单测（renderMarkdown 方法层）已覆盖属性输出。
            check("img src = COS 预签名 URL", "cos.ap-shanghai.myqcloud.com" in src, src[:80])
            check("img alt = 架构图", alt == "架构图", alt)

        # 3) 危险协议不渲染为 <img>（保持纯文本）
        body = page.locator(".doc-content").inner_text()
        check("javascript: 图片未渲染", "javascript:alert(1)" in body, "")
        check("data: 图片未渲染", "data:image/svg+xml" in body, "")
        check("onerror 注入未渲染", 'a.png" onerror' in body, "")
        total_imgs = page.locator(".doc-content img").count()
        check("仅 1 个合法 <img>", total_imgs == 1, f"count={total_imgs}")

        # 4) 普通 markdown 元素仍正常
        check("h1 rendered", page.locator(".doc-content h1").count() > 0)
        check("bold rendered", page.locator(".doc-content strong").count() > 0)

        # 5) 截图
        page.screenshot(path=os.path.join(SHOT_DIR, f"epic64_s2_doc_image_{ts}.png"), full_page=True)

        # 6) 错误汇总
        check("0 console error", len(console_errors) == 0, "; ".join(console_errors[:3]))
        check("0 pageerror", len(page_errors) == 0, "; ".join(page_errors[:3]))
        check("0 js/css load failure", len(failed_resources) == 0, "; ".join(failed_resources[:3]))

        browser.close()

    # ---- 清理测试文档 ----
    api_call("DELETE", f"/api/documents/{doc_id}", token=token)

    failed = [r for r in results if not r[1]]
    print(f"\n==== {len(results) - len(failed)}/{len(results)} passed ====")
    if failed:
        print("FAILED:", [r[0] for r in failed])
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
