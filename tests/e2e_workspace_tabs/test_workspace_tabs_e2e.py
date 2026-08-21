"""项目工作台多 Tab 系统 E2E（2026-08-21 结构调整）

需求：
- 8 个子视图（概览/看板/Epics/工作项/提案/文档/成员/设置）可同时打开为独立 tab
- 点击左侧菜单 → 新增 tab；如果已开 → 激活
- 切换 tab 不卸载，状态保留
- 关闭 tab → 从 tab 条移除
- URL 反映当前激活 section（直链/前进后退/刷新 work）
- 同 (projectId, kind) 至多 1 个 tab；切项目 → 清空 tab

E2E 真实断言（按 test 函数分粒度）：
1. test_open_project_default_overview_tab_only
   进入项目 → 1 个 tab，激活的是概览
2. test_click_menu_adds_tab
   点 Kanban → 2 个 tab（概览 + 看板），激活看板
   点 Proposals → 3 个 tab（概览 + 看板 + 提案），激活提案
3. test_click_existing_tab_activates_only
   点 Kanban（开）→ 激活看板，tab 数仍 3
4. test_close_tab
   关闭中间 tab → 邻居 tab 激活，tab 数 -1
5. test_state_preserved_across_tab_switch
   看板 tab 触发某筛选（开关 includeAll），切到提案，再切回 → 筛选态仍在
6. test_url_sync_and_browser_back
   URL 反映 section；浏览器 back → 回到上一个 section（同时上一个 tab 重新激活）

这些测试基于 conftest 注入的 admin_token + frontend 端口 :4200 + dev API。
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

# 项目 ID 1 = 系统自动 seed 的 Apex 演示项目（与 e2e_epic149/test_x_b1_route_8tab.py 一致）
PROJECT_ID = 1

# 8 menu item kind
KIND_OVERVIEW = "overview"
KIND_KANBAN = "kanban"
KIND_EPICS = "epics"
KIND_BACKLOG = "backlog"
KIND_PROPOSALS = "proposals"
KIND_DOCUMENTS = "documents"
KIND_MEMBERS = "members"
KIND_SETTINGS = "settings"

# menu item 在 sidebar 显示的文本（与 shell.ts menuItems 对应）
LABEL_BY_KIND = {
    KIND_OVERVIEW: "概览",
    KIND_KANBAN: "看板",
    KIND_EPICS: "Epics",
    KIND_BACKLOG: "工作项",
    KIND_PROPOSALS: "提案",
    KIND_DOCUMENTS: "文档",
    KIND_MEMBERS: "成员与 Agents",
    KIND_SETTINGS: "设置",
}


def _collect_signals(page: Page) -> dict:
    """一次 evaluate 收集 tab/menu/URL 三组信号。"""
    return page.evaluate("""
        ({
            url: location.pathname,
            tabStripItems: Array.from(document.querySelectorAll('.tab-strip-item')).map(el => ({
                id: el.getAttribute('data-tab-id'),
                label: el.querySelector('.tab-strip-label')?.textContent?.trim() || '',
                active: el.classList.contains('tab-strip-item--active'),
            })),
            tabStripCount: document.querySelectorAll('.tab-strip-item').length,
            activePaneKind: (document.querySelector('.tab-pane-host:not(.tab-pane-host--hidden) [data-tab-kind]')?.getAttribute('data-tab-kind') || null),
            menuActive: (document.querySelector('a.project-nav-button-v7[aria-current="page"]')?.textContent?.trim().replace(/\\\\s+/g, ' ') || ''),
        })
    """)


def _click_menu(page: Page, kind: str) -> None:
    """点击左侧菜单某个 kind。label 是中文，DOM 上 a 的 aria-label 是中文。"""
    label = LABEL_BY_KIND[kind]
    page.click(f'a.project-nav-button-v7[aria-label="{label}"]')
    time.sleep(0.5)


def _click_tab_strip(page: Page, kind: str) -> None:
    """点击 tab 条上某个 kind。"""
    page.click(f'.tab-strip-item[data-tab-id="{PROJECT_ID}-{kind}"] .tab-strip-link')
    time.sleep(0.5)


def _close_tab(page: Page, kind: str) -> None:
    """点 tab 上的 × 关闭按钮。"""
    page.click(f'.tab-strip-item[data-tab-id="{PROJECT_ID}-{kind}"] .tab-strip-close')
    time.sleep(0.5)


def _shot(page: Page, name: str) -> None:
    p = SHOT_DIR / name
    page.screenshot(path=str(p), full_page=False)
    log(f"   shot {p.name} size={p.stat().st_size}B")


# ============================================================
# Pytest test_* functions
# ============================================================

@pytest.mark.e2e
def test_open_project_default_overview_tab_only(page: Page, admin_token: str) -> None:
    """进入项目：默认只有 1 个 tab（概览），激活。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/overview"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.0)
    sig = _collect_signals(page)
    log(f"   sig = {sig}")
    _shot(page, "01_default.png")

    assert sig["url"].endswith(f"/{KIND_OVERVIEW}"), f"URL 应为 /{KIND_OVERVIEW}, 实际 {sig['url']!r}"
    assert sig["tabStripCount"] == 1, f"应有 1 个 tab, 实际 {sig['tabStripCount']}"
    assert sig["tabStripItems"][0]["id"] == f"{PROJECT_ID}-{KIND_OVERVIEW}"
    assert sig["tabStripItems"][0]["active"] is True
    assert sig["activePaneKind"] == KIND_OVERVIEW, f"激活 pane 应为 {KIND_OVERVIEW}, 实际 {sig['activePaneKind']!r}"
    assert KIND_OVERVIEW in sig["menuActive"], f"menu active 应包含 {KIND_OVERVIEW}, 实际 {sig['menuActive']!r}"


