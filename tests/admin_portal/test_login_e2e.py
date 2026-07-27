"""Admin Portal 登录流程端到端验证 (Story 71: admin-portal 前端实现)。

覆盖 Task 850(初始化)/851(登录页)/856(样式与主题) 的交付验收。
复用 tests/admin_portal/_harness.py 的统一浏览器装配与错误采集。
"""
import sys

from _harness import BASE, start_browser, check_errors, report


def main():
    pw, browser, page, errors, resp_401 = start_browser()
    try:
        # 1) 应用加载 -> 根路径重定向到 /login
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_url("**/login", timeout=15000)
        assert page.locator(".auth-card").is_visible(), "登录卡片未渲染"
        assert "登录到控制台" in page.inner_text("body"), "登录标题缺失"

        # 2) 错误凭据 -> 告警 (预期 1 次 /api/auth/login 401)
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

        problems = check_errors(errors, resp_401, allow_login_401=True)
        ok = report("login", problems)
        browser.close()
        pw.stop()
        sys.exit(0 if ok else 1)
    except AssertionError as e:
        print("FAILED [login]:", e)
        browser.close()
        pw.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
