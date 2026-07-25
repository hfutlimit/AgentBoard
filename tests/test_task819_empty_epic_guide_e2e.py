"""
Task 819: 空项目引导创建第一个 Epic —— 端到端验证
- 登录 admin -> 通过 API 新建一个空项目（0 Epic）
- 直接进入 /project/{id}，Epics 标签页自动加载
- 断言空状态引导(.empty-state-guide)渲染：标题「开启你的第一个 Epic」+ CTA「创建第一个 Epic」+ SVG 图标
- 点击 CTA -> 断言新建 Epic 弹窗(#create-modal)打开，标题「新建 Epic」，含 #create-title 输入框
- 关闭弹窗 -> 断言引导仍可见（未误建 Epic）
- 测试末删除空项目，不污染数据
- 断言：0 pageerror / console error / .js+.css 404
"""
import json
import sys
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:28080"
API = "http://127.0.0.1:18000"
USER = "admin"
PASS = "admin123"
SEED = "__E2E_EPIC_GUIDE__" + str(1787535748)


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
    created_project = None
    errors = []
    try:
        # 新建空项目（0 Epic）
        st, p = api("POST", "/api/projects", token=token, body={
            "name": SEED,
            "key": "E2EG",
            "description": "E2E 空项目，用于验证 Epic 空状态引导。",
        })
        assert st == 201, f"create project failed {st} {p}"
        pid = p["id"]
        created_project = pid
        print("created empty project:", pid)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox"])
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
            page.goto(WEB + f"/project/{pid}", wait_until="networkidle")

            # Epics 标签页默认加载，空项目应显示引导
            page.wait_for_selector(".empty-state-guide", timeout=15000)
            print("empty-state-guide rendered")

            # 标题
            title = page.locator(".empty-state-guide .empty-state-title").inner_text()
            assert "开启你的第一个 Epic" in title, f"title mismatch: {title}"

            # CTA 按钮
            cta = page.locator(".empty-state-guide button", has_text="创建第一个 Epic")
            assert cta.count() == 1, "CTA button '创建第一个 Epic' not found"

            # SVG 图标（premium 软色圆块图标）
            assert page.locator(".empty-state-guide .empty-state-icon svg").count() == 1, "icon svg missing"

            # 提示行
            assert page.locator(".empty-state-guide .empty-state-hint").count() == 1, "hint line missing"

            # 点击 CTA -> 新建 Epic 弹窗
            cta.click()
            page.wait_for_selector("#create-modal", timeout=8000)
            modal_title = page.locator("#create-modal h3").inner_text()
            assert "新建 Epic" in modal_title, f"modal title mismatch: {modal_title}"
            assert page.locator("#create-title").count() == 1, "title input missing in modal"
            print("create-epic modal opened")

            # 关闭弹窗（优先 Esc，兜底点关闭按钮）
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            if page.locator("#create-modal").count() == 1:
                page.locator("#create-modal .modal-close").click()
                page.wait_for_timeout(300)
            assert page.locator("#create-modal").count() == 0, "modal should close after cancel"

            # 引导仍存在（取消未误建 Epic）
            assert page.locator(".empty-state-guide").count() == 1, "guide should remain after cancel"

            browser.close()
    finally:
        if created_project is not None:
            st, _ = api("DELETE", f"/api/projects/{created_project}", token=token)
            print("cleanup project", created_project, "->", st)

    real_errors = [e for e in errors if "ERR_ABORTED" not in e and "ABORTED" not in e]
    if real_errors:
        print("ERRORS:", real_errors)
        sys.exit(1)
    print("E2E PASSED: 0 pageerror/console/.js+.css 404")


if __name__ == "__main__":
    main()
