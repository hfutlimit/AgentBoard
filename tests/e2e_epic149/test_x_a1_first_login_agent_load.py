"""Epic 151 / Story 326 / Task 1298 端到端验证：首次登录 Agent 加载竞态。

背景（Epic 149 静态 Review 阻断级 3）：
- ``ngOnInit()`` 同步调 ``validateAuth()`` + ``loadAgents()``。
- 首次访问无 token 时，prod REQUIRE_AUTH=1 下 ``loadAgents()`` 失败（401）；
  dev mode 拿空数据。两种情况都导致登录成功后，``loadAgents()`` 不会
  重新触发（``authenticate()`` 内部没补 ``loadAgents()`` 调用）。
- 结果：首次登录后侧栏「X 个 Agents 在线」= 0、members tab Agent 表为空，
  必须刷新或进 Agents 池视图才恢复。

Task 1298 修复：
- ``app.ts authenticate()`` 成功回调追加 ``void this.loadAgents()``，
  重新拉全局 Agent 池（带 Bearer token 走通鉴权）。

E2E 验证（Story 330 / Task 1323 改造为 pytest）：
1. **清空 localStorage** + page.goto / → SPA 应显示登录 modal（authVisible=true）。
2. 填 admin/admin123 → click 提交。
3. 等登录 modal 关闭 + home shell 出现。
4. 等若干秒（loadAgents 异步 + 后端处理）。
5. 验证 home shell 中 Agents tab 表格行数 ≥ 1（dev DB 至少有 1 个 agent）。
6. 截图 ``_x_a1_home_after_login.png``。

注：本测试**不复用 token-injection 模式**（直接走登录流程），
确保覆盖「登录后」这条 race 路径。任务由 review 阻断级 3 标注。

2026-08-20 创建；Story 330 改造为 pytest 收集格式。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import pytest
try:
    from playwright.sync_api import Page
except ModuleNotFoundError:  # pragma: no cover - collected without E2E extras
    Page = object

from conftest import (
    FRONTEND_ORIGIN,
    SHOT_DIR,
    ADMIN_USER,
    ADMIN_PASS,
    API_BASE,
    log,
)


def ensure_seed_agent() -> None:
    """确保 dev DB 至少有 1 个 agent（dev DB 可能空，Task 1297 E2E 残留的）。"""
    req = urllib.request.Request(
        f"{API_BASE}/api/auth/login",
        data=json.dumps({"username": ADMIN_USER, "password": ADMIN_PASS}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        token = json.loads(r.read().decode())["token"]

    body = json.dumps({
        "agent_id": "e2e-x-a1-seed-bot",
        "name": "E2E X.A1 Seed Bot",
        "roles": '["reviewer"]',
        "capabilities": "[]",
        "cli_command": "codebuddy --model {model}",
        "model": "hy3",
        "auth_key": "abk_e2e_x_a1_seed",  # noqa: S105
    }).encode()
    req = urllib.request.Request(f"{API_BASE}/api/agents/register", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            log(f">>> seed agent: status {r.status}")
    except urllib.error.HTTPError as e:
        if e.code not in (200, 201):
            log(f"FAIL: seed agent failed: {e.code} {e.reason}")
            raise


@pytest.mark.e2e
def test_first_login_loads_agents(browser) -> None:
    """首次登录后 Agents 立即加载（Task 1298 修复验证）。"""
    # 1) 后端 seed 1 agent
    log(">>> seed at least 1 agent (dev DB may be empty)")
    ensure_seed_agent()

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        # 2) page.goto / + 清 localStorage + reload（避免 token 缓存）
        log(">>> STEP 1 — open / with cleared localStorage")
        page.goto(f"{FRONTEND_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)
        page.evaluate("try { localStorage.clear(); } catch(e) {}")
        page.reload(wait_until="domcontentloaded", timeout=30000)

        # 3) 等 login form 出现
        n0 = page.locator("input[name='username']").count()
        log(f"   immediate count = {n0}")
        if n0 == 0:
            for i in range(30):
                n = page.locator("input[name='username']").count()
                if n > 0:
                    log(f"   login form visible at iter {i}")
                    break
                time.sleep(0.5)
            else:
                page.screenshot(path=str(SHOT_DIR / "_x_a1_no_login.png"), full_page=True)
                pytest.fail("login form not shown within 15s")
        else:
            log("   skip polling, immediate count already 1")

        # 4) 填表 + 提交
        log(">>> STEP 2 — fill credentials and submit")
        fill_js = f"""
        () => {{
            const u = document.querySelector('input[name="username"]');
            const p = document.querySelector('input[name="password"]');
            if (u) {{
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                nativeSetter.call(u, '{ADMIN_USER}');
                u.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}
            if (p) {{
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                nativeSetter.call(p, '{ADMIN_PASS}');
                p.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}
            const btn = document.querySelector('app-login button[type="submit"]')
                || document.querySelector('button[type="submit"]');
            if (btn) btn.click();
            return {{ u: !!u, p: !!p, btn: !!btn }};
        }}
        """
        res = page.evaluate(fill_js)
        log(f"   fill+submit result: {res}")
        time.sleep(3.0)

        # 5) 等 home shell 出现
        log(">>> STEP 3 — wait for home shell (login success)")
        n_home = page.evaluate(
            "document.querySelectorAll('app-home-shell .home-shell-v7').length"
        )
        log(f"   immediate home shell count = {n_home}")
        if n_home == 0:
            for i in range(40):
                time.sleep(0.5)
                n = page.evaluate(
                    "document.querySelectorAll('app-home-shell .home-shell-v7').length"
                )
                if n > 0:
                    log(f"   home shell visible at iter {i}")
                    break
            else:
                page.screenshot(path=str(SHOT_DIR / "_x_a1_no_home.png"), full_page=True)
                pytest.fail("home shell not shown after login within 20s")
        else:
            log("   skip polling, immediate home shell count = 1")

        # 6) 等 loadAgents 完成 + 切到 Agents tab 验证
        log(">>> STEP 4 — verify Agents tab has data (loadAgents ran)")
        time.sleep(1.0)
        page.evaluate(
            """
            (() => {
                for (const b of document.querySelectorAll('app-home-shell .hs-tab-button')) {
                    if (b.textContent && b.textContent.includes('Agents')) { b.click(); break; }
                }
            })()
            """
        )
        time.sleep(0.5)
        rows = page.evaluate(
            "document.querySelectorAll('app-home-shell .agent-row-v7').length"
        )
        log(f"   agents rows in home shell = {rows}")
        assert rows >= 1, (
            f"agents row count after first login = {rows} (expected ≥ 1; "
            "loadAgents race regression — Task 1298 broken)"
        )

        page.screenshot(path=str(SHOT_DIR / "_x_a1_home_after_login.png"), full_page=True)
    finally:
        ctx.close()


# ============================================================
# 兼容老 main() 入口
# ============================================================

def main() -> int:
    """手动跑用。"""
    from playwright.sync_api import sync_playwright

    log(">>> seed agent (main 入口)")
    ensure_seed_agent()

    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            page.goto(f"{FRONTEND_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)
            page.evaluate("try { localStorage.clear(); } catch(e) {}")
            page.reload(wait_until="domcontentloaded", timeout=30000)
            n0 = page.locator("input[name='username']").count()
            if n0 == 0:
                for i in range(30):
                    n = page.locator("input[name='username']").count()
                    if n > 0:
                        break
                    time.sleep(0.5)
                else:
                    failures.append("login form not shown within 15s")

            fill_js = f"""
            () => {{
                const u = document.querySelector('input[name="username"]');
                const p = document.querySelector('input[name="password"]');
                if (u) {{
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(u, '{ADMIN_USER}');
                    u.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
                if (p) {{
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(p, '{ADMIN_PASS}');
                    p.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
                const btn = document.querySelector('app-login button[type="submit"]')
                    || document.querySelector('button[type="submit"]');
                if (btn) btn.click();
                return {{ u: !!u, p: !!p, btn: !!btn }};
            }}
            """
            page.evaluate(fill_js)
            time.sleep(3.0)
            n_home = page.evaluate("document.querySelectorAll('app-home-shell .home-shell-v7').length")
            if n_home == 0:
                for i in range(40):
                    time.sleep(0.5)
                    n_home = page.evaluate("document.querySelectorAll('app-home-shell .home-shell-v7').length")
                    if n_home > 0:
                        break
                else:
                    failures.append("home shell not shown after login within 20s")
            time.sleep(1.0)
            page.evaluate("""
                for (const b of document.querySelectorAll('app-home-shell .hs-tab-button')) {
                    if (b.textContent && b.textContent.includes('Agents')) { b.click(); break; }
                }
            """)
            time.sleep(0.5)
            rows = page.evaluate("document.querySelectorAll('app-home-shell .agent-row-v7').length")
            log(f"   agents rows = {rows}")
            if rows < 1:
                failures.append(f"agents row count {rows} < 1")
        finally:
            ctx.close()
            browser.close()

    if failures:
        log("FAIL:")
        for f in failures:
            log("  -", f)
        return 1
    log("PASS — X.A1 first-login agent load OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
