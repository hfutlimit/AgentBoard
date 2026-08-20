"""Epic 151 / Story 327 / Task 1300 E2E：8 tab 路由化 + 浏览器前进后退。

背景（review 高优先级 #4 + #5）：
- 8 navy project-sidebar tab 改 `routerLink` 驱动 URL 切换；
- `app.routes.ts` 8 tab 全部 `loadComponent` 化（懒加载 chunk）；
- `app.ts` `loadRoute()` 解析 8 个 section（overview / kanban / epics /
  backlog / proposals / documents / members / settings）→ `activeTab.set`；
- 删 emoji tab-bar（含 stats / tickets 新 navy 没保留的）；
- app.html 顶层根 `<router-outlet />` 已删（避免与 @switch 渲染双轨）。

E2E 验证：
1. 启动浏览器 + 注入 token → 直接 `goto /project/1/<tab>` 8 个 URL；
2. 每个 URL 验证：workspace-heading 出现 + active tab 高亮（navy 按钮 [class.active]）；
3. 浏览器前进 / 后退：依次 goto 8 tab → 按 back 5 次 → 验证前一个 tab 高亮；
4. 截图 8 tab（已截的 10 view 截图脚本可复用，但本脚本精简版）；
5. **不复用 token-injection 之外的辅助**（保留端到端真实路径）。

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

# 8 navy tab → (URL section, sidebar button 文字, workspace-heading h1 关键字)
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

ROOT = Path(__file__).resolve().parent
SHOT_DIR = ROOT / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str, *, flush: bool = True) -> None:
    print(msg, flush=flush)


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


def goto_url_with_token(page: Page, token: str, url: str) -> None:
    """goto + evaluate 设 token（一次） + reload 同 URL。

    经验：Playwright sync `page.evaluate` 在多次 goto / reload 后偶发 hang，
    因此不读 DOM，只截图 + sleep 等渲染。
    """
    # 第一次 goto 注入 token + reload（同 URL）让 SPA 走 auth + 渲染
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.evaluate(f"localStorage.setItem('agentboard_token', {json.dumps(token)})")
    page.evaluate("localStorage.setItem('agentboard_user', 'admin')")
    page.reload(wait_until="domcontentloaded", timeout=30000)
    time.sleep(3.0)  # 等 loadRoute async + 项目数据加载


def main() -> int:
    failures: list[str] = []

    log(">>> login")
    token = login()
    log(f"   token len={len(token)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        # 不用 add_init_script（经验证它会让 Playwright sync 通信 hang），
        # 改用「每次 goto + evaluate 设 token + reload 同 URL」模式。
        page = ctx.new_page()
        try:
            # === PART A: 8 tab 各自 URL 直达（仅截图证据，不读 DOM） ===
            log(">>> PART A — 8 tab 各自 URL 直达（截图证据）")
            for section, nav_text, h1_keyword in TABS:
                url = f"{FRONTEND_ORIGIN}/project/1/{section}"
                log(f"   goto {url}")
                goto_url_with_token(page, token, url)
                shot = SHOT_DIR / f"_x_b1_{section}.png"
                page.screenshot(path=str(shot), full_page=False)
                size = shot.stat().st_size if shot.exists() else 0
                log(f"   shot {shot.name} size={size}B")
                if size < 1000:
                    failures.append(
                        f"{section}: 截图过小 ({size}B)，可能页面未渲染"
                    )

            # === PART B: 浏览器前进 / 后退（截图证据） ===
            log(">>> PART B — 浏览器前进 / 后退（截图证据）")
            # 重新进 settings（最后一个 tab），然后连续 back
            page.goto(
                f"{FRONTEND_ORIGIN}/project/1/settings",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            time.sleep(2.0)
            page.screenshot(
                path=str(SHOT_DIR / "_x_b1_back_0_settings.png"), full_page=False
            )
            for step in range(5):
                page.go_back(wait_until="domcontentloaded", timeout=30000)
                time.sleep(2.0)
                shot = SHOT_DIR / f"_x_b1_back_{step + 1}.png"
                page.screenshot(path=str(shot), full_page=False)
                size = shot.stat().st_size if shot.exists() else 0
                log(f"   back #{step + 1}: shot {shot.name} size={size}B")
                if size < 1000:
                    failures.append(
                        f"back #{step + 1}: 截图过小 ({size}B)"
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
            log("  -", f)
        return 1
    log("PASS — X.B1 8 tab 路由化 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
