"""项目工作台实体 Tab E2E（Epic / Proposal / Story / Task 内嵌详情）。

普通左键在应用工作台内打开并复用实体 Tab；链接保留真实 href，因此
Ctrl/Cmd/中键仍由浏览器原生打开新标签页。
"""
from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page
else:
    Page = Any

from conftest import FRONTEND_ORIGIN, SHOT_DIR, goto_url_with_token, log

PROJECT_ID = int(os.environ.get("AGENTBOARD_E2E_PROJECT_ID", "1"))


def _collect_signals(page: Page) -> dict:
    return page.evaluate("""
        ({
            url: location.pathname,
            tabStripCount: document.querySelectorAll('.tab-strip-item').length,
            activeTabKind: document.querySelector(
                '.tab-pane-host:not(.tab-pane-host--hidden) [data-tab-kind]'
            )?.getAttribute('data-tab-kind') || null,
            workspaceVisible: !!document.querySelector('.project-workspace-shell'),
            detailPaneOpen: !!document.querySelector('.detail-pane'),
        })
    """)


def _first_epic(page: Page):
    link = page.locator(
        '.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/project/"][href*="/epics/"]'
    ).first
    if link.count() == 0:
        pytest.skip("no epics seeded in dev DB for this project")
    href = link.get_attribute("href") or ""
    epic_id = int(href.rstrip("/").split("/")[-1])
    return link, epic_id


def _first_story(page: Page):
    page.get_by_role("button", name="📚 Story 列表").click()
    link = page.locator(
        '.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/story/"]'
    ).first
    if link.count() == 0:
        pytest.skip("selected epic has no stories")
    href = link.get_attribute("href") or ""
    story_id = int(href.rstrip("/").split("/")[-1])
    return link, story_id


def _first_task(page: Page):
    page.get_by_role("button", name="📝 Task 列表").click()
    link = page.locator(
        '.tab-pane-host:not(.tab-pane-host--hidden) a[href^="/task/"]'
    ).first
    if link.count() == 0:
        pytest.skip("selected story has no tasks")
    href = link.get_attribute("href") or ""
    task_id = int(href.rstrip("/").split("/")[-1])
    return link, task_id


def _shot(page: Page, name: str) -> None:
    path = SHOT_DIR / name
    page.screenshot(path=str(path), full_page=False)
    log(f"   shot {path.name} size={path.stat().st_size}B")


@pytest.mark.e2e
def test_click_epic_opens_workspace_entity_tab(admin_token: str, page: Page) -> None:
    goto_url_with_token(page, admin_token, f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/epics")
    time.sleep(1.5)
    link, epic_id = _first_epic(page)

    before = _collect_signals(page)
    assert before["tabStripCount"] == 1
    link.click()
    time.sleep(1.0)

    after = _collect_signals(page)
    _shot(page, "epic_workspace_entity_tab.png")
    assert after["url"] == f"/project/{PROJECT_ID}/epics/{epic_id}"
    assert after["tabStripCount"] == 2
    assert after["activeTabKind"] == "epic"
    assert after["workspaceVisible"] is True
    assert after["detailPaneOpen"] is False
    assert page.locator('.tab-strip-item[data-tab-id$="-epics"]').count() == 1
    assert page.locator(f'.tab-strip-item[data-tab-id$="-epic-{epic_id}"]').count() == 1


@pytest.mark.e2e
def test_reopen_same_epic_reuses_existing_tab(admin_token: str, page: Page) -> None:
    goto_url_with_token(page, admin_token, f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/epics")
    time.sleep(1.5)
    link, epic_id = _first_epic(page)
    link.click()
    time.sleep(0.8)

    page.locator('.tab-strip-item[data-tab-id$="-epics"] .tab-strip-link').click()
    time.sleep(0.3)
    link, _ = _first_epic(page)
    link.click()
    time.sleep(0.8)

    after = _collect_signals(page)
    assert after["tabStripCount"] == 2
    assert after["activeTabKind"] == "epic"
    assert page.locator(f'.tab-strip-item[data-tab-id$="-epic-{epic_id}"]').count() == 1


@pytest.mark.e2e
def test_epic_link_keeps_native_new_tab_href(admin_token: str, page: Page) -> None:
    goto_url_with_token(page, admin_token, f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/epics")
    time.sleep(1.5)
    link, epic_id = _first_epic(page)

    assert link.get_attribute("href") == f"/project/{PROJECT_ID}/epics/{epic_id}"


@pytest.mark.e2e
def test_epic_story_task_chain_stays_in_workspace(admin_token: str, page: Page) -> None:
    """Epic → Story → Task all become deduplicated workspace entity tabs."""
    goto_url_with_token(page, admin_token, f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/epics")
    time.sleep(1.5)

    epic_link, epic_id = _first_epic(page)
    epic_link.click()
    time.sleep(0.8)

    story_link, story_id = _first_story(page)
    assert story_link.get_attribute("href") == f"/story/{story_id}"
    story_link.click()
    time.sleep(0.8)
    story_state = _collect_signals(page)
    assert story_state["url"] == f"/project/{PROJECT_ID}/stories/{story_id}"
    assert story_state["activeTabKind"] == "story"
    assert story_state["workspaceVisible"] is True

    task_link, task_id = _first_task(page)
    assert task_link.get_attribute("href") == f"/task/{task_id}"
    task_link.click()
    time.sleep(0.8)
    task_state = _collect_signals(page)
    assert task_state["url"] == f"/project/{PROJECT_ID}/tasks/{task_id}"
    assert task_state["activeTabKind"] == "task"
    assert task_state["tabStripCount"] == 4
    assert page.locator(f'.tab-strip-item[data-tab-id$="-epic-{epic_id}"]').count() == 1
    assert page.locator(f'.tab-strip-item[data-tab-id$="-story-{story_id}"]').count() == 1
    assert page.locator(f'.tab-strip-item[data-tab-id$="-task-{task_id}"]').count() == 1

    page.reload()
    time.sleep(1.0)
    refreshed = _collect_signals(page)
    assert refreshed["url"] == f"/project/{PROJECT_ID}/tasks/{task_id}"
    assert refreshed["activeTabKind"] == "task"
    assert refreshed["workspaceVisible"] is True


@pytest.mark.e2e
def test_sidebar_menu_still_switches_section_tabs(admin_token: str, page: Page) -> None:
    goto_url_with_token(page, admin_token, f"{FRONTEND_ORIGIN}/project/{PROJECT_ID}/epics")
    time.sleep(1.5)

    page.click('a.project-nav-button-v7[aria-label="看板"]')
    time.sleep(0.5)
    after = _collect_signals(page)

    assert after["activeTabKind"] == "kanban"
    assert after["tabStripCount"] == 2
    assert after["url"].endswith("/kanban")
