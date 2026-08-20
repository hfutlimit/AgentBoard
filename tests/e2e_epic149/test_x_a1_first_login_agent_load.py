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

E2E 验证（main() 脚本，与同目录 test_x_*.py 一致）：
1. **清空 localStorage** + page.goto / → SPA 应显示登录 modal（authVisible=true）。
2. 填 admin/admin123 → click 提交。
3. 等登录 modal 关闭 + home shell 出现。
4. 等若干秒（loadAgents 异步 + 后端处理）。
5. 验证 home shell 中 Agents tab 表格行数 ≥ 1（dev DB 至少有 1 个 agent）。
6. 截图 ``_x_a1_home_after_login.png``。

注：本测试**不复用 token-injection 模式**（直接走登录流程），
确保覆盖「登录后」这条 race 路径。任务由 review 阻断级 3 标注。

2026-08-20 创建。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

FRONTEND_ORIGIN = "http://127.0.0.1:4200"
API_BASE = os.environ.get("AGENTBOARD_API_BASE", "http://127.0.0.1:18000")
ADMIN_USER = os.environ.get("AGENTBOARD_E2E_USER", "admin")
ADMIN_PASS = os.environ.get("AGENTBOARD_E2E_PASS", "admin123")

ROOT = Path(__file__).resolve().parent
SHOT_DIR = ROOT / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str, *, flush: bool = True) -> None:
    """Print + flush immediately（避免 PowerShell 缓冲吞输出）。"""
    print(msg, flush=flush)


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
        # 200 (idempotent 已存在) 或 201 都行；422/403/500 才报错
        if e.code not in (200, 201):
            log(f"FAIL: seed agent failed: {e.code} {e.reason}")
            raise


def click_agents_tab_in_home_shell(page: Page) -> None:
    """在 home shell 切到 Agents tab（用 JS evaluate 避免 page.locator hang）。"""
    page.evaluate(
        """
        const btn = document.querySelector('app-home-shell .hs-tab-button');
        for (const b of document.querySelectorAll('app-home-shell .hs-tab-button')) {
            if (b.textContent && b.textContent.includes('Agents')) { b.click(); break; }
        }
        """
    )


def main() -> int:
    failures: list[str] = []

    # 1) 后端至少 1 个 agent（前置条件）
    log(">>> seed at least 1 agent (dev DB may be empty)")
    try:
        ensure_seed_agent()
    except Exception as e:
        log(f"FAIL: cannot seed agent: {e!r}")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            # 2) page.goto / → 应显示登录 modal（authVisible=true）
            # 不用 add_init_script（经验证它会让 Playwright sync 通信 hang），
            # 改用「首次 goto → evaluate 清 localStorage → reload」清干净。
            log(">>> STEP 1 — open / with cleared localStorage")
            page.goto(f"{FRONTEND_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)
            log("   domcontentloaded", flush=True)
            # 清 localStorage + reload（避免 localStorage 缓存 token）
            page.evaluate("try { localStorage.clear(); } catch(e) {}")
            page.reload(wait_until="domcontentloaded", timeout=30000)
            log("   domcontentloaded (after clear+reload)", flush=True)
            n0 = page.locator("input[name='username']").count()
            log(f"   immediate count = {n0}", flush=True)
            # 验证 input 已可见（immediate count 已是 1，跳过 polling）
            if n0 == 0:
                log("   waiting for input to appear (polling) ...", flush=True)
                for i in range(30):  # 最多 15s
                    n = page.locator("input[name='username']").count()
                    if n > 0:
                        log(f"   login form visible at iter {i} (count={n}) ✓", flush=True)
                        break
                    time.sleep(0.5)
                else:
                    log(f"   login form NEVER visible in 15s", flush=True)
                    page.screenshot(path=str(SHOT_DIR / "_x_a1_no_login.png"), full_page=True)
                    body_text = page.evaluate("document.body.innerText.slice(0, 500)")
                    log(f"   body text: {body_text}", flush=True)
                    failures.append("login form not shown within 15s")
                    return 1
            else:
                log("   skip polling, immediate count already 1", flush=True)

            # 3) 填表 + 提交（用 JS evaluate 避开 Playwright fill/click 偶发 hang）
            log(">>> STEP 2 — fill credentials and submit", flush=True)
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
            log(f"   fill+submit result: {res}", flush=True)
            # 等若干秒让 Angular 处理登录（不 networkidle 因为 SPA 持续发请求）
            time.sleep(3.0)
            log("   sleep 3.0 done", flush=True)

            # 4) 等登录 modal 关闭 + home shell 出现（用 JS evaluate 轮询）
            log(">>> STEP 3 — wait for home shell (login success)", flush=True)
            n_home = page.evaluate(
                "document.querySelectorAll('app-home-shell .home-shell-v7').length"
            )
            log(f"   immediate home shell count = {n_home}", flush=True)
            if n_home == 0:
                # 轮询
                for i in range(40):  # 最多 20s
                    time.sleep(0.5)
                    n = page.evaluate(
                        "document.querySelectorAll('app-home-shell .home-shell-v7').length"
                    )
                    if n > 0:
                        log(f"   home shell visible at iter {i} (n={n}) ✓", flush=True)
                        break
                else:
                    log(f"   home shell NEVER visible in 20s", flush=True)
                    page.screenshot(path=str(SHOT_DIR / "_x_a1_no_home.png"), full_page=True)
                    failures.append("home shell not shown after login within 20s")
                    return 1
            else:
                log(f"   skip polling, immediate home shell count = {n_home}", flush=True)

            # 5) 等 loadAgents 完成（异步 + 后端处理）
            #    切到 Agents tab 看 row 数（用 JS click 避开 Playwright click hang）
            log(">>> STEP 4 — verify Agents tab has data (loadAgents ran)", flush=True)
            time.sleep(1.0)  # 让 loadAgents 异步完成
            # 用 JS evaluate 切到 Agents tab
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
            # 立即看 agent row
            rows = page.evaluate(
                "document.querySelectorAll('app-home-shell .agent-row-v7').length"
            )
            log(f"   agents rows in home shell = {rows}", flush=True)
            if rows < 1:
                failures.append(
                    f"agents row count after first login = {rows} (expected ≥ 1; "
                    "loadAgents race regression — Task 1298 broken)"
                )

            page.screenshot(path=str(SHOT_DIR / "_x_a1_home_after_login.png"), full_page=True)
        except Exception as e:
            log(f"   EXCEPTION: {e!r}")
            failures.append(f"exception: {e!r}")
            try:
                page.screenshot(path=str(SHOT_DIR / "_x_a1_error.png"), full_page=True)
            except Exception:
                pass
        finally:
            ctx.close()
            browser.close()

    log("")
    if failures:
        log("FAIL:")
        for f in failures:
            log("  -", f)
        return 1
    log("PASS — X.A1 first-login agent load OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
