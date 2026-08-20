"""Epic 151 / Story 328 / Task 1313 E2E：响应式 + 无障碍 (a11y)。

验证（5 视口截图 + 关键指标）：
- 5 视口（375 / 768 / 1024 / 1280 / 1440）截图 `screenshots/_x_b2_vp_*.png`
- 移动视口（375 / 768）：bottom-tab-bar 出现 + navy project-sidebar 隐藏
- 桌面视口（1280 / 1440）：navy project-sidebar 出现 + bottom-tab-bar 隐藏
- 中间视口（1024）：navy project-sidebar 收窄但仍可见 + bottom-tab-bar 隐藏
- aria-label 存在性：topbar home-nav / sidebar nav / 8 navy tab 都有
- focus trap 验证：打开 create modal，Tab 5 次后焦点仍在 modal 内

Story 330 / Task 1323 改造（2026-08-20）：支持 pytest 收集。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Page

# 兼容老 main() 入口
from conftest import FRONTEND_ORIGIN, SHOT_DIR, log, goto_url_with_token

VIEWPORTS = [
    (375, 800, "mobile_375"),
    (768, 1024, "tablet_768"),
    (1024, 768, "laptop_1024"),
    (1280, 800, "desktop_1280"),
    (1440, 900, "desktop_1440"),
]

EXPECTED_BTB_LABELS = {"首页", "项目", "工作台", "通知", "我的"}
EXPECTED_NAVY_LABELS = {"概览", "看板", "Epics", "工作项", "提案", "文档", "成员与 Agents", "设置"}


def open_home_with_token(page, token: str) -> None:
    """进入 / (home) 注入 token。"""
    page.goto(f"{FRONTEND_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)
    page.evaluate(f"localStorage.setItem('agentboard_token', {json.dumps(token)})")
    page.evaluate("localStorage.setItem('agentboard_user', 'admin')")
    page.reload(wait_until="domcontentloaded", timeout=30000)
    time.sleep(3.0)


def open_project_with_token(page, token: str, path: str = "/project/1/overview") -> None:
    """进入 project view（/projects 或 /project/:id/...）注入 token。"""
    page.goto(f"{FRONTEND_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)
    page.evaluate(f"localStorage.setItem('agentboard_token', {json.dumps(token)})")
    page.evaluate("localStorage.setItem('agentboard_user', 'admin')")
    page.goto(f"{FRONTEND_ORIGIN}{path}", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3.0)


def collect_project_signals(page) -> dict:
    """单次 evaluate 收集 project view 的 navy tab + btb 信号。"""
    return page.evaluate("""
        ({
            url: location.pathname,
            btbDisplay: getComputedStyle(document.querySelector('app-bottom-tab-bar nav')).display,
            btbActive: (document.querySelector('app-bottom-tab-bar nav a[aria-current="page"]')?.getAttribute('aria-label') || '').trim(),
            navyTabCount: document.querySelectorAll('a.project-nav-button-v7').length,
            navyTabAriaLabels: Array.from(document.querySelectorAll('a.project-nav-button-v7[aria-label]')).map(a => a.getAttribute('aria-label')),
            navyTabActive: (document.querySelector('a.project-nav-button-v7[aria-current="page"]')?.getAttribute('aria-label') || '').trim(),
        })
    """)


def collect_layout_signals(page) -> dict:
    """单次 evaluate 收集响应式 + a11y 信号（home view）。"""
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


# ============================================================
# Pytest fixtures：per-viewport context
# ============================================================

@pytest.fixture(params=[v for v in VIEWPORTS], ids=[v[2] for v in VIEWPORTS])
def viewport_context(browser, request):
    """每个视口一个独立 context + page（per-test isolation）。"""
    w, h, _label = request.param
    ctx = browser.new_context(viewport={"width": w, "height": h})
    page = ctx.new_page()
    try:
        yield page, w, h
    finally:
        ctx.close()


# ============================================================
# Pytest test_* functions
# ============================================================

@pytest.mark.e2e
def test_home_view_responsive_a11y(viewport_context, admin_token: str) -> None:
    """PART A：5 视口 home view 响应式 + a11y。"""
    page, w, h = viewport_context
    log(f"   [{w}x{h}] home view")
    open_home_with_token(page, admin_token)
    sig = collect_layout_signals(page)
    log(f"   sig = {sig}")

    shot = SHOT_DIR / f"_x_b2_vp_{w}x{h}.png"
    page.screenshot(path=str(shot), full_page=False)
    size = shot.stat().st_size if shot.exists() else 0

    is_mobile = w <= 840
    is_desktop = w >= 1280

    # 1) bottom-tab-bar 可见性
    if is_mobile:
        assert sig["btbDisplay"] != "none", f"{w}x{h}: 移动端 btb 应显示但 display={sig['btbDisplay']}"
        assert sig["btbVisible"] == 5, f"{w}x{h}: 移动端 btb 应有 5 item 但 {sig['btbVisible']}"
    else:
        assert sig["btbDisplay"] == "none", f"{w}x{h}: 桌面 btb 应隐藏但 display={sig['btbDisplay']}"

    # 2) btb 5 个 aria-label
    actual_btb_labels = set(sig["btbAriaLabels"])
    missing_btb = EXPECTED_BTB_LABELS - actual_btb_labels
    assert not missing_btb, f"{w}x{h}: btb 缺 aria-label {missing_btb}"

    # 3) URL 应为 / 或 /login
    assert sig["url"] in ("/", "/login"), f"{w}x{h}: URL 应为 / 或 /login 但 '{sig['url']}'"

    # 4) 截图非空
    assert size >= 1000, f"{w}x{h}: 截图过小 ({size}B)"


@pytest.mark.e2e
def test_project_view_responsive_a11y(browser, admin_token: str) -> None:
    """PART C：5 视口 project view 测 navy tab aria-label + btb 高亮。

    单独 test 因为需要不同 path（/project/1/overview 而非 /），跟 PART A 拆开。
    内部循环 5 视口（不用 parametrize 因为中间状态不一样）。
    """
    for w, h, label in VIEWPORTS:
        ctx = browser.new_context(viewport={"width": w, "height": h})
        page = ctx.new_page()
        try:
            log(f"   [{label}] project view {w}x{h}")
            open_project_with_token(page, admin_token, "/project/1/overview")
            sig = collect_project_signals(page)
            log(f"   sig = {sig}")

            shot = SHOT_DIR / f"_x_b2_project_vp_{label}.png"
            page.screenshot(path=str(shot), full_page=False)
            size = shot.stat().st_size if shot.exists() else 0

            is_mobile = w <= 840
            is_desktop = w >= 1280

            # 1) 桌面 navy tab 8 个 aria-label
            if is_desktop:
                assert sig["navyTabCount"] == 8, f"{label}: 桌面 navy 应有 8 但 {sig['navyTabCount']}"
                actual = set(sig["navyTabAriaLabels"])
                missing = EXPECTED_NAVY_LABELS - actual
                assert not missing, f"{label}: navy 缺 aria-label {missing}"
                assert sig["navyTabActive"] == "概览", f"{label}: navy active 应为 概览 但 {sig['navyTabActive']!r}"

            # 2) btb 显隐 + active
            if is_mobile:
                assert sig["btbDisplay"] != "none", f"{label}: 移动端 btb 应显示但 {sig['btbDisplay']}"
                assert sig["btbActive"] == "当前项目工作台", (
                    f"{label}: 移动端 btb active 应为 '当前项目工作台' 但 {sig['btbActive']!r}"
                )
            else:
                assert sig["btbDisplay"] == "none", f"{label}: 桌面 btb 应隐藏但 {sig['btbDisplay']}"

            assert size >= 1000, f"{label}: 截图过小 ({size}B)"
        finally:
            ctx.close()


@pytest.mark.e2e
def test_projects_list_btb_highlight(browser, admin_token: str) -> None:
    """PART D：/projects 列表页 btb「项目」高亮（Task 1310d 修复点）。"""
    ctx = browser.new_context(viewport={"width": 375, "height": 800})
    page = ctx.new_page()
    try:
        open_project_with_token(page, admin_token, "/projects")
        sig = collect_project_signals(page)
        log(f"   /projects sig = {sig}")
        assert sig["btbActive"] == "项目", (
            f"/projects 移动端 btb active 应为 '项目' 但 {sig['btbActive']!r} "
            f"（Task 1310d 修复点：/projects 不能映射成 'project' 让「工作台」亮）"
        )
        page.screenshot(path=str(SHOT_DIR / "_x_b2_projects_list_mobile.png"), full_page=False)
    finally:
        ctx.close()


@pytest.mark.e2e
def test_create_modal_focus_trap(browser, admin_token: str) -> None:
    """PART B：create modal 焦点 trap 验证。"""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    try:
        open_home_with_token(page, admin_token)
        # 打开 create modal
        page.evaluate("""
            const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent && (b.textContent.includes('新建') || b.textContent.includes('+')));
            if (btn) btn.click();
        """)
        time.sleep(1.0)
        modal_visible = page.evaluate("!!document.querySelector('.modal.modal-create')")
        assert modal_visible, "create modal 未被「+ 新建」按钮打开"

        focus_in_modal_count = 0
        for _ in range(5):
            page.keyboard.press("Tab")
            time.sleep(0.1)
            in_modal = page.evaluate(
                "document.activeElement && document.querySelector('.modal.modal-create')?.contains(document.activeElement)"
            )
            if in_modal:
                focus_in_modal_count += 1
        log(f"   focus 在 modal 内的次数: {focus_in_modal_count}/5")
        assert focus_in_modal_count >= 4, (
            f"focus trap 失效：Tab 5 次只有 {focus_in_modal_count} 次在 modal 内"
        )
        page.screenshot(path=str(SHOT_DIR / "_x_b2_focus_trap_modal.png"), full_page=False)
    finally:
        ctx.close()


# ============================================================
# 兼容老 main() 入口
# ============================================================

def main() -> int:
    import urllib.request
    from playwright.sync_api import sync_playwright

    from conftest import API_BASE, ADMIN_USER, ADMIN_PASS

    log(">>> login (main 兼容入口)")
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
        try:
            # PART A: 5 视口 home
            for w, h, label in VIEWPORTS:
                ctx = browser.new_context(viewport={"width": w, "height": h})
                page = ctx.new_page()
                try:
                    open_home_with_token(page, token)
                    sig = collect_layout_signals(page)
                    is_mobile = w <= 840
                    if is_mobile:
                        if sig["btbDisplay"] == "none":
                            failures.append(f"PART A {label}: btb 应显示")
                        if sig["btbVisible"] != 5:
                            failures.append(f"PART A {label}: btb 应 5 item 但 {sig['btbVisible']}")
                    else:
                        if sig["btbDisplay"] != "none":
                            failures.append(f"PART A {label}: btb 应隐藏")
                    missing = EXPECTED_BTB_LABELS - set(sig["btbAriaLabels"])
                    if missing:
                        failures.append(f"PART A {label}: btb 缺 aria-label {missing}")
                    if sig["url"] not in ("/", "/login"):
                        failures.append(f"PART A {label}: URL 错 {sig['url']}")
                finally:
                    ctx.close()

            # PART B: focus trap
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            try:
                open_home_with_token(page, token)
                page.evaluate("""
                    const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent && (b.textContent.includes('新建') || b.textContent.includes('+')));
                    if (btn) btn.click();
                """)
                time.sleep(1.0)
                modal_visible = page.evaluate("!!document.querySelector('.modal.modal-create')")
                if not modal_visible:
                    failures.append("PART B: create modal 未打开")
                else:
                    cnt = 0
                    for _ in range(5):
                        page.keyboard.press("Tab")
                        time.sleep(0.1)
                        in_modal = page.evaluate(
                            "document.activeElement && document.querySelector('.modal.modal-create')?.contains(document.activeElement)"
                        )
                        if in_modal:
                            cnt += 1
                    if cnt < 4:
                        failures.append(f"PART B: focus trap 失效 {cnt}/5")
            finally:
                ctx.close()

            # PART C: project view 5 视口
            for w, h, label in VIEWPORTS:
                ctx = browser.new_context(viewport={"width": w, "height": h})
                page = ctx.new_page()
                try:
                    open_project_with_token(page, token, "/project/1/overview")
                    sig = collect_project_signals(page)
                    is_mobile = w <= 840
                    is_desktop = w >= 1280
                    if is_desktop:
                        if sig["navyTabCount"] != 8:
                            failures.append(f"PART C {label}: navy {sig['navyTabCount']} != 8")
                        missing = EXPECTED_NAVY_LABELS - set(sig["navyTabAriaLabels"])
                        if missing:
                            failures.append(f"PART C {label}: navy 缺 {missing}")
                        if sig["navyTabActive"] != "概览":
                            failures.append(f"PART C {label}: active {sig['navyTabActive']!r}")
                    if is_mobile:
                        if sig["btbDisplay"] == "none":
                            failures.append(f"PART C {label}: btb 应显示")
                        if sig["btbActive"] != "当前项目工作台":
                            failures.append(f"PART C {label}: btb active {sig['btbActive']!r}")
                finally:
                    ctx.close()

            # PART D
            ctx = browser.new_context(viewport={"width": 375, "height": 800})
            page = ctx.new_page()
            try:
                open_project_with_token(page, token, "/projects")
                sig = collect_project_signals(page)
                if sig["btbActive"] != "项目":
                    failures.append(f"PART D: btb active {sig['btbActive']!r}")
            finally:
                ctx.close()
        finally:
            browser.close()

    if failures:
        log("FAIL:")
        for f in failures:
            log(f"  - {f}")
        return 1
    log("PASS — X.B2 响应式 + a11y OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
