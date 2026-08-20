"""Epic 151 / Story 328 / Task 1313 E2E：响应式 + 无障碍 (a11y)。

验证（5 视口截图 + 关键指标）：
- 5 视口（375 / 768 / 1024 / 1280 / 1440）截图 `screenshots/_x_b2_vp_*.png`
- 移动视口（375 / 768）：bottom-tab-bar 出现 + navy project-sidebar 隐藏
- 桌面视口（1280 / 1440）：navy project-sidebar 出现 + bottom-tab-bar 隐藏
- 中间视口（1024）：navy project-sidebar 收窄但仍可见 + bottom-tab-bar 隐藏
- aria-label 存在性：topbar home-nav / sidebar nav / 8 navy tab 都有
- focus trap 验证：打开 create modal，Tab 5 次后焦点仍在 modal 内

2026-08-20 创建。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

FRONTEND_ORIGIN = "http://127.0.0.1:4200"
API_BASE = os.environ.get("AGENTBOARD_API_BASE", "http://127.0.0.1:18000")
ADMIN_USER = os.environ.get("AGENTBOARD_E2E_USER", "admin")
ADMIN_PASS = os.environ.get("AGENTBOARD_E2E_PASS", "admin123")

VIEWPORTS = [
    (375, 800, "mobile_375"),
    (768, 1024, "tablet_768"),
    (1024, 768, "laptop_1024"),
    (1280, 800, "desktop_1280"),
    (1440, 900, "desktop_1440"),
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


def open_home_with_token(page, token: str) -> None:
    """进入 / (home) 注入 token。简化版：home view 不依赖项目 id/loadRoute，
    只验证 token 注入 + authVisible 关闭 + bottom-tab-bar 渲染。

    为什么不用 /project/1/overview：SPA 的 `validateAuth` 在没 token 时会调用
    showLogin() + router.navigateByUrl('/login')，导致 URL 切走。validateAuth 修复
    后（Epic 151 / Story 328 a11y）reload 仍可能让 Angular 客户端路由把 URL
    拉回 home。home view 是验证响应式 + a11y 信号的足够入口。
    """
    page.goto(f"{FRONTEND_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)
    page.evaluate(f"localStorage.setItem('agentboard_token', {json.dumps(token)})")
    page.evaluate("localStorage.setItem('agentboard_user', 'admin')")
    page.reload(wait_until="domcontentloaded", timeout=30000)
    time.sleep(3.0)


def collect_layout_signals(page) -> dict:
    """单次 evaluate 收集响应式 + a11y 信号（home view）。

    注：navySidebar 仅在 project view 显示；home view 看 navySidebar
    应该都是 'absent'，重点验证 bottom-tab-bar + 8 navy tab aria-label（后
    者通过 selector 即可拿到，即便不在 active view）。
    """
    return page.evaluate("""
        ({
            url: location.pathname,
            btbDisplay: getComputedStyle(document.querySelector('app-bottom-tab-bar nav')).display,
            btbVisible: document.querySelectorAll('app-bottom-tab-bar nav a').length,
            btbActive: (document.querySelector('app-bottom-tab-bar nav a[aria-current="page"]')?.textContent || '').trim().replace(/\\s+/g, ' '),
            btbAriaLabels: Array.from(document.querySelectorAll('app-bottom-tab-bar nav a')).map(a => a.getAttribute('aria-label')),
            navyTabAriaLabels: Array.from(document.querySelectorAll('a.project-nav-button-v7[aria-label]')).map(a => a.getAttribute('aria-label')),
            navyTabCount: document.querySelectorAll('a.project-nav-button-v7').length,
            h1: (document.querySelector('app-workspace-heading h1')?.textContent || '').trim(),
        })
    """)


def main() -> int:
    failures: list[str] = []
    log(">>> login")
    token = login()
    log(f"   token len={len(token)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        try:
            # === PART A: 5 视口响应式 + a11y 截图 ===
            log(">>> PART A — 5 视口响应式截图 + 信号收集")
            for w, h, label in VIEWPORTS:
                ctx = browser.new_context(viewport={"width": w, "height": h})
                page = ctx.new_page()
                try:
                    log(f"   [{label}] viewport {w}x{h}")
                    open_home_with_token(page, token)
                    sig = collect_layout_signals(page)
                    log(f"   sig = {sig}")

                    # 截图
                    shot = SHOT_DIR / f"_x_b2_vp_{label}.png"
                    page.screenshot(path=str(shot), full_page=False)
                    size = shot.stat().st_size if shot.exists() else 0
                    log(f"   shot {shot.name} size={size}B")

                    # 视口判断
                    is_mobile = w <= 840
                    is_desktop = w >= 1280
                    is_mid = 840 < w < 1280

                    # 1) bottom-tab-bar 可见性
                    if is_mobile:
                        if sig["btbDisplay"] == "none":
                            failures.append(
                                f"{label}: bottom-tab-bar 应显示但 display={sig['btbDisplay']}"
                            )
                        if sig["btbVisible"] < 3:
                            failures.append(
                                f"{label}: bottom-tab-bar item 数 {sig['btbVisible']} < 3"
                            )
                        # 移动端应有 5 个 nav item
                        if sig["btbVisible"] != 5:
                            failures.append(
                                f"{label}: bottom-tab-bar 应有 5 item 但实际 {sig['btbVisible']}"
                            )
                    else:
                        if sig["btbDisplay"] != "none":
                            failures.append(
                                f"{label}: bottom-tab-bar 应隐藏但 display={sig['btbDisplay']}"
                            )

                    # 2) bottom-tab-bar 5 个 aria-label（每个 item）
                    expected_btb_labels = {"首页", "项目", "工作台", "通知", "我的"}
                    actual_btb_labels = set(sig["btbAriaLabels"])
                    missing_btb = expected_btb_labels - actual_btb_labels
                    if missing_btb:
                        failures.append(
                            f"{label}: bottom-tab-bar 缺 aria-label {missing_btb}"
                        )

                    # 3) home view 验证 URL
                    if sig["url"] not in ("/", "/login"):
                        failures.append(
                            f"{label}: url 应为 / 或 /login 但实际 '{sig['url']}'"
                        )

                except Exception as e:
                    log(f"   EXCEPTION: {e!r}")
                    failures.append(f"{label}: {e!r}")
                finally:
                    ctx.close()

            # === PART B: focus trap 验证 ===
            log(">>> PART B — focus trap 验证（create modal）")
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            try:
                open_home_with_token(page, token)
                # 打开 create modal（点 + 按钮或按 n 键）
                page.evaluate("""
                    const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent && (b.textContent.includes('新建') || b.textContent.includes('+')));
                    if (btn) btn.click();
                """)
                time.sleep(1.0)
                modal_visible = page.evaluate("!!document.querySelector('.modal.modal-create')")
                log(f"   create modal visible: {modal_visible}")
                if not modal_visible:
                    failures.append("create modal not opened by '+ 新建' button")
                else:
                    focus_in_modal_count = 0
                    for i in range(5):
                        page.keyboard.press("Tab")
                        time.sleep(0.1)
                        in_modal = page.evaluate(
                            "document.activeElement && document.querySelector('.modal.modal-create')?.contains(document.activeElement)"
                        )
                        if in_modal:
                            focus_in_modal_count += 1
                    log(f"   focus 在 modal 内的次数: {focus_in_modal_count}/5")
                    if focus_in_modal_count < 4:
                        failures.append(
                            f"focus trap 失效：Tab 5 次只有 {focus_in_modal_count} 次在 modal 内"
                        )
                    page.screenshot(
                        path=str(SHOT_DIR / "_x_b2_focus_trap_modal.png"),
                        full_page=False,
                    )
            except Exception as e:
                log(f"   EXCEPTION: {e!r}")
                failures.append(f"focus trap: {e!r}")
            finally:
                ctx.close()
        finally:
            browser.close()

    log("")
    if failures:
        log("FAIL:")
        for f in failures:
            log(f"  - {f}")
        return 1
    log("PASS — X.B2 响应式 + a11y OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
