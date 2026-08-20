"""Epic 150 / Story 322 (X1) PR 3 验证：home view 完全由 home-shell 接管 + 路由切换。

验证点：
1. home view 进入：home-shell 渲染、11 monogram、agents tab 切换
2. 切到 projects view：home-shell DOM 消失、projects 视图内容出现
3. 切回 home view：home-shell 重新出现、monogram 仍在
4. view 切走期间 dashboard 永远不出现（hero / stats / analytics 都 false）
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

FRONTEND_ORIGIN = "http://127.0.0.1:4200"
PROD_API = "http://124.220.44.12"
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"

ROOT = Path(__file__).resolve().parent
SHOT_DIR = ROOT / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


def login() -> str:
    """经生产 API 直接拿 token（避免 Playwright 走 login 表单）。"""
    req = urllib.request.Request(
        PROD_API + "/api/auth/login",
        data=json.dumps({"username": ADMIN_USER, "password": ADMIN_PASS}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["token"]


def has_home_shell(page: Page) -> bool:
    return page.locator("app-home-shell .home-shell-v7").count() > 0


def monogram_count(page: Page) -> int:
    return page.locator("app-home-shell .project-monogram-v7").count()


def has_agents_table(page: Page) -> bool:
    return page.locator("app-home-shell .agent-table-v7").count() > 0


def agent_row_count(page: Page) -> int:
    return page.locator("app-home-shell .agent-row-v7").count()


def dashboard_visible(page: Page) -> dict[str, bool]:
    """检查原 home view dashboard 三块（hero / stats / analytics）是否存在。"""
    return {
        "hero": page.locator("section.hero").count() > 0,
        "stats": page.locator(".stats-row").count() > 0,
        "analytics": page.locator(".dashboard-analytics").count() > 0,
    }


def click_sidebar_item(page: Page, label: str) -> None:
    """点击 sidebar nav item 切换 view。"""
    sel = page.locator(f"a.sidebar-nav-item:has-text('{label}')").first
    sel.click()
    page.wait_for_load_state("networkidle", timeout=10000)
    time.sleep(0.3)


def main() -> int:
    failures: list[str] = []
    token = login()
    print(">>> got token len=%d" % len(token))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            # 注入 token 让 SPA 跳过登录页
            page.add_init_script(
                "localStorage.setItem('agentboard_token', %s);"
                "localStorage.setItem('agentboard_user', 'admin');" % json.dumps(token)
            )
            print(">>> goto home (/)")
            page.goto(f"{FRONTEND_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)

            # 默认视图（routerLink="/" 就是 home）— 等待 home-shell 出现
            page.wait_for_selector("app-home-shell .home-shell-v7", timeout=30000)
            time.sleep(0.5)

            # 验证 1：home view 完整渲染
            print(">>> STEP 1 — verify home view (default)")
            assert has_home_shell(page), "home-shell should be present on home view"
            m = monogram_count(page)
            print(f"   monogram count = {m}")
            if m < 5:
                failures.append(f"home view monogram count too low: {m}")
            dbg = dashboard_visible(page)
            print(f"   dashboard leaks = {dbg}")
            if any(dbg.values()):
                failures.append(f"home view: dashboard leaked back: {dbg}")
            page.screenshot(path=str(SHOT_DIR / "_x1_pr3_01_home.png"), full_page=False)

            # agents tab 切换
            agents_btn = page.locator("app-home-shell .hs-tab-button:has-text('Agents')").first
            agents_btn.click()
            time.sleep(0.4)
            tbl = has_agents_table(page)
            rows = agent_row_count(page)
            print(f"   agents table={tbl} rows={rows}")
            if not tbl:
                failures.append("agents table not visible after tab switch")
            if rows < 3:
                failures.append(f"agent rows too few: {rows}")
            page.screenshot(path=str(SHOT_DIR / "_x1_pr3_02_agents.png"), full_page=False)

            # 切回 projects tab
            proj_btn = page.locator("app-home-shell .hs-tab-button:has-text('项目')").first
            proj_btn.click()
            time.sleep(0.3)

            # 验证 2：切到 projects view
            print(">>> STEP 2 — switch to projects view")
            click_sidebar_item(page, "项目")
            time.sleep(0.5)
            shell_after_switch = has_home_shell(page)
            print(f"   home-shell after projects switch = {shell_after_switch}")
            if shell_after_switch:
                failures.append("home-shell should NOT be visible when view=projects")
            dbg2 = dashboard_visible(page)
            print(f"   dashboard leaks (projects view) = {dbg2}")
            if any(dbg2.values()):
                failures.append(f"projects view: dashboard leaked: {dbg2}")
            proj_center = page.locator("h2:has-text('项目中心')").count() > 0
            if not proj_center:
                failures.append("projects center heading missing")
            page.screenshot(path=str(SHOT_DIR / "_x1_pr3_03_projects.png"), full_page=False)

            # 验证 3：切回 home view
            print(">>> STEP 3 — switch back to home view")
            click_sidebar_item(page, "仪表盘")
            time.sleep(0.5)
            page.wait_for_selector("app-home-shell .home-shell-v7", timeout=10000)
            time.sleep(0.3)
            shell_back = has_home_shell(page)
            m_back = monogram_count(page)
            print(f"   home-shell after home switch = {shell_back} monograms={m_back}")
            if not shell_back:
                failures.append("home-shell should be visible after switching back to home")
            if m_back < 5:
                failures.append(f"home view after switch back: monogram count too low: {m_back}")
            dbg3 = dashboard_visible(page)
            print(f"   dashboard leaks (back to home) = {dbg3}")
            if any(dbg3.values()):
                failures.append(f"home view (back): dashboard leaked: {dbg3}")
            page.screenshot(path=str(SHOT_DIR / "_x1_pr3_04_home_back.png"), full_page=False)

            # 验证 4：切到 agents view (sidebar 里的 Agents)
            print(">>> STEP 4 — switch to agents view (sidebar)")
            click_sidebar_item(page, "Agents")
            time.sleep(0.5)
            shell_agents_view = has_home_shell(page)
            print(f"   home-shell after agents-view switch = {shell_agents_view}")
            if shell_agents_view:
                failures.append("home-shell should NOT be visible when view=agents")
            dbg4 = dashboard_visible(page)
            print(f"   dashboard leaks (agents view) = {dbg4}")
            if any(dbg4.values()):
                failures.append(f"agents view: dashboard leaked: {dbg4}")
            page.screenshot(path=str(SHOT_DIR / "_x1_pr3_05_agents_view.png"), full_page=False)

        except Exception as e:
            failures.append(f"exception: {e!r}")
            try:
                page.screenshot(path=str(SHOT_DIR / "_x1_pr3_error.png"), full_page=True)
            except Exception:
                pass
        finally:
            ctx.close()
            browser.close()

    print()
    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS — X1 PR 3 route switch OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
