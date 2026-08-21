"""项目工作台 master-detail side panel E2E（2026-08-21 Epic152 v3 修）

需求:
- 用户从 *-tab 内部点 link (story / task / epic / proposal / sprint / document)
  → **不**跳到顶层 /story/:id / /task/:id / /epic/:id / /proposals/:id 全页
  → 改为 workspace 内的 master-detail side panel
- side panel 出现,workspace tab 上下文保留
- 点 × 关闭 side panel
- 点 "open in full page" 走原顶层路由

E2E 真实断言:
1. test_detail_pane_appears_on_internal_link_click
   从 epics tab 点 story 链接 → side panel 出现,URL 不变顶层路由
2. test_detail_pane_closes_on_x
   点 × 关闭 side panel
3. test_detail_pane_open_full_page_works
   点 "open in full page" → 跳到顶层 /story/:id
4. test_detail_pane_does_not_break_workspace_context
   detail panel 打开时,workspace tab 切换仍 work (tab 上下文不丢)
5. test_detail_pane_does_not_affect_other_links
   侧边栏 / 顶栏的同 URL link 仍按原行为 (不被拦截)
"""
from __future__ import annotations

import time

import pytest
from playwright.sync_api import Page

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
            menuActive: (document.querySelector('a.project-nav-button-v7[aria-current="page"]')?.textContent?.trim().replace(/\\\\s+/g, ' ') || ''),
            detailPaneOpen: !!document.querySelector('.detail-pane'),
            detailPaneKind: (document.querySelector('.detail-pane-label')?.textContent?.trim() || null),
            detailPaneId: (document.querySelector('.detail-pane-id')?.textContent?.trim() || null),
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
def test_detail_pane_appears_on_internal_link_click(page: Page, admin_token: str) -> None:
    """从 *-tab 内部点 link → side panel 出现 + URL 不跳顶层路由。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/{KIND_EPICS}"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.5)

    # 在 Epics tab 内找第一个 epic 行 (有 routerLink 跳 /epic/:id)
    # Epics tab 渲染 list,每个 item 是 <a [routerLink]="['/epic', item.id]">
    # 我们用 evaluate 找第一个 a[href^="/epic/"] 并触发 click
    first_epic_id = page.evaluate("""
        (() => {
            const a = document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]');
            if (!a) return null;
            const m = a.getAttribute('href').match(/^\\/epic\\/(\\d+)/);
            return m ? Number(m[1]) : null;
        })()
    """)
    if first_epic_id is None:
        # 没有 epic 数据,跳过 (dev 环境可能没 seed 数据)
        pytest.skip("no epics seeded in dev DB for this project")

    log(f"   first epic id = {first_epic_id}")

    # URL 应仍是 /epics (workspace 内)
    sig_before = _collect_signals(page)
    log(f"   sig before click = {sig_before}")
    _shot(page, "pane_01_before.png")

    # 点第一个 epic link
    page.evaluate("""
        (() => {
            const a = document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]');
            if (a) a.click();
        })()
    """)
    time.sleep(0.5)

    sig_after = _collect_signals(page)
    log(f"   sig after click = {sig_after}")
    _shot(page, "pane_02_after_click.png")

    # 关键断言 1:side panel 出现
    assert sig_after["detailPaneOpen"] is True, (
        f"点 *-tab 内部 link 后 side panel 没出现 — workspace 内 link 应被拦截改为 panel "
        f"(URL 仍是 {sig_after['url']!r}, panel 状态 = {sig_after['detailPaneOpen']})"
    )
    # 关键断言 2:side panel 显示 Epic + id
    assert sig_after["detailPaneKind"] == "Epic", (
        f"panel 应显示 'Epic' label, 实际 {sig_after['detailPaneKind']!r}"
    )
    assert sig_after["detailPaneId"] == f"#{first_epic_id}", (
        f"panel id 应为 #{first_epic_id}, 实际 {sig_after['detailPaneId']!r}"
    )
    # 关键断言 3:URL 不跳顶层 /epic/:id — 应仍是 /epics (workspace 上下文)
    assert sig_after["url"].endswith(f"/{KIND_EPICS}"), (
        f"URL 应保持在 workspace {KIND_EPICS}, 实际 {sig_after['url']!r} — "
        f"v3 修复必须把 *-tab 内部 link 拦截改成 side panel,不允许跳顶层全页"
    )
    # 关键断言 4:workspace tab 上下文保留 (epics tab 仍 active)
    assert sig_after["activeTabKind"] == KIND_EPICS


@pytest.mark.e2e
def test_detail_pane_closes_on_x(page: Page, admin_token: str) -> None:
    """点 × 关闭 side panel,workspace 上下文不变。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/{KIND_EPICS}"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.5)

    # 触发一个 detail panel
    has_link = page.evaluate("""
        !!document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]')
    """)
    if not has_link:
        pytest.skip("no epics seeded in dev DB for this project")

    page.evaluate("""
        (() => {
            const a = document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]');
            if (a) a.click();
        })()
    """)
    time.sleep(0.5)
    sig1 = _collect_signals(page)
    assert sig1["detailPaneOpen"] is True

    # 点 ×
    page.click('.detail-pane-close')
    time.sleep(0.3)
    sig2 = _collect_signals(page)
    log(f"   after close = {sig2}")
    _shot(page, "pane_03_after_close.png")

    assert sig2["detailPaneOpen"] is False
    # workspace 上下文仍在 (epics tab 仍 active, URL 仍是 /epics)
    assert sig2["url"].endswith(f"/{KIND_EPICS}")
    assert sig2["activeTabKind"] == KIND_EPICS


