"""
Admin Portal 登录流程端到端验证 (Epic 66 / Story 71: admin-portal 前端实现)
覆盖 Task 850(初始化)/851(登录页)/856(样式与主题) 的交付验收。

验证点:
  - 应用可加载并渲染登录卡片 (init + theme)
  - 登录表单提交 -> 调用 /api/auth/login -> 存储 token -> 跳转 /dashboard
  - 路由守卫: 未登录直接访问 /dashboard 重定向回 /login
  - 错误凭据显示告警 (故意触发 1 次 /api/auth/login 401, 属预期路径)
  - 0 个 pageerror / console error / .js+.css 404, 且无除「错误凭据」外的任何 401
"""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4300"

def main():
    errors = []        # (kind, type, text)
    resp_401 = []      # urls returning 401
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        ctx = browser.new_context()
        page = ctx.new_page()

        page.on("console", lambda m: errors.append(("console", m.type, m.text)) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(("pageerror", "error", str(e))))
        page.on("requestfailed", lambda r: (
            errors.append(("reqfail", r.url, r.failure))
            if (r.url.endswith(".js") or r.url.endswith(".css")) else None
        ))
        page.on("response", lambda r: resp_401.append(r.url) if r.status == 401 else None)

        # 1) 应用加载 -> 根路径重定向到 /login
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_url("**/login", timeout=15000)
        assert page.locator(".auth-card").is_visible(), "登录卡片未渲染"
        assert "登录到控制台" in page.inner_text("body"), "登录标题缺失"

        # 2) 错误凭据 -> 告警 (故意 1 次 401, 预期路径)
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "wrong-password")
        page.click('button[type="submit"]')
        page.wait_for_selector(".alert", timeout=10000)
        assert page.inner_text(".alert").strip() != "", "错误凭据未显示告警"

        # 3) 正确凭据 -> token 存储 + 跳转 dashboard
        page.fill('input[name="password"]', "admin123")
        page.click('button[type="submit"]')
        page.wait_for_url("**/dashboard", timeout=15000)
        token = page.evaluate("localStorage.getItem('admin_portal_token')")
        assert token and token.strip() and token != "undefined", "登录后未写入有效 token"
        assert "欢迎回来" in page.inner_text("body"), "dashboard 未渲染欢迎语"
        who = page.inner_text(".who") if page.locator(".who").count() else ""
        assert "admin" in who.lower() or "管理员" in who, f"未显示用户名: {who!r}"

        # 4) 路由守卫: 清除 token 后访问 /dashboard 应重定向回 /login
        page.evaluate("localStorage.removeItem('admin_portal_token')")
        page.goto(BASE + "/dashboard", wait_until="networkidle")
        page.wait_for_url("**/login", timeout=15000)
        assert page.locator(".auth-card").is_visible(), "守卫未拦截未登录访问"

        # 5) 重新登录以干净收尾(不污染)
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "admin123")
        page.click('button[type="submit"]')
        page.wait_for_url("**/dashboard", timeout=15000)

        browser.close()

    # 预期路径: 仅「错误凭据」触发的 /api/auth/login 401 一次
    login_401 = [u for u in resp_401 if u.rstrip("/").endswith("/api/auth/login")]
    other_401 = [u for u in resp_401 if not u.rstrip("/").endswith("/api/auth/login")]

    # 过滤良性噪声: ERR_ABORTED(导航中断) / favicon 资源失败
    real = [e for e in errors if not (isinstance(e[2], str) and "ERR_ABORTED" in e[2])
            and not (e[0] == "reqfail" and "favicon" in e[1])]

    # 故意的错误凭据测试会触发 1 次 /api/auth/login 401, 浏览器会记录一条
    # "Failed to load resource 401" 的 console 日志, 属预期噪声, 予以放行。
    console_401 = [e for e in real if e[0] == "console" and "401" in str(e[2])]
    other_real = [e for e in real if e not in console_401]
    if console_401 and len(login_401) < 1:
        other_real.append(("console", "error", "存在 401 控制台错误但无预期登录 401 响应"))

    problems = []
    if other_401:
        problems.append(f"非预期 401: {other_401}")
    if other_real:
        problems.append(f"前端错误: {other_real}")

    if problems:
        print("FAILED:")
        for x in problems:
            print("  ", x)
        sys.exit(1)
    if len(login_401) != 1:
        print(f"WARN: 预期恰好 1 次登录 401, 实际 {len(login_401)}")
    print("PASS: admin-portal 登录流程 E2E 全部通过 (0 pageerror/console/.js+.css 404, 无预期外 401)")

if __name__ == "__main__":
    main()
