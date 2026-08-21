"""项目工作台 *-tab 内部点详情 link E2E（2026-08-21 Epic152 v3 - 4 修）

v3 - 4 修:不显示 side panel,改成**在新浏览器 tab 打开全页**。
之前 v3 Step 1 实现的 side panel 用户实测不要,改为 window.open 方式。
原 tab 上下文保持不变,workspace tab 条不丢,user 切回原 tab 即可。

E2E 真实断言:
1. test_click_epic_opens_new_tab
   从 epics tab 点 epic → 新 tab 跳 /epic/:id,原 tab URL 不变
2. test_no_side_panel_appears
   点 link 后 workspace main 不出现 side panel
3. test_workspace_url_unchanged_after_open
   点 link 后,原 workspace tab URL /workspace tab list 都不变
4. test_sidebar_menu_click_not_opens_new_tab
   左侧菜单点 kanban 不开新 tab(菜单是切换 tab,不是开新 tab)
"""
from __future__ import annotations

import time

import pytest
from playwright.sync_api import Browser, Page

from conftest import (
    FRONTEND_ORIGIN,
    SHOT_DIR,
    goto_url_with_token,
    log,
)

PROJECT_ID = 1

KIND_KANBAN = "kanban"
KIND_EPICS = "epics"


def _collect_signals(page: Page) -> dict:
    return page.evaluate("""
        ({
            url: location.pathname,
            tabStripCount: document.querySelectorAll('.tab-strip-item').length,
            activeTabKind: (document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) [data-tab-kind]')?.getAttribute('data-tab-kind') || null),
            detailPaneOpen: !!document.querySelector('.detail-pane'),
        })
    """)


def _shot(page: Page, name: str) -> None:
    p = SHOT_DIR / name
    page.screenshot(path=str(p), full_page=False)
    log(f"   shot {p.name} size={p.stat().st_size}B")


# ============================================================
# Pytest test_* functions
# ============================================================

@pytest.mark.e2e
def test_click_epic_opens_new_tab(browser: Browser, admin_token: str) -> None:
    """从 epics tab 点 epic → **新浏览器 tab 打开** /epic/:id。

    v3 - 4 修:不在原 tab 内 navigate (会触发 loadRoute 重拉数据、覆盖 workspace
    上下文),改成 window.open 在新 tab 打开全页路由,原 tab 不动。

    验证方式:用 add_init_script 在 page 加载前 hook window.open,捕获调用参数。
    这样不依赖 browser 弹窗策略(可能被 headless 模式拦)。
    """
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    # 在 context 创建时 hook window.open,所有 page 都能拿到
    ctx.add_init_script("""
        window.__openCalls = [];
        const origOpen = window.open;
        window.open = function(url, target, features) {
            window.__openCalls.push({ url, target, features });
            // 真正尝试打开(可能被弹窗策略拦,无所谓 — 我们只关心调用参数)
            return origOpen ? origOpen.call(window, url, target, features) : null;
        };
    """)
    page = ctx.new_page()
    try:
        url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/{KIND_EPICS}"
        goto_url_with_token(page, admin_token, url)
        time.sleep(1.5)

        sig_before = _collect_signals(page)
        log(f"   before click: {sig_before}")
        assert sig_before["url"].endswith(f"/{KIND_EPICS}")
        assert sig_before["tabStripCount"] == 1

        # 取到 epic id 用于断言
        epic_id = page.evaluate("""
            (() => {
                const a = document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]');
                if (!a) return null;
                const m = a.getAttribute('href').match(/^\\/epic\\/(\\d+)/);
                return m ? Number(m[1]) : null;
            })()
        """)
        if epic_id is None:
            pytest.skip("no epics seeded in dev DB for this project")

        # 点 epics tab 里的第一个 epic 链接 — 模拟正常左键点击
        page.evaluate("""
            (() => {
                const a = document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]');
                if (a) a.click();
            })()
        """)
        time.sleep(0.5)

        # 验证 window.open 被调用
        open_calls = page.evaluate("window.__openCalls || []")
        log(f"   window.open calls: {open_calls}")

        # 关键断言 1:window.open 被调用 1 次,目标 url 是 /epic/:id
        assert len(open_calls) == 1, f"window.open 应被调用 1 次, 实际 {len(open_calls)} 次"
        call = open_calls[0]
        assert call["target"] == "_blank", f"target 应是 _blank, 实际 {call['target']!r}"
        assert call["url"].endswith(f"/epic/{epic_id}"), (
            f"url 应是 /epic/{epic_id}, 实际 {call['url']!r}"
        )
        # 验证 noopener,noreferrer 防 tab-nabbing
        assert "noopener" in (call["features"] or ""), (
            f"features 应含 noopener, 实际 {call['features']!r}"
        )

        # 关键断言 2:原 tab URL 不变(没 navigate)
        sig_after = _collect_signals(page)
        log(f"   original tab after click: {sig_after}")
        _shot(page, "original_tab_after.png")
        assert sig_after["url"].endswith(f"/{KIND_EPICS}"), (
            f"原 tab URL 应保持 /{KIND_EPICS}, 实际 {sig_after['url']!r} — "
            f"v3 - 4 修:workspace 内点 link 不在原 tab 内 navigate"
        )
        # tab 列表不变
        assert sig_after["tabStripCount"] == 1, (
            f"原 tab tab 列表应保持 1, 实际 {sig_after['tabStripCount']}"
        )
    finally:
        ctx.close()