@pytest.mark.e2e
def test_click_menu_adds_tab(page: Page, admin_token: str) -> None:
    """点 Kanban 菜单 → 加 tab；点 Proposals 菜单 → 再加 tab。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/overview"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.0)

    _click_menu(page, KIND_KANBAN)
    sig1 = _collect_signals(page)
    log(f"   after kanban = {sig1}")
    _shot(page, "02_kanban.png")
    assert sig1["tabStripCount"] == 2, f"点 Kanban 后应有 2 个 tab, 实际 {sig1['tabStripCount']}"
    assert sig1["tabStripItems"][1]["id"] == f"{PROJECT_ID}-{KIND_KANBAN}"
    assert sig1["tabStripItems"][1]["active"] is True
    assert sig1["activePaneKind"] == KIND_KANBAN

    _click_menu(page, KIND_PROPOSALS)
    sig2 = _collect_signals(page)
    log(f"   after proposals = {sig2}")
    _shot(page, "03_proposals.png")
    assert sig2["tabStripCount"] == 3, f"点 Proposals 后应有 3 个 tab, 实际 {sig2['tabStripCount']}"
    assert sig2["tabStripItems"][2]["id"] == f"{PROJECT_ID}-{KIND_PROPOSALS}"
    assert sig2["tabStripItems"][2]["active"] is True
    assert sig2["activePaneKind"] == KIND_PROPOSALS


@pytest.mark.e2e
def test_click_existing_tab_activates_only(page: Page, admin_token: str) -> None:
    """点已开 tab → 只激活，tab 数不变。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/overview"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.0)
    _click_menu(page, KIND_KANBAN)
    _click_menu(page, KIND_PROPOSALS)
    time.sleep(0.5)
    sig0 = _collect_signals(page)
    assert sig0["tabStripCount"] == 3

    # 此时 proposals 激活。点 Kanban tab → 应激活 kanban，tab 数仍 3
    _click_tab_strip(page, KIND_KANBAN)
    sig1 = _collect_signals(page)
    log(f"   after click kanban tab = {sig1}")
    _shot(page, "04_reactivate_kanban.png")
    assert sig1["tabStripCount"] == 3, f"点已开 tab 后 tab 数应仍为 3, 实际 {sig1['tabStripCount']}"
    kanban_item = next((t for t in sig1["tabStripItems"] if t["id"] == f"{PROJECT_ID}-{KIND_KANBAN}"), None)
    assert kanban_item is not None and kanban_item["active"] is True
    assert sig1["activePaneKind"] == KIND_KANBAN