@pytest.mark.e2e
def test_detail_pane_does_not_break_workspace_context(page: Page, admin_token: str) -> None:
    """detail panel 打开时,workspace tab 切换仍 work (无 router 跳路由)。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/{KIND_EPICS}"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.5)

    has_link = page.evaluate("""
        !!document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]')
    """)
    if not has_link:
        pytest.skip("no epics seeded in dev DB for this project")

    # 开 detail panel
    page.evaluate("""
        (() => {
            const a = document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]');
            if (a) a.click();
        })()
    """)
    time.sleep(0.5)
    sig1 = _collect_signals(page)
    assert sig1["detailPaneOpen"] is True

    # 设置 sentinel — 防止 page reload 测不出来
    page.evaluate("document.body.setAttribute('data-detail-pane-sentinel', 'present')")

    # 切到 kanban tab (左侧菜单)
    page.click('a.project-nav-button-v7[aria-label="看板"]')
    time.sleep(0.5)
    sig2 = _collect_signals(page)
    log(f"   after tab switch with panel open = {sig2}")
    _shot(page, "pane_04_tab_switch.png")

    # 关键断言:tab 切换 work + 没 page reload (sentinel 还在)
    sentinel = page.evaluate("document.body.getAttribute('data-detail-pane-sentinel')")
    assert sentinel == "present", (
        f"切 tab 触发 page reload — v2 修复的 ajax 风格被破坏。sentinel={sentinel!r}"
    )
    assert sig2["activeTabKind"] == KIND_KANBAN
    # detail panel 状态:我们没显式关掉它,可能仍 open (这是设计 — 切 tab 不关 panel)
    # 但 URL 应是 kanban (replaceState 触发的)
    assert sig2["url"].endswith(f"/{KIND_KANBAN}"), (
        f"切到 kanban 后 URL 应是 /{KIND_KANBAN}, 实际 {sig2['url']!r}"
    )
    # panel 仍显示 (切 tab 不影响 panel 状态)
    assert sig2["detailPaneOpen"] is True, (
        f"切 tab 不应关掉 detail panel — panel 是独立于 tab 的状态。实际 {sig2['detailPaneOpen']}"
    )
    # panel 仍显示 Epic #N (panel 内容是独立 selection)
    assert sig2["detailPaneKind"] == "Epic"


@pytest.mark.e2e
def test_detail_pane_does_not_affect_other_links(page: Page, admin_token: str) -> None:
    """侧边栏 / 顶栏的同 URL link 不被拦截 (他们的 routerLink 仍按原行为 work)。

    这里我们不强求侧边栏有 /epic/:id link(实际没有),
    只验证 workspace 拦截逻辑不误伤 — 点左侧菜单切 tab 仍正常 work。
    """
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/{KIND_EPICS}"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.5)

    # 点左侧菜单切到 kanban (不是 detail 链接,应正常 work)
    page.click('a.project-nav-button-v7[aria-label="看板"]')
    time.sleep(0.5)
    sig = _collect_signals(page)
    _shot(page, "pane_05_menu_not_intercepted.png")
    assert sig["activeTabKind"] == KIND_KANBAN
    assert sig["url"].endswith(f"/{KIND_KANBAN}")
    # 没意外打开 detail panel
    assert sig["detailPaneOpen"] is False


@pytest.mark.e2e
def test_detail_pane_open_full_page_works(page: Page, admin_token: str) -> None:
    """点 "open in full page" 跳到顶层 /epic/:id (legacy 路由,app.html @case 接管)。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/{KIND_EPICS}"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.5)

    has_link = page.evaluate("""
        !!document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]')
    """)
    if not has_link:
        pytest.skip("no epics seeded in dev DB for this project")

    # 开 detail panel
    first_epic_id = page.evaluate("""
        (() => {
            const a = document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]');
            if (!a) return null;
            const m = a.getAttribute('href').match(/^\\/epic\\/(\\d+)/);
            return m ? Number(m[1]) : null;
        })()
    """)
    page.evaluate("""
        (() => {
            const a = document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/epic/"]');
            if (a) a.click();
        })()
    """)
    time.sleep(0.5)

    # 点 "open in full page"
    page.click('.detail-pane .btn-primary-sm')
    time.sleep(1.0)
    sig = _collect_signals(page)
    log(f"   after open full = {sig}")
    _shot(page, "pane_06_full_page.png")

    # 顶层 /epic/:id 路由被 app.ts loadRoute 接管,view='epic', 整个 app 切到 epic 视图
    # URL 应是 /epic/:id
    assert sig["url"] == f"/epic/{first_epic_id}", (
        f"open full page 应跳 /epic/{first_epic_id}, 实际 {sig['url']!r}"
    )
    # detail panel 应关闭
    assert sig["detailPaneOpen"] is False
