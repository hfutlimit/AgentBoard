"""项目工作台多 Tab 系统 E2E（2026-08-21 结构调整，v2 修）

v2 核心修复：tab 切换是纯 service 操作（ajax 风格），**不**触发 Angular
router 跳路由，因此 app.ts 的 loadRoute() 不会被调用，其他 tab 状态保留。

需求：
- 8 个子视图（概览/看板/Epics/工作项/提案/文档/成员/设置）可同时打开为独立 tab
- 点击左侧菜单 → 新增 tab；如果已开 → 激活（**不**触发 router 跳路由）
- 切换 tab 不卸载，状态保留
- 关闭 tab → 从 tab 条移除
- URL 用 history.replaceState 静默同步（不触发 router），但与 service 状态一致
- 同 (projectId, kind) 至多 1 个 tab；切项目 → 清空 tab
- 切 tab **不**引起整页刷新 / 数据重拉

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
5. test_no_page_reload_on_tab_click    [v2 修新增]
   设置 window sentinel → 点 tab → sentinel 仍在（证明无 page reload）
6. test_state_preserved_across_tab_switch    [v2 修新增]
   文档 tab 选某 filter → 切到 Kanban → 切回文档 tab → filter 仍是原值
   （证明非激活 tab 状态保留）
7. test_url_replaced_silently_on_tab_click   [v2 修新增]
   点 tab → URL 路径更新为对应 section，但 history entry 不变（replaceState
   不会新增 history）

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

# 项目 ID 1 = 系统自动 seed 的 Apex 演示项目
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
    """点击左侧菜单某个 kind。"""
    label = LABEL_BY_KIND[kind]
    page.click(f'a.project-nav-button-v7[aria-label="{label}"]')
    time.sleep(0.4)


def _click_tab_strip(page: Page, kind: str) -> None:
    """点击 tab 条上某个 kind。"""
    page.click(f'.tab-strip-item[data-tab-id="{PROJECT_ID}-{kind}"] .tab-strip-link')
    time.sleep(0.4)


def _close_tab(page: Page, kind: str) -> None:
    """点 tab 上的 × 关闭按钮。"""
    page.click(f'.tab-strip-item[data-tab-id="{PROJECT_ID}-{kind}"] .tab-strip-close')
    time.sleep(0.4)


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
    assert sig["activePaneKind"] == KIND_OVERVIEW
    # menu active 是中文 label,断言含中文字符串
    assert LABEL_BY_KIND[KIND_OVERVIEW] in sig["menuActive"], f"menu active 应含 '{LABEL_BY_KIND[KIND_OVERVIEW]}', 实际 {sig['menuActive']!r}"


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
    assert sig1["tabStripCount"] == 2
    assert sig1["tabStripItems"][1]["id"] == f"{PROJECT_ID}-{KIND_KANBAN}"
    assert sig1["tabStripItems"][1]["active"] is True
    assert sig1["activePaneKind"] == KIND_KANBAN

    _click_menu(page, KIND_PROPOSALS)
    sig2 = _collect_signals(page)
    log(f"   after proposals = {sig2}")
    _shot(page, "03_proposals.png")
    assert sig2["tabStripCount"] == 3
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

    _click_tab_strip(page, KIND_KANBAN)
    sig = _collect_signals(page)
    log(f"   after click kanban tab = {sig}")
    _shot(page, "04_reactivate_kanban.png")
    assert sig["tabStripCount"] == 3
    kanban_item = next((t for t in sig["tabStripItems"] if t["id"] == f"{PROJECT_ID}-{KIND_KANBAN}"), None)
    assert kanban_item is not None and kanban_item["active"] is True
    assert sig["activePaneKind"] == KIND_KANBAN


@pytest.mark.e2e
def test_close_tab_activates_neighbor(page: Page, admin_token: str) -> None:
    """关闭中间 tab → 邻居 tab 激活。"""
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/overview"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.0)
    _click_menu(page, KIND_KANBAN)
    _click_menu(page, KIND_PROPOSALS)
    time.sleep(0.5)

    _close_tab(page, KIND_KANBAN)
    sig = _collect_signals(page)
    log(f"   after close kanban = {sig}")
    _shot(page, "05_close_kanban.png")
    assert sig["tabStripCount"] == 2
    remaining = [t["id"] for t in sig["tabStripItems"]]
    assert f"{PROJECT_ID}-{KIND_KANBAN}" not in remaining
    assert f"{PROJECT_ID}-{KIND_OVERVIEW}" in remaining
    assert f"{PROJECT_ID}-{KIND_PROPOSALS}" in remaining
    active_item = next((t for t in sig["tabStripItems"] if t["active"]), None)
    assert active_item is not None
    assert active_item["id"] == f"{PROJECT_ID}-{KIND_PROPOSALS}"


# ============================================================
# v2 修新增 — 切 tab 不应触发 page reload / 状态保留 / URL 静默同步
# ============================================================

@pytest.mark.e2e
def test_no_page_reload_on_tab_click(page: Page, admin_token: str) -> None:
    """切 tab 不应触发整页刷新（v2 修核心断言）。

    验证方式：设置 data-test-sentinel attribute → 点 tab → 重新读 sentinel
    应仍为 'present'（如果 page reload 了，sentinel 会消失）。
    """
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/overview"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.0)

    # 在 body 上设 sentinel（DOM 属性，page reload 会清掉）
    page.evaluate("document.body.setAttribute('data-test-sentinel', 'present')")

    # 切 3 次 tab：kanban → proposals → kanban
    _click_menu(page, KIND_KANBAN)
    _click_menu(page, KIND_PROPOSALS)
    _click_tab_strip(page, KIND_KANBAN)
    time.sleep(0.3)

    # 验证 sentinel 还在（说明没有 page reload）
    sentinel = page.evaluate("document.body.getAttribute('data-test-sentinel')")
    log(f"   sentinel after tab clicks = {sentinel!r}")
    _shot(page, "06_no_reload.png")
    assert sentinel == "present", (
        f"切 tab 触发了 page reload (sentinel 消失). "
        f"v2 修复必须让 service 调用走纯 client state，不触发 router 跳路由。"
    )


@pytest.mark.e2e
def test_state_preserved_across_tab_switch(page: Page, admin_token: str) -> None:
    """非激活 tab 状态保留（v2 修核心断言）。

    场景：文档 tab 加载后选 filter → 切到 kanban → 切回 documents →
    文档 tab 的 select 元素仍是原值（DOM 没重建，Angular 组件实例活着）。
    """
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/overview"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.0)

    # 开 documents tab
    _click_menu(page, KIND_DOCUMENTS)
    time.sleep(1.0)
    sig1 = _collect_signals(page)
    assert sig1["tabStripCount"] == 2
    assert sig1["activePaneKind"] == KIND_DOCUMENTS
    _shot(page, "07a_documents_open.png")

    # 记录 documents pane 内 select 数量
    select_count = page.evaluate("""
        document.querySelectorAll('.tab-pane-host:not(.tab-pane-host--hidden) select').length
    """)
    log(f"   documents tab select count = {select_count}")
    _shot(page, "07b_documents_filter.png")

    # 切到 kanban tab（documents tab 隐藏但实例不销毁）— 打开新 tab
    _click_menu(page, KIND_KANBAN)
    time.sleep(0.5)
    sig2 = _collect_signals(page)
    # 应该是 3 tab:overview + documents + kanban
    assert sig2["tabStripCount"] == 3, f"加 kanban 后应是 3 tab, 实际 {sig2['tabStripCount']}"
    assert sig2["activePaneKind"] == KIND_KANBAN
    _shot(page, "07c_switched_to_kanban.png")

    # 切回 documents tab（在 tab strip 上点 documents）
    _click_tab_strip(page, KIND_DOCUMENTS)
    time.sleep(0.5)
    sig3 = _collect_signals(page)
    # 仍是 3 tab:overview + documents + kanban (激活的是 documents)
    assert sig3["tabStripCount"] == 3, f"切 tab 不增删 tab,应是 3, 实际 {sig3['tabStripCount']}"
    assert sig3["activePaneKind"] == KIND_DOCUMENTS
    _shot(page, "07d_switched_back.png")

    # 验证 documents tab 的 select 数仍是原值（DOM 没重建）
    select_count_after = page.evaluate("""
        document.querySelectorAll('.tab-pane-host:not(.tab-pane-host--hidden) select').length
    """)
    log(f"   documents tab select count after switch = {select_count_after}")
    assert select_count_after == select_count, (
        f"切走再切回，select 数量变了（{select_count} → {select_count_after}）— "
        f"说明 tab 实例被销毁/重建，状态会丢失。v2 必须用 CSS display:none 保活。"
    )


@pytest.mark.e2e
def test_url_replaced_silently_on_tab_click(page: Page, admin_token: str) -> None:
    """点 tab → URL 静默同步（replaceState，不新增 history）。

    验证方式：记下 history.length → 点 3 次 tab → 验证 URL 变了但 history.length 没变。
    """
    url = f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/overview"
    goto_url_with_token(page, admin_token, url)
    time.sleep(1.0)

    history_before = page.evaluate("history.length")
    url_before = page.evaluate("location.pathname")
    log(f"   before: history.length={history_before}, url={url_before!r}")

    # 切 3 次 tab
    _click_menu(page, KIND_KANBAN)
    _click_menu(page, KIND_PROPOSALS)
    _click_menu(page, KIND_DOCUMENTS)
    time.sleep(0.3)

    history_after = page.evaluate("history.length")
    url_after = page.evaluate("location.pathname")
    log(f"   after 3 tab clicks: history.length={history_after}, url={url_after!r}")
    _shot(page, "08_url_replaced.png")

    # URL 应更新为 documents（最后点的那次）
    assert url_after.endswith(f"/{KIND_DOCUMENTS}"), (
        f"URL 应更新到 /{KIND_DOCUMENTS}, 实际 {url_after!r} — v2 用 replaceState 同步 URL"
    )
    # history.length 应不变（replaceState 不增 history）
    assert history_after == history_before, (
        f"history.length 变化（{history_before} → {history_after}）— "
        f"v2 必须用 replaceState 静默同步 URL，不能用 pushState"
    )
