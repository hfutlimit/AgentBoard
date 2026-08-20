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
   - workspace-heading ``<h1>`` 非空
   - 当前 navy tab 按钮 ``[aria-current="page"]`` 落在预期位置
   - 8 navy tab aria-label 全部存在
3. 浏览器前进 / 后退：依次 goto 8 tab → 按 back 5 次 → 验证前一个 tab 高亮（URL 断言）
4. 截图 8 tab + back 5 步（视觉证据）

Story 330 / Task 1323 改造（2026-08-20）：支持 pytest 收集 (`def test_xxx()`) +
保留 `__main__` 入口（手动 `python tests/.../test_x_b1_route_8tab.py` 仍能跑）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
try:
    from playwright.sync_api import Page
except ModuleNotFoundError:  # pragma: no cover - collected without E2E extras
    Page = object

# 兼容老 main() 入口：常量从 conftest 拉
from conftest import (
    FRONTEND_ORIGIN,
    SHOT_DIR,
    goto_url_with_token,
    log,
)

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

EXPECTED_NAVY_LABELS: set[str] = {label for _, label, _ in TABS}


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


# ============================================================
# Pytest test_* functions (Task 1323)
# ============================================================

@pytest.mark.e2e
def test_8tab_url_and_browser_back(page: Page, admin_token: str) -> None:
    """8 tab URL 直达 + 浏览器前进/后退 + 真实断言（单 test 内完成）。

    合并原因：pytest 的 `page` fixture 是 function scope，back 5 步需要
    同一 page context 的 history。两个 test 会拆成独立 page 上下文，
    back 5 步会回到初始 / 而不是上一个 tab URL（实测 back #1: URL=/）。

    顺序：
    - PART A：依次 goto 8 tab（每次 reload 强制到 target URL，补充 history）
    - PART B：再 goto settings（last tab），back 5 步回到 epics
    """
    # PART A
    log(">>> PART A — 8 tab URL 直达 + 真实断言")
    for idx, (section, expected_label, _h1_keyword) in enumerate(TABS):
        url = f"{FRONTEND_ORIGIN}/project/1/{section}"
        log(f"   goto {url}")
        goto_url_with_token(page, admin_token, url, first=(idx == 0))
        sig = collect_signals(page)
        log(f"   sig = {sig}")

        shot = SHOT_DIR / f"_x_b1_{section}.png"
        page.screenshot(path=str(shot), full_page=False)
        size = shot.stat().st_size if shot.exists() else 0
        log(f"   shot {shot.name} size={size}B")

        # URL 末段断言
        assert sig["url"].endswith(f"/{section}"), (
            f"{section}: URL 末段应为 /{section}，实际 '{sig['url']}'"
        )
        # h1 非空
        assert sig["h1"], f"{section}: workspace-heading h1 空，组件可能未渲染"
        # 当前 active navy tab aria-label
        assert sig["navyTabActive"] == expected_label, (
            f"{section}: navy active 应为 '{expected_label}'，实际 {sig['navyTabActive']!r}"
        )
        # 8 navy tab
        assert sig["navyTabCount"] == 8, (
            f"{section}: navy tab 数应为 8，实际 {sig['navyTabCount']}"
        )
        # aria-label 完整集合
        actual = set(sig["navyTabAriaLabels"])
        missing = EXPECTED_NAVY_LABELS - actual
        assert not missing, f"{section}: navy tab 缺 aria-label {missing}"
        # 截图非空
        assert size >= 1000, f"{section}: 截图过小 ({size}B)"

    # PART B
    log(">>> PART B — back 5 步 + URL + navy active 断言")
    goto_url_with_token(page, admin_token, f"{FRONTEND_ORIGIN}/project/1/settings")
    sig_settings = collect_signals(page)
    log(f"   settings sig = {sig_settings}")
    assert sig_settings["navyTabActive"] == "设置", (
        f"back 起点: settings tab 高亮应为 '设置'，实际 {sig_settings['navyTabActive']!r}"
    )
    page.screenshot(
        path=str(SHOT_DIR / "_x_b1_back_0_settings.png"), full_page=False
    )

    expected_back_sections = ["members", "documents", "proposals", "backlog", "epics"]
    expected_label_map = {sec: lab for sec, lab, _ in TABS}

    for step, expected_section in enumerate(expected_back_sections, start=1):
        page.go_back(wait_until="domcontentloaded", timeout=30000)
        time.sleep(2.5)
        sig = collect_signals(page)
        log(f"   back #{step} sig = {sig}")

        shot = SHOT_DIR / f"_x_b1_back_{step}.png"
        page.screenshot(path=str(shot), full_page=False)
        size = shot.stat().st_size if shot.exists() else 0
        log(f"   back #{step}: shot {shot.name} size={size}B")

        assert sig["url"].endswith(f"/{expected_section}"), (
            f"back #{step}: URL 末段应为 /{expected_section}，实际 '{sig['url']}'"
        )
        expected_label = expected_label_map.get(expected_section)
        if expected_label:
            assert sig["navyTabActive"] == expected_label, (
                f"back #{step}: navy active 应为 '{expected_label}'，实际 {sig['navyTabActive']!r}"
            )
        assert size >= 1000, f"back #{step}: 截图过小 ({size}B)"


