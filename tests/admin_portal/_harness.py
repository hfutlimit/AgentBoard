"""Admin Portal E2E 共享骨架。

提供:
  - start_browser(): 启动 Chromium + 装配错误/401 采集器
  - login_ui(page): 通过 UI 完成登录并跳转 dashboard
  - check_errors(...): 过滤良性噪声后返回问题列表

所有 admin-portal E2E 测试统一复用本模块，避免重复装配逻辑。
"""
import os
import sys

from playwright.sync_api import sync_playwright

# 服务基址: 默认指向 scripts/serve_admin_portal.py 启动的静态+代理服务
BASE = os.environ.get("ADMIN_PORTAL_URL", "http://127.0.0.1:4321")

ADMIN_USER = os.environ.get("ADMIN_PORTAL_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PORTAL_PASS", "admin123")


def start_browser():
    """启动浏览器并装配错误采集, 返回 (pw, browser, page, errors, resp_401)。"""
    pw = sync_playwright().start()
    browser = pw.chromium.launch(args=["--no-proxy-server"])
    ctx = browser.new_context()
    page = ctx.new_page()

    errors = []      # (kind, type, text)
    resp_401 = []    # urls returning 401

    page.on(
        "console",
        lambda m: errors.append(("console", m.type, m.text))
        if m.type == "error"
        else None,
    )
    page.on("pageerror", lambda e: errors.append(("pageerror", "error", str(e))))
    page.on(
        "requestfailed",
        lambda r: errors.append(("reqfail", r.url, r.failure))
        if (r.url.endswith(".js") or r.url.endswith(".css"))
        else None,
    )
    page.on("response", lambda r: resp_401.append(r.url) if r.status == 401 else None)

    return pw, browser, page, errors, resp_401


def login_ui(page):
    """通过登录表单完成认证并等待跳转 dashboard。"""
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_url("**/login", timeout=15000)
    page.fill('input[name="username"]', ADMIN_USER)
    page.fill('input[name="password"]', ADMIN_PASS)
    page.click('button[type="submit"]')
    page.wait_for_url("**/dashboard", timeout=15000)


def check_errors(errors, resp_401, allow_login_401=True):
    """过滤良性噪声, 返回问题字符串列表 (空=通过)。"""
    # 良性噪声: ERR_ABORTED(导航中断) / favicon 资源失败
    real = [
        e
        for e in errors
        if not (isinstance(e[2], str) and "ERR_ABORTED" in e[2])
        and not (e[0] == "reqfail" and "favicon" in e[1])
    ]
    # 登录 401 是预期路径(错误凭据), 对应一条 console 401 属良性, 予以放行
    console_401 = [e for e in real if e[0] == "console" and "401" in str(e[2])]
    other_real = [e for e in real if e not in console_401]

    login_401 = [u for u in resp_401 if u.rstrip("/").endswith("/api/auth/login")]
    if console_401 and allow_login_401 and len(login_401) < 1:
        other_real.append(("console", "error", "存在 401 控制台错误但无登录 401 响应"))

    other_401 = [u for u in resp_401 if not u.rstrip("/").endswith("/api/auth/login")]

    problems = []
    if other_401:
        problems.append(f"非预期 401: {other_401}")
    if other_real:
        problems.append(f"前端错误: {other_real}")
    return problems


def report(name, problems):
    if problems:
        print(f"FAILED [{name}]:")
        for x in problems:
            print("   ", x)
        return False
    print(f"PASS [{name}]")
    return True
