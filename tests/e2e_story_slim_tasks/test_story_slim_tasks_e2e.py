r"""Story 详情页「简洁任务列表」v7.3 E2E（2026-08-21）。

背景：Story 详情页 task 列表原本挂了整套重型装备（批量选择框、6 状态筛选 pill、
只看我开关、排序/分组下拉、密度/视图切换、自动刷新、筛选预设、导出菜单、
键盘快捷键提示、统计条——五层堆叠）。v7.3 收敛为「标题行 + 轻量筛选行」两层：

E2E 真实断言：
1. test_taskbar_slim_inline_progress
   taskbar 单行承载 标题「任务 N」+ 内联进度（1/3 完成）；旧独立汇总条
   .task-list-summary 不复存在；刷新/自动/视图切换按钮 icon 化（label display:none）
2. test_options_popover_open_close_and_controls
   选项 popover 默认关闭；打开后含 只看我/密度/排序/分组/筛选预设/导出 全套；
   点 backdrop 关闭
3. test_zero_count_status_chips_hidden
   3 个 task 分布 todo/in_progress/done → 状态 chips 只有 全部+3 个非零状态；
   评审中/已阻塞（零计数且未激活）不渲染
4. test_inline_noise_reduction
   无 due 的行不渲染 due-pill、无 assignee 的行不渲染 assignee-pill（旧版有
   「⏰ 设截止」「?」占位）
5. test_options_popover_density_and_active_dot
   popover 内点密度 → .entity-list 加 density-compact；关闭 popover 后
   选项按钮出现「已改默认」活动小圆点 .task-opts-dot
6. test_checkbox_and_kbd_hint_reveal
   批量勾选框默认 opacity:0（hover 才显）；kbd-hint 默认 display:none

跑法（dev 环境端口）：
  $env:AGENTBOARD_E2E_BASE='http://127.0.0.1:28080'
  $env:AGENTBOARD_API_BASE='http://127.0.0.1:18000'
  .\.venv\Scripts\python.exe -m pytest tests/e2e_story_slim_tasks/ -q -p no:cacheprovider -m e2e
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


def _shot(page: Page, name: str) -> None:
    p = SHOT_DIR / name
    page.screenshot(path=str(p), full_page=False)
    log(f"   shot {p.name} size={p.stat().st_size}B")


def _open_story(slim_story: dict, admin_token: str, page: Page) -> None:
    url = f"{FRONTEND_ORIGIN}/story/{slim_story['story_id']}"
    goto_url_with_token(page, admin_token, url)
    # Story 详情页默认落在「📋 详情」tab，任务列表在「📝 Task 列表」tab
    page.wait_for_selector(".detail-tabs .tab-btn", timeout=15000)
    page.locator(".detail-tabs .tab-btn", has_text="Task 列表").click()
    page.wait_for_selector(".taskbar--slim", timeout=15000)
    page.wait_for_selector(".entity-item--rich", timeout=15000)


def _row(page: Page, title: str):
    """按 task 标题取对应行元素（返回 locator）。"""
    return page.locator(".entity-item--rich", has_text=title).first


@pytest.mark.e2e
def test_taskbar_slim_inline_progress(slim_story, admin_token: str, page: Page) -> None:
    """taskbar 单行：标题+计数+内联进度；旧汇总条不复存在；动作按钮 icon 化。"""
    _open_story(slim_story, admin_token, page)

    # 标题行：任务 + count-badge=API 总数 + 内联进度 done/total（页面显示 = API 事实；
    # story 创建会自动编排生成「设计：/实现：」子任务，故不硬编码计数）
    total, done = slim_story["expected_total"], slim_story["expected_done"]
    assert total >= 3, f"fixture 数据异常: expected_total={total}"
    assert page.locator(".taskbar--slim .taskbar__title .count-badge").inner_text().strip() == str(total)
    progress_text = page.locator(".taskbar--slim .taskbar__progress-text").inner_text().strip()
    assert f"{done}/{total}" in progress_text, (
        f"内联进度应含 '{done}/{total}', 实际 {progress_text!r}"
    )
    assert page.locator(".taskbar--slim .summary-stack .summary-seg").count() >= 1, "进度分段条应渲染"

    # 旧独立汇总条已删除
    assert page.locator(".task-list-summary").count() == 0, (
        "v7.3 删除了独立汇总条 .task-list-summary（进度并入 taskbar 内联）"
    )

    # 高频动作保留在标题行；文字标签 CSS 隐藏（icon 化）
    for btn_id in ("#refreshBtn", "#autoRefreshBtn", "#boardToggle"):
        assert page.locator(f".taskbar--slim .taskbar__actions {btn_id}").count() == 1, (
            f"{btn_id} 应保留在 taskbar 动作区"
        )
    hidden = page.evaluate("""
        (() => {
            const out = [];
            for (const sel of ['.refresh-label', '.auto-refresh-label', '.view-label']) {
                const el = document.querySelector(`.taskbar--slim ${sel}`);
                out.push([sel, el ? getComputedStyle(el).display : 'ABSENT']);
            }
            return out;
        })()
    """)
    for sel, display in hidden:
        assert display == "none", f"{sel} 应被 CSS 隐藏（icon 化）, 实际 display={display!r}"

    # 新建任务入口保留
    assert page.locator(".taskbar--slim .taskbar__actions button", has_text="新建任务").count() == 1
    _shot(page, "v73_taskbar_slim.png")


@pytest.mark.e2e
def test_options_popover_open_close_and_controls(slim_story, admin_token: str, page: Page) -> None:
    """低频控件全部收进「选项」popover：默认关闭，打开含全套，backdrop 关闭。"""
    _open_story(slim_story, admin_token, page)

    # 默认关闭：popover 与 backdrop 均不渲染
    assert page.locator(".task-opts-popover").count() == 0
    assert page.locator(".task-opts-wrap .status-menu-backdrop").count() == 0

    # 点「选项」打开
    page.locator(".task-opts-btn").click()
    page.wait_for_selector(".task-opts-popover", timeout=5000)
    assert page.locator(".task-opts-popover").is_visible()

    # 全套控件：只看我 / 密度 / 排序 / 分组 / 筛选预设 / 导出
    row_labels = page.locator(".task-opts-popover .task-opts-row-label").all_inner_texts()
    for label in ("只看我", "密度", "排序", "分组", "导出"):
        assert label in row_labels, f"popover 应含「{label}」行, 实际 {row_labels}"
    assert page.locator(".task-opts-popover .task-opts-section-title", has_text="筛选预设").count() == 1
    assert page.locator(".task-opts-popover .preset-panel--inline").count() == 1, (
        "筛选预设面板应以 inline 形式内嵌在 popover"
    )
    # 密度按钮迁入 popover（列表视图下可用）
    assert page.locator(".task-opts-popover #densityToggle").count() == 1
    # 导出按钮（旧 export-menu details 已删）
    export_btns = page.locator(".task-opts-popover .task-opts-export button").all_inner_texts()
    assert "CSV" in export_btns and "JSON" in export_btns, f"导出应有 CSV/JSON, 实际 {export_btns}"
    # 旧 export-menu 不复存在
    assert page.locator("details.export-menu").count() == 0
    _shot(page, "v73_options_popover.png")

    # 点 backdrop 关闭
    page.locator(".task-opts-wrap .status-menu-backdrop").click()
    time.sleep(0.3)
    assert page.locator(".task-opts-popover").count() == 0, "点 backdrop 应关闭 popover"


@pytest.mark.e2e
def test_zero_count_status_chips_hidden(slim_story, admin_token: str, page: Page) -> None:
    """零计数状态 chip 不渲染：只有 todo/in_progress/done 非零（含自动子任务）→ 4 chip。"""
    _open_story(slim_story, admin_token, page)

    chips = page.locator(".filterbar--inline .chips .chip").all_inner_texts()
    chips_clean = [c.strip() for c in chips]
    assert len(chips_clean) == 4, (
        f"应渲染 4 个 chip（全部 + 待办/进行中/完成）, 实际 {chips_clean}"
    )
    assert chips_clean[0].startswith("全部"), f"第一个 chip 应是「全部」, 实际 {chips_clean[0]!r}"
    for s in ("待办", "进行中", "完成"):
        assert any(s in c for c in chips_clean), f"非零状态 chip「{s}」应渲染, 实际 {chips_clean}"
    for s in ("评审中", "已阻塞"):
        assert not any(s in c for c in chips_clean), (
            f"零计数且未激活的 chip「{s}」不应渲染, 实际 {chips_clean}"
        )

    # 轻量筛选行：仅搜索框 + chips，无卡片背景装饰（inline 语义由 CSS 承载）
    assert page.locator(".filterbar--inline input[aria-label='搜索任务']").count() == 1
    _shot(page, "v73_zero_count_chips.png")


@pytest.mark.e2e
def test_inline_noise_reduction(slim_story, admin_token: str, page: Page) -> None:
    """行内降噪：无 due 不渲染 due-pill、无 assignee 不渲染 assignee-pill。"""
    _open_story(slim_story, admin_token, page)

    # v73-待办：无 due 无 assignee → 两种 pill 都不渲染（旧版有「⏰ 设截止」「?」占位）
    row_todo = _row(page, "v73-待办")
    assert row_todo.locator(".due-pill").count() == 0, "无截止日期的行不应渲染 due-pill 占位"
    assert row_todo.locator(".assignee-pill").count() == 0, "未指派的行不应渲染 assignee-pill 占位"
    row_todo_txt = row_todo.inner_text()
    assert "设截止" not in row_todo_txt, "「⏰ 设截止」占位文案不应出现"

    # v73-已完成：指派给 admin → 有 assignee-pill；无 due → 无 due-pill
    row_done = _row(page, "v73-已完成")
    assert row_done.locator(".assignee-pill").count() == 1
    assert row_done.locator(".due-pill").count() == 0

    # v73-进行中：有 due_date → 有 due-pill；未指派 → 无 assignee-pill
    row_prog = _row(page, "v73-进行中")
    assert row_prog.locator(".due-pill").count() == 1
    assert row_prog.locator(".assignee-pill").count() == 0
    _shot(page, "v73_inline_noise_reduction.png")


@pytest.mark.e2e
def test_options_popover_density_and_active_dot(slim_story, admin_token: str, page: Page) -> None:
    """popover 内密度切换生效（density-compact class）+ 选项按钮出现活动小圆点。"""
    _open_story(slim_story, admin_token, page)

    # 默认舒适密度（e2e 用全新 browser context，无 localStorage 残留）
    assert page.locator(".entity-list.density-compact").count() == 0

    # 打开 popover 切紧凑
    page.locator(".task-opts-btn").click()
    page.wait_for_selector(".task-opts-popover", timeout=5000)
    page.locator(".task-opts-popover #densityToggle").click()
    time.sleep(0.3)
    assert page.locator(".entity-list.density-compact").count() == 1, (
        "点密度切换后 .entity-list 应加 density-compact"
    )
    _shot(page, "v73_density_compact.png")

    # 关闭 popover：非默认选项已改 → 选项按钮显示活动小圆点
    page.locator(".task-opts-wrap .status-menu-backdrop").click()
    time.sleep(0.3)
    assert page.locator(".task-opts-btn .task-opts-dot").count() == 1, (
        "改过非默认选项后, 选项按钮应显示 .task-opts-dot 提示"
    )


@pytest.mark.e2e
def test_checkbox_and_kbd_hint_reveal(slim_story, admin_token: str, page: Page) -> None:
    """降噪显隐：批量勾选框默认 opacity:0；kbd-hint 默认不占屏。"""
    _open_story(slim_story, admin_token, page)

    checkbox_op = page.evaluate("""
        (() => {
            const el = document.querySelector('.entity-item--rich .task-checkbox');
            return el ? getComputedStyle(el).opacity : 'ABSENT';
        })()
    """)
    assert checkbox_op == "0", (
        f"批量勾选框默认应 opacity:0（hover/键盘焦点才显）, 实际 {checkbox_op!r}"
    )

    kbd_display = page.evaluate("""
        (() => {
            const el = document.querySelector('.kbd-hint');
            return el ? getComputedStyle(el).display : 'ABSENT';
        })()
    """)
    assert kbd_display == "none", (
        f"kbd-hint 默认应 display:none（列表聚焦才显）, 实际 {kbd_display!r}"
    )
