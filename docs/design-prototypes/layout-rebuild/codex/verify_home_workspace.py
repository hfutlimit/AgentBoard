from pathlib import Path

from playwright.sync_api import Page, sync_playwright


ROOT = Path(__file__).resolve().parent
PROTOTYPE = ROOT / "agentboard-home-workspace.html"
SHOTS = ROOT / "home-workspace-shots"


def capture(page: Page, name: str, width: int, height: int) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.wait_for_timeout(260)
    output = SHOTS / name
    page.screenshot(path=str(output), full_page=False)
    print(f"saved {name} ({output.stat().st_size} bytes)")


def assert_no_page_overflow(page: Page) -> None:
    metrics = page.evaluate(
        """() => ({
          viewport: document.documentElement.clientWidth,
          page: document.documentElement.scrollWidth
        })"""
    )
    assert metrics["page"] <= metrics["viewport"], metrics


def main() -> None:
    assert PROTOTYPE.is_file(), f"Home/workspace prototype missing: {PROTOTYPE}"
    SHOTS.mkdir(parents=True, exist_ok=True)
    for old_shot in SHOTS.glob("*.png"):
        old_shot.unlink()

    console_errors: list[str] = []
    page_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(PROTOTYPE.as_uri(), wait_until="load")

        # Home is a global, sidebar-free space with two top-level destinations.
        assert page.locator("#homeShell").is_visible()
        assert not page.locator("#projectWorkspace").is_visible()
        assert not page.locator("#projectSidebar").is_visible()
        assert page.locator(".home-nav-button").count() == 2
        assert page.locator(".project-master-row").count() == 4
        assert page.locator("#projectDetailTitle").inner_text() == "AgentBoard 平台"
        font_size = page.locator("body").evaluate(
            "element => parseFloat(getComputedStyle(element).fontSize)"
        )
        assert font_size >= 14
        capture(page, "01-home-projects-1440.png", 1440, 900)

        page.locator('.project-master-row[data-project="knowledge"]').click()
        assert page.locator("#projectDetailTitle").inner_text() == "KnowledgeVault"

        # My Agents is intentionally read-only and derives from the user's keys.
        page.locator('.home-nav-button[data-home-route="agents"]').click()
        assert page.locator('[data-home-view="agents"]').is_visible()
        assert page.locator(".agent-row").count() == 4
        assert page.locator("[data-agent-mutation]").count() == 0
        assert page.locator(".readonly-badge").is_visible()
        capture(page, "02-home-agents-1440.png", 1440, 900)

        # Entering one project changes the information architecture.
        page.locator('.home-nav-button[data-home-route="projects"]').click()
        page.locator('.project-master-row[data-project="agentboard"]').click()
        page.locator("#enterWorkspaceButton").click()
        assert not page.locator("#homeShell").is_visible()
        assert page.locator("#projectWorkspace").is_visible()
        assert page.locator("#projectSidebar").is_visible()
        assert page.locator(".project-nav-button").count() == 8
        assert page.locator('[data-workspace-view="overview"]').is_visible()
        capture(page, "03-workspace-overview-1440.png", 1440, 900)

        page.locator('.project-nav-button[data-workspace-route="kanban"]').click()
        assert page.locator('[data-workspace-view="kanban"]').is_visible()
        assert page.locator(".kanban-column").count() == 4
        capture(page, "04-workspace-kanban-1440.png", 1440, 900)

        page.locator('.project-nav-button[data-workspace-route="epics"]').click()
        assert page.locator('[data-workspace-view="epics"]').is_visible()
        assert page.locator('[data-list-key="epics"] .list-toolbar').is_visible()
        assert page.locator('[data-list-key="epics"] .epic-row:visible').count() == 5
        capture(page, "05-workspace-epics-1440.png", 1440, 900)

        # Every table-style project page shares filtering and pagination behavior.
        for route in ("epics", "workitems", "proposals", "documents", "members"):
            page.locator(
                f'.project-nav-button[data-workspace-route="{route}"]'
            ).click()
            managed_list = page.locator(f'[data-list-key="{route}"]')
            assert managed_list.locator(".list-toolbar").is_visible()
            assert managed_list.locator(".list-search-input").is_visible()
            assert managed_list.locator(".list-filter-select").is_visible()
            assert managed_list.locator(".list-pagination").is_visible()

        work_items = page.locator('[data-list-key="workitems"]')
        page.locator('.project-nav-button[data-workspace-route="workitems"]').click()
        assert work_items.locator(".page-summary").inner_text() == "1–5 / 共 12 项"
        first_page_title = work_items.locator(".list-data-row .simple-title").first.inner_text()
        work_items.locator(".page-next").click()
        assert work_items.locator(".page-summary").inner_text() == "6–10 / 共 12 项"
        assert (
            work_items.locator(".list-data-row .simple-title").first.inner_text()
            != first_page_title
        )

        work_items.locator(".list-search-input").fill("Worker")
        assert work_items.locator(".page-summary").inner_text() == "1–4 / 共 4 项"
        work_items.locator(".list-filter-select").select_option("进行中")
        assert work_items.locator(".page-summary").inner_text() == "1–2 / 共 2 项"
        assert work_items.locator(".active-filter-row").is_visible()
        work_items.locator(".advanced-filter-button").click()
        assert (
            work_items.locator(".advanced-filter-popover").get_attribute("aria-hidden")
            == "false"
        )
        capture(page, "09-workitems-filtered-1440.png", 1440, 900)
        work_items.locator(".advanced-filter-button").click()
        work_items.locator(".clear-list-filters").click()
        assert work_items.locator(".page-summary").inner_text() == "1–5 / 共 12 项"

        page.locator('.project-nav-button[data-workspace-route="settings"]').click()
        assert page.locator('[data-workspace-view="settings"]').is_visible()
        assert page.locator(".settings-section").count() >= 2
        capture(page, "06-workspace-settings-1440.png", 1440, 900)

        # Project switching and project creation live in a compact top-bar popover.
        page.locator("#projectSwitcherButton").click()
        assert page.locator("#projectSwitcher").get_attribute("aria-hidden") == "false"
        assert page.locator(".switcher-project").count() == 4
        assert page.locator("#addProjectButton").is_visible()
        page.locator('.switcher-project[data-project="knowledge"]').click()
        assert page.locator("#workspaceProjectName").inner_text() == "KnowledgeVault"
        page.locator("#projectSwitcherButton").click()
        capture(page, "07-project-switcher-1440.png", 1440, 900)
        page.locator("#addProjectButton").click()
        assert page.locator("#createProjectPanel").get_attribute("aria-hidden") == "false"
        page.locator("#createProjectClose").click()

        page.locator("#notificationButton").click()
        assert page.locator("#notificationPanel").get_attribute("aria-hidden") == "false"
        page.locator("#notificationButton").click()

        # Compact desktop remains full-width; Kanban owns its own horizontal scroll.
        page.locator('.project-nav-button[data-workspace-route="kanban"]').click()
        page.set_viewport_size({"width": 1024, "height": 760})
        page.wait_for_timeout(260)
        assert_no_page_overflow(page)
        sidebar = page.locator("#projectSidebar").bounding_box()
        assert sidebar is not None and sidebar["width"] <= 220
        capture(page, "08-workspace-compact-1024.png", 1024, 760)
        browser.close()

    assert not page_errors, f"page errors: {page_errors}"
    assert not console_errors, f"console errors: {console_errors}"
    screenshots = sorted(SHOTS.glob("*.png"))
    assert len(screenshots) == 9, f"expected 9 screenshots, found {len(screenshots)}"
    assert all(path.stat().st_size > 10_000 for path in screenshots)
    print("ALL HOME / WORKSPACE CHECKS PASSED")


if __name__ == "__main__":
    main()
