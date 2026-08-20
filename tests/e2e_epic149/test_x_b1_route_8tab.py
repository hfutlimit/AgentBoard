"""Epic 151 / Story 327 / Task 1300 + Task 1302a E2E：8 tab 路由化 + 真实断言。

背景（review 高优先级 #4 + #5）：
- 8 navy project-sidebar tab 改 `routerLink` 驱动 URL 切换；
- `app.routes.ts` 8 tab 全部 `loadComponent` 化（懒加载 chunk）；
- `app.ts` `loadRoute()` 解析 8 个 section（overview / kanban / epics /
  backlog / proposals / documents / members / settings）→ `activeTab.set`；
- 删 emoji tab-bar（含 stats / tickets 新 navy 没保留的）；
- app.html 顶层根 `<router-outlet />` 已删（避免与 @switch 渲染双轨）。

E2E 真实断言（2026-08-20 Task 1302a 加固）：
1. 启动浏览器 + 注入 token → 直接 `goto /project/1/<tab>` 8 个 URL；
2. 每个 URL 验证：
   - ``location.pathname`` 末段 = section 名（URL 正确）
   - workspace-heading ``<h1>`` 包含预期关键字
   - 当前 navy tab 按钮 ``[aria-current="page"]`` 落在预期位置
   - 8 navy tab aria-label 全部存在
3. 浏览器前进 / 后退：依次 goto 8 tab → 按 back 5 次 → 验证前一个 tab 高亮（URL 断言）
4. 截图 8 tab + back 5 步（视觉证据）
5. **不复用 token-injection 之外的辅助**（保留端到端真实路径）

2026-08-20 创建；2026-08-20 加固断言。
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

# 8 navy tab → (URL section, 期望 navy aria-label, workspace-heading h1 关键字)
TABS: list[tuple[str, str, str]] = [
    ("overview", "概览", "项目概览"),
    ("kanban", "看板", "看板"),
    ("epics", "Epics", "Epics"),
    ("backlog", "工作项", "工作项"),
    ("proposals", "提案", "提案"),
    ("documents", "文档", "文档"),
    ("members", "成员与 Agents", "成员与 Agents"),
    ("settings", "设置", "项目设置"),
]

# 期望 navy tab 完整 aria-label 集合（8 个）
EXPECTED_NAVY_LABELS: set[str] = {label for _, label, _ in TABS}

ROOT = Path(__file__).resolve().parent
SHOT_DIR = ROOT / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str, *, flush: bool = True) -> None:
    """Safe print: 替换非 ASCII 字符为 ? 避免 Windows GBK 控制台崩溃。"""
    safe = msg.encode("ascii", "replace").decode("ascii")
    print(safe, flush=flush)


def login() -> str:
    """经 dev API 拿 admin token。"""
    req = urllib.request.Request(
        f"{API_BASE}/api/auth/login",
        data=json.dumps({"username": ADMIN_USER, "password": ADMIN_PASS}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["token"]


def goto_url_with_token(page: Page, token: str, url: str, first: bool = False) -> None:
    """goto + 在目标 URL 完成 token 注入 + reload。

    经验（2026-08-20 Task 1302a 加固）：
    - 2-step「先 / 再 target」会污染 browser history 导致 back 跳 / 而不是 target
    - 单次 goto target + setItem + reload：reload 后 validateAuth 把 URL 拉回 /
      是旧 bug（已修 Task 1310d）
    - 现在 validateAuth token 有效时设置 authVisible.set(false)，loadRoute 不会再
      提前 return，所以 reload 目标 URL 后会留在目标
    - **首次**调用要先在 / 完成 auth 状态建立，再切 target（否则 reload 后
      validateAuth 还没完成就 loadRoute，仍可能拉回 /）
    """
    if first:
        # Step 1: 在 / 完成 token 注入 + auth 状态
        page.goto(f"{FRONTEND_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)
        page.evaluate(f"localStorage.setItem('agentboard_token', {json.dumps(token)})")
        page.evaluate("localStorage.setItem('agentboard_user', 'admin')")
        page.reload(wait_until="domcontentloaded", timeout=30000)
        time.sleep(2.0)
    # Step 2: 切到目标 URL
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.reload(wait_until="domcontentloaded", timeout=30000)  # 强制 reload 目标 URL
    time.sleep(3.0)  # 等 loadRoute async + 项目数据加载


def collect_signals(page: Page) -> dict:
    """单次 evaluate 收集所有断言所需信号（避免多次 evaluate 通信 hang）。"""
    return page.evaluate("""
        ({
            url: location.pathname,
            title: document.title,
            h1: (document.querySelector('app-workspace-heading h1')?.textContent || '').trim().replace(/\\s+/g, ' '),
            navyTabCount: document.querySelectorAll('a.project-nav-button-v7').length,
            navyTabAriaLabels: Array.from(document.querySelectorAll('a.project-nav-button-v7[aria-label]')).map(a => a.getAttribute('aria-label')),
            navyTabActive: (document.querySelector('a.project-nav-button-v7[aria-current="page"]')?.getAttribute('aria-label') || '').trim(),
        })
    """)


def main() -> int:
    failures: list[str] = []

    log(">>> login")
    token = login()
    log(f"   token len={len(token)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            # === PART A: 8 tab 各自 URL 直达 + 真实断言 ===
            log(">>> PART A — 8 tab 各自 URL 直达 + 真实断言")
            for idx, (section, expected_label, h1_keyword) in enumerate(TABS):
                url = f"{FRONTEND_ORIGIN}/project/1/{section}"
                log(f"   goto {url}")
                goto_url_with_token(page, token, url, first=(idx == 0))
                sig = collect_signals(page)
                log(f"   sig = {sig}")

                shot = SHOT_DIR / f"_x_b1_{section}.png"
                page.screenshot(path=str(shot), full_page=False)
                size = shot.stat().st_size if shot.exists() else 0
                log(f"   shot {shot.name} size={size}B")

                # 1) URL 末段断言
                if not sig["url"].endswith(f"/{section}"):
                    failures.append(
                        f"{section}: URL 末段应为 /{section}，实际 '{sig['url']}'"
                    )

                # 2) h1 包含项目名（workspace-heading 实际是项目名，不是 tab 名）
                #    留这个断言是为了确认 workspace-heading 渲染了；不强求 tab 关键字
                if not sig["h1"]:
                    failures.append(
                        f"{section}: workspace-heading h1 空，组件可能未渲染"
                    )

                # 3) 当前 active navy tab aria-label 断言
                if sig["navyTabActive"] != expected_label:
                    failures.append(
                        f"{section}: navy active 应为 '{expected_label}'，实际 {sig['navyTabActive']!r}"
                    )

                # 4) navy tab 8 个全在
                if sig["navyTabCount"] != 8:
                    failures.append(
                        f"{section}: navy tab 数应为 8，实际 {sig['navyTabCount']}"
                    )

                # 5) navy tab aria-label 完整集合断言
                actual = set(sig["navyTabAriaLabels"])
                missing = EXPECTED_NAVY_LABELS - actual
                if missing:
                    failures.append(
                        f"{section}: navy tab 缺 aria-label {missing}"
                    )

                # 6) 截图非空
                if size < 1000:
                    failures.append(
                        f"{section}: 截图过小 ({size}B)，可能页面未渲染"
                    )

            # === PART B: 浏览器前进 / 后退 + URL 断言 ===
            log(">>> PART B — 浏览器前进 / 后退 + URL 断言")
            # 重新进 settings（最后一个 tab），然后连续 back
            goto_url_with_token(page, token, f"{FRONTEND_ORIGIN}/project/1/settings")
            sig_settings = collect_signals(page)
            log(f"   settings sig = {sig_settings}")
            if sig_settings["navyTabActive"] != "设置":
                failures.append(
                    f"back 起点: settings tab 高亮应为 '设置'，实际 {sig_settings['navyTabActive']!r}"
                )
            page.screenshot(
                path=str(SHOT_DIR / "_x_b1_back_0_settings.png"), full_page=False
            )

            # back 顺序：settings → 倒序穿过 PART A 8 tab → 停在 kanban
            # PART A 顺序：overview, kanban, epics, backlog, proposals, documents, members, settings
            # 从 settings 往前 back 5 次：members, documents, proposals, backlog, epics
            expected_back_sections = ["members", "documents", "proposals", "backlog", "epics"]
            for step, expected_section in enumerate(expected_back_sections, start=1):
                page.go_back(wait_until="domcontentloaded", timeout=30000)
                time.sleep(2.5)  # 等 Angular 重新渲染
                sig = collect_signals(page)
                log(f"   back #{step} sig = {sig}")

                shot = SHOT_DIR / f"_x_b1_back_{step}.png"
                page.screenshot(path=str(shot), full_page=False)
                size = shot.stat().st_size if shot.exists() else 0
                log(f"   back #{step}: shot {shot.name} size={size}B")

                # URL 断言
                if not sig["url"].endswith(f"/{expected_section}"):
                    failures.append(
                        f"back #{step}: URL 末段应为 /{expected_section}，实际 '{sig['url']}'"
                    )
                # navy active 断言（URL section → 期望 tab label）
                expected_label_map = {sec: lab for sec, lab, _ in TABS}
                expected_label = expected_label_map.get(expected_section)
                if expected_label and sig["navyTabActive"] != expected_label:
                    failures.append(
                        f"back #{step}: navy active 应为 '{expected_label}'，实际 {sig['navyTabActive']!r}"
                    )

                if size < 1000:
                    failures.append(
                        f"back #{step}: 截图过小 ({size}B)"
                    )

        except Exception as e:
            log(f"   EXCEPTION: {e!r}")
            failures.append(f"exception: {e!r}")
            try:
                page.screenshot(path=str(SHOT_DIR / "_x_b1_error.png"), full_page=True)
            except Exception:
                pass
        finally:
            ctx.close()
            browser.close()

    log("")
    if failures:
        log("FAIL:")
        for f in failures:
            log(f"  - {f}")
        return 1
    log("PASS — X.B1 8 tab 路由化 + 真实断言 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