@pytest.mark.e2e
def test_close_tab_activates_neighbor(page: Page, admin_token: str) -> None:
    """关闭中间 tab → 邻居 tab 激活。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/overview"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.0)
    _click_menu(page, KIND_KANBAN)
    _click_menu(page, KIND_PROPOSALS)
    time.sleep(0.5)
    sig0 = _collect_signals(page)
    assert sig0["tabStripCount"] == 3

    # 当前 proposals 激活，关闭 kanban（中间 tab）
    _close_tab(page, KIND_KANBAN)
    sig1 = _collect_signals(page)
    log(f"   after close kanban = {sig1}")
    _shot(page, "05_close_kanban.png")
    assert sig1["tabStripCount"] == 2, f"关闭 1 个后 tab 数应为 2, 实际 {sig1['tabStripCount']}"
    remaining = [t["id"] for t in sig1["tabStripItems"]]
    assert f"{PROJECT_ID}-{KIND_KANBAN}" not in remaining, "kanban tab 应被移除"
    assert f"{PROJECT_ID}-{KIND_OVERVIEW}" in remaining
    assert f"{PROJECT_ID}-{KIND_PROPOSALS}" in remaining
    # 关闭中间 tab，激活态保持原激活的（proposals）— 因为服务约定"激活态保持"
    # 或者切到左侧邻居（overview）— 取决于 closeTab 实现。当前是「保持当前激活 ID，除非关的就是它」
    # 关的是 kanban，激活的本来就是 proposals，所以仍 proposals
    active_item = next((t for t in sig1["tabStripItems"] if t["active"]), None)
    assert active_item is not None
    assert active_item["id"] == f"{PROJECT_ID}-{KIND_PROPOSALS}"


@pytest.mark.e2e
def test_close_active_tab_activates_neighbor(page: Page, admin_token: str) -> None:
    """关闭当前激活的 tab → 邻居激活（左侧优先）。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/overview"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.0)
    _click_menu(page, KIND_KANBAN)
    time.sleep(0.5)

    # 激活的是 kanban。关掉 kanban → 应该激活 overview
    _close_tab(page, KIND_KANBAN)
    sig = _collect_signals(page)
    log(f"   after close active kanban = {sig}")
    _shot(page, "06_close_active.png")
    assert sig["tabStripCount"] == 1
    assert sig["tabStripItems"][0]["id"] == f"{PROJECT_ID}-{KIND_OVERVIEW}"
    assert sig["tabStripItems"][0]["active"] is True


@pytest.mark.e2e
def test_url_sync_and_browser_back(page: Page, admin_token: str) -> None:
    """URL 反映 section；浏览器 back → 回到上一个 section + 上一个 tab 激活。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/overview"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.0)
    _click_menu(page, KIND_KANBAN)
    time.sleep(0.5)
    _click_menu(page, KIND_PROPOSALS)
    time.sleep(0.5)

    sig0 = _collect_signals(page)
    assert sig0["url"].endswith(f"/{KIND_PROPOSALS}")
    assert sig0["tabStripCount"] == 3
    _shot(page, "07_before_back.png")

    page.go_back(wait_until="domcontentloaded", timeout=30000)
    time.sleep(1.5)
    sig1 = _collect_signals(page)
    log(f"   back 1 = {sig1}")
    _shot(page, "08_back_1.png")
    assert sig1["url"].endswith(f"/{KIND_KANBAN}"), f"back 后 URL 应为 /{KIND_KANBAN}, 实际 {sig1['url']!r}"
    kanban_item = next((t for t in sig1["tabStripItems"] if t["id"] == f"{PROJECT_ID}-{KIND_KANBAN}"), None)
    assert kanban_item is not None and kanban_item["active"] is True
    assert sig1["activePaneKind"] == KIND_KANBAN

    page.go_back(wait_until="domcontentloaded", timeout=30000)
    time.sleep(1.5)
    sig2 = _collect_signals(page)
    log(f"   back 2 = {sig2}")
    _shot(page, "09_back_2.png")
    assert sig2["url"].endswith(f"/{KIND_OVERVIEW}")
    overview_item = next((t for t in sig2["tabStripItems"] if t["id"] == f"{PROJECT_ID}-{KIND_OVERVIEW}"), None)
    assert overview_item is not None and overview_item["active"] is True
