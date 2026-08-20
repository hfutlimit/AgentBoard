r"""Epic 150 整体 review：截所有 view 渲染图 + 关键元素检查 + 视觉对比 prototype。

跑法：cd D:\AI\Projects\AgentBoard; .venv\Scripts\python.exe tests/e2e_epic149/test_review_all_views.py

输出：tests/e2e_epic149/screenshots/_review_*.png（每个 view 一张）
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pytest
try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:  # pragma: no cover - collected without E2E extras
    sync_playwright = None

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

FRONTEND_ORIGIN = "http://127.0.0.1:4200"
PROD_API = "http://124.220.44.12"
ADMIN_USER = os.environ.get("AGENTBOARD_E2E_USER", "admin")
ADMIN_PASS = os.environ.get("AGENTBOARD_E2E_PASS", "admin123")
ROOT = Path(__file__).resolve().parent
SHOT_DIR = ROOT / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


def login() -> str:
    req = urllib.request.Request(
        PROD_API + "/api/auth/login",
        data=json.dumps({"username": ADMIN_USER, "password": ADMIN_PASS}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["token"]


# Review 列表：(path_or_steps, label, screenshot_name, prototype_compare, mode)
# mode: "goto" = page.goto, "click" = (label, project_id), "home" = 直接 /, "tab" = (label, subtab)
REVIEW_VIEWS = [
    ({"mode": "home"}, "home (X1)", "_review_01_home.png", "01-home-projects-1440.png"),
    ({"mode": "home", "tab": "agents"}, "home + agents tab", "_review_02_home_agents.png", "02-home-agents-1440.png"),
    ({"mode": "goto", "path": "/projects"}, "projects (X3 list)", "_review_03_projects.png", None),
    ({"mode": "goto", "path": "/notifications"}, "notifications (X3 list)", "_review_04_notifications.png", None),
    ({"mode": "goto", "path": "/settings"}, "settings (X2 PR3 pilot)", "_review_05_settings.png", None),
    ({"mode": "goto", "path": "/project/3"}, "project view top (X3 detail)", "_review_06_project.png", None),
    ({"mode": "goto", "path": "/project/3", "tab": "kanban"}, "project + kanban tab", "_review_07_kanban.png", "04-workspace-kanban-1440.png"),
    ({"mode": "goto", "path": "/project/3", "tab": "epics"}, "project + epics tab", "_review_08_epics.png", "05-workspace-epics-1440.png"),
    ({"mode": "goto", "path": "/project/3", "tab": "backlog"}, "project + backlog tab", "_review_09_backlog.png", "09-workitems-filtered-1440.png"),
    ({"mode": "goto", "path": "/project/3", "tab": "settings"}, "project + settings tab", "_review_10_project_settings.png", "06-workspace-settings-1440.png"),
]


def main() -> int:
    failures = []
    token = login()
    print(">>> got token len=%d" % len(token))
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            page.add_init_script(
                "localStorage.setItem('agentboard_token', %s);"
                "localStorage.setItem('agentboard_user', 'admin');" % json.dumps(token)
            )

            for entry in REVIEW_VIEWS:
                opts, label, shot, prototype = entry
                mode = opts.get("mode", "goto")
                try:
                    if mode == "home":
                        page.goto(f"{FRONTEND_ORIGIN}/", wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_selector("app-home-shell .home-shell-v7", timeout=15000)
                    elif mode == "goto":
                        page.goto(f"{FRONTEND_ORIGIN}{opts['path']}", wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_selector("app-workspace-topbar .back-button-v7, app-workspace-heading .workspace-heading-v7, app-home-shell .home-shell-v7, aside.project-sidebar-v7", timeout=15000)

                    time.sleep(0.6)

                    if opts.get("tab"):
                        # click project sidebar tab
                        tab_label = opts["tab"]
                        # 'kanban' → '看板', 'epics' → 'Epics', 'backlog' → '工作项', 'settings' → '设置'
                        label_map = {"kanban": "看板", "epics": "Epics", "backlog": "工作项", "settings": "设置", "members": "成员与 Agents", "overview": "概览", "documents": "文档", "proposals": "提案", "tickets": "Tickets", "stats": "统计"}
                        nav_label = label_map.get(tab_label, tab_label)
                        if mode == "home" and tab_label == "agents":
                            # home shell 内部 tab
                            tab_btn = page.locator("app-home-shell .hs-tab-button:has-text('Agents')").first
                            tab_btn.click()
                        else:
                            tab_btn = page.locator(f"aside.project-sidebar-v7 .project-nav-button-v7:has-text('{nav_label}')").first
                            tab_btn.click()
                        time.sleep(0.5)

                    page.screenshot(path=str(SHOT_DIR / shot), full_page=False)
                    print(f">>> shot {label} -> {shot}")
                except Exception as e:
                    failures.append(f"{label}: {e!r}")
                    print(f"!!! {label} FAILED: {e!r}")

        except Exception as e:
            failures.append(f"exception: {e!r}")
        finally:
            ctx.close()
            browser.close()

    print()
    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print(f"OK — captured {len(REVIEW_VIEWS)} screenshots in {SHOT_DIR}")
    return 0


@pytest.mark.e2e
@pytest.mark.legacy
@pytest.mark.skip(reason="legacy manual E2E; run this file directly")
def test_review_all_views_legacy() -> None:
    """Collect the legacy screenshot script without running it by default."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
