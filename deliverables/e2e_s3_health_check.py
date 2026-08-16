"""Epic 140 切片 3 前端健康检查 E2E（一次性验证脚本，不入库）。

验证本地 28080 前端核心渲染：admin 登录 → 项目列表页 → 项目详情/看板
无 JS 报错、无 404、无 console error、无 js/css 加载失败。
"""
import json
import urllib.error
import urllib.request
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:18000"
WEB = "http://127.0.0.1:28080"
USER = "admin"
PASS = "admin123"


def api(method, path, token=None, body=None):
    req = urllib.request.Request(API + path,
                                 data=json.dumps(body).encode() if body else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def main():
    st, u = api("POST", "/api/auth/login",
                body={"username": USER, "password": PASS})
    assert st == 200, f"login failed {st}"
    token = u["token"]
    print("[OK] login")

    # 取第一个可见项目（admin 可见全部）
    st, data = api("GET", "/api/projects", token=token)
    projects = data if isinstance(data, list) else (data or {}).get("items", [])
    assert projects, "无可见项目"
    pid = projects[0]["id"]
    print(f"[OK] 取到项目 #{pid}")

    errors = []
    js_css_fail = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
        page.on("console", lambda m: errors.append("console: " + m.text)
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: (
            js_css_fail.append(r.url) if (r.url.endswith(".js") or r.url.endswith(".css")) else None
        ))

        # 登录页 → 走真实 UI 登录（不注入 localStorage，验证鉴权链路）
        page.goto(WEB + "/login", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        page.fill('input[placeholder="请输入用户名"]', USER)
        page.fill('input[placeholder="请输入密码"]', PASS)
        page.click("button:has-text('登')")
        page.wait_for_timeout(5000)
        body_text = page.inner_text("body")
        assert "项目" in body_text, f"登录后未进入项目页: {body_text[:120]}"
        print("[OK] UI 登录成功")

        # 项目列表页（登录后默认落地）
        page.wait_for_timeout(2000)
        body_text = page.inner_text("body")
        assert "项目" in body_text, "项目列表页未渲染"
        print("[OK] /projects 渲染")

        # 项目详情/看板
        page.goto(f"{WEB}/projects/{pid}", wait_until="domcontentloaded")
        page.wait_for_timeout(4500)
        body_text = page.inner_text("body")
        assert "看板" in body_text or "任务" in body_text or "Story" in body_text, \
            f"项目页未渲染核心内容: {body_text[:120]}"
        page.screenshot(path="deliverables/e2e_s3_project.png", full_page=False)
        print("[OK] 项目页渲染 + 截图")

        browser.close()

    print(f"[E2E] pageerror/console errors: {len(errors)}")
    print(f"[E2E] js/css 404/失败: {len(js_css_fail)}")
    for e in errors[:5]:
        print("  ERR:", e)
    for f in js_css_fail[:5]:
        print("  FAIL:", f)
    assert not errors, f"页面存在 JS 错误: {errors[:3]}"
    assert not js_css_fail, f"存在 js/css 加载失败: {js_css_fail[:3]}"
    print("[PASS] S3 E2E 健康检查通过")


if __name__ == "__main__":
    main()