@pytest.mark.e2e
def test_no_side_panel_appears(admin_token: str, page: Page) -> None:
    """点 link 后 workspace main **不**出现 side panel（v3 - 4 修核心）。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/{KIND_EPICS}"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.5)

    has_link = page.evaluate("""
        !!document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]')
    """)
    if not has_link:
        pytest.skip("no epics seeded in dev DB for this project")

    sig_before = _collect_signals(page)
    assert sig_before["detailPaneOpen"] is False, "初始无 side panel"

    # 点 epic 链接
    page.evaluate("""
        (() => {
            const a = document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]');
            if (a) a.click();
        })()
    """)
    time.sleep(1.0)
    sig_after = _collect_signals(page)
    log(f"   after click: {sig_after}")
    _shot(page, "no_side_panel.png")

    # 关键断言:无 side panel 出现
    assert sig_after["detailPaneOpen"] is False, (
        f"v3 - 4 修:点 link 不应再出现 side panel, 实际 detailPaneOpen={sig_after['detailPaneOpen']}"
    )


@pytest.mark.e2e
def test_workspace_url_unchanged_after_open(admin_token: str, page: Page) -> None:
    """点 link 后,原 workspace tab URL / tab list 都不变。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/{KIND_EPICS}"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.5)

    has_link = page.evaluate("""
        !!document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]')
    """)
    if not has_link:
        pytest.skip("no epics seeded in dev DB for this project")

    sig_before = _collect_signals(page)
    assert sig_before["tabStripCount"] == 1
    assert sig_before["url"].endswith(f"/{KIND_EPICS}")

    # 点 epic 链接
    page.evaluate("""
        (() => {
            const a = document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]');
            if (a) a.click();
        })()
    """)
    time.sleep(1.0)
    sig_after = _collect_signals(page)
    log(f"   after click: {sig_after}")
    _shot(page, "url_unchanged.png")

    # 关键断言:workspace 上下文完全不变
    assert sig_after["url"].endswith(f"/{KIND_EPICS}")
    assert sig_after["tabStripCount"] == 1, f"tab 列表应保持 1, 实际 {sig_after['tabStripCount']}"


@pytest.mark.e2e
def test_sidebar_menu_click_not_opens_new_tab(admin_token: str, page: Page) -> None:
    """左侧菜单点 kanban **不**开新 tab(菜单是切换 tab,不是开新 tab)。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/{KIND_EPICS}"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.5)

    sig_before = _collect_signals(page)
    assert sig_before["tabStripCount"] == 1

    # 点左侧菜单的 kanban 项(在 sidebar 里,不应被全局 click 拦截器影响)
    page.click('a.project-nav-button-v7[aria-label="看板"]')
    time.sleep(0.5)
    sig_after = _collect_signals(page)
    log(f"   after sidebar kanban click: {sig_after}")
    _shot(page, "sidebar_not_intercepted.png")

    # 关键断言:菜单 click 仍按原行为 work(切 tab,不开新 tab)
    assert sig_after["activeTabKind"] == KIND_KANBAN, (
        f"菜单切到 kanban,实际 active={sig_after['activeTabKind']!r}"
    )
    assert sig_after["tabStripCount"] == 2, f"应是 2 tab(overview + kanban), 实际 {sig_after['tabStripCount']}"
    assert sig_after["url"].endswith(f"/{KIND_KANBAN}"), (
        f"URL 应是 /{KIND_KANBAN}, 实际 {sig_after['url']!r}"
    )