# ============================================================
# 兼容老 main() 入口（手动 python tests/.../test_x_b1_route_8tab.py）
# ============================================================

def main() -> int:
    """Story 330 兼容层：手动跑用。pytest 自动收集 test_*_xxx()。

    流程：login → PART A 8 tab → PART B back 5 → 汇总 failures。
    """
    import urllib.request
    from playwright.sync_api import sync_playwright

    log(">>> login (main 兼容入口)")
    # 用 urllib 拿 token（避免依赖 pytest fixture）
    from conftest import API_BASE, ADMIN_USER, ADMIN_PASS
    req = urllib.request.Request(
        f"{API_BASE}/api/auth/login",
        data=json.dumps({"username": ADMIN_USER, "password": ADMIN_PASS}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        token = json.loads(r.read().decode())["token"]
    log(f"   token len={len(token)}")

    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            # PART A
            log(">>> PART A — 8 tab URL 直达 + 真实断言")
            for idx, (section, expected_label, _) in enumerate(TABS):
                url = f"{FRONTEND_ORIGIN}/project/1/{section}"
                goto_url_with_token(page, token, url, first=(idx == 0))
                sig = collect_signals(page)
                if not sig["url"].endswith(f"/{section}"):
                    failures.append(f"{section}: URL 末段错 '{sig['url']}'")
                if not sig["h1"]:
                    failures.append(f"{section}: h1 空")
                if sig["navyTabActive"] != expected_label:
                    failures.append(f"{section}: navy active 错 {sig['navyTabActive']!r}")
                if sig["navyTabCount"] != 8:
                    failures.append(f"{section}: navy tab 数 {sig['navyTabCount']}")
                missing = EXPECTED_NAVY_LABELS - set(sig["navyTabAriaLabels"])
                if missing:
                    failures.append(f"{section}: 缺 aria-label {missing}")
                page.screenshot(path=str(SHOT_DIR / f"_x_b1_{section}.png"), full_page=False)

            # PART B
            log(">>> PART B — back 5 步")
            goto_url_with_token(page, token, f"{FRONTEND_ORIGIN}/project/1/settings")
            expected_back_sections = ["members", "documents", "proposals", "backlog", "epics"]
            expected_label_map = {sec: lab for sec, lab, _ in TABS}
            for step, expected_section in enumerate(expected_back_sections, start=1):
                page.go_back(wait_until="domcontentloaded", timeout=30000)
                time.sleep(2.5)
                sig = collect_signals(page)
                if not sig["url"].endswith(f"/{expected_section}"):
                    failures.append(f"back #{step}: URL 末段错 '{sig['url']}'")
                expected_label = expected_label_map.get(expected_section)
                if expected_label and sig["navyTabActive"] != expected_label:
                    failures.append(f"back #{step}: navy active 错 {sig['navyTabActive']!r}")
                page.screenshot(path=str(SHOT_DIR / f"_x_b1_back_{step}.png"), full_page=False)
        finally:
            ctx.close()
            browser.close()

    if failures:
        log("FAIL:")
        for f in failures:
            log(f"  - {f}")
        return 1
    log("PASS — X.B1 8 tab 路由化 + 真实断言 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
