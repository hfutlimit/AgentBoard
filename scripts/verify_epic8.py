"""Epic 8 端到端验证 v4：登录 -> /story/19 -> 看板/筛选/抽屉/通知/快捷键。"""
import os
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8089/"
OUT = "screenshots/epic8_verify"
os.makedirs(OUT, exist_ok=True)

def counts(pg, sels):
    return pg.evaluate("""(sels) => { const o={}; for(const s of sels) o[s]=document.querySelectorAll(s).length; return o; }""", sels)

def try_click(pg, selectors, wait=1200):
    for sel in selectors:
        el = pg.query_selector(sel)
        if el and el.is_visible():
            el.click()
            pg.wait_for_timeout(wait)
            return sel
    return None

def main():
    errors, console, failed = [], [], []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        pg.on("requestfailed", lambda r: failed.append(f"FAIL {r.url} {r.failure}"))
        pg.on("response", lambda r: failed.append(f"{r.status} {r.url}") if r.status >= 400 else None)

        pg.goto(BASE, wait_until="networkidle", timeout=30000)
        pg.wait_for_timeout(1500)
        pg.get_by_placeholder("请输入用户名").fill("test_verify")
        pg.get_by_placeholder("请输入密码").fill("Verify1234!")
        pg.click("button.login-submit")
        pg.wait_for_timeout(3000)
        print("URL after login:", pg.url)
        pg.screenshot(path=f"{OUT}/01-after-login.png")

        # 进入 Story 19（Epic 8 的第一个 Story：看板卡片优先级可视化）
        pg.goto(BASE + "story/19", wait_until="domcontentloaded", timeout=20000)
        pg.wait_for_timeout(3000)
        print("URL on story/19:", pg.url)
        pg.screenshot(path=f"{OUT}/02-story19-list.png")

        all_sels = [".kanban-card", ".kanban-card--pri-highest", ".kanban-card--pri-high",
                    ".kanban-card--pri-medium", ".kanban-card--pri-low", ".kanban-card--pri-lowest",
                    ".kanban-card-progress", ".filter-panel", ".filter-chip", ".filter-btn",
                    ".quick-actions", ".notif-group", ".notif-group-actions",
                    ".shortcuts-note", "kbd"]
        c_list = counts(pg, all_sels)
        print("COUNTS(list view):", c_list)

        # 切到看板
        clicked = try_click(pg, ["button.seg-btn:has-text('看板')", ".seg-btn:has-text('看板')", "button:has-text('看板')"], wait=2500)
        print("clicked board:", clicked)
        pg.screenshot(path=f"{OUT}/03-story19-board.png")
        c_board = counts(pg, all_sels)
        print("COUNTS(board view):", c_board)

        # 打开筛选面板
        try_click(pg, ["button:has-text('筛选')", "button:has-text('⚙')", "button[title='筛选']"], wait=1500)
        pg.screenshot(path=f"{OUT}/04-story19-filter.png")
        c_filter = counts(pg, all_sels)
        print("COUNTS(filter open):", c_filter)

        # 点击第一个看板卡片 -> 抽屉
        card = pg.query_selector(".kanban-card")
        if card:
            card.click(); pg.wait_for_timeout(1800)
            pg.screenshot(path=f"{OUT}/05-task-drawer.png")
            c_drawer = counts(pg, all_sels)
            print("COUNTS(drawer):", c_drawer)

        # 通知铃铛
        try_click(pg, ["button.topbar-notif", ".notif-btn", "button:has-text('通知')", ".bell", "button[title*='通知']"], wait=1500)
        pg.screenshot(path=f"{OUT}/06-notif.png")
        c_notif = counts(pg, all_sels)
        print("COUNTS(notif):", c_notif)

        # 快捷键帮助（顶部 ? 按钮）
        try_click(pg, ["button.topbar-help", "button:has-text('?')", "button[title*='快捷键']", "button[title*='帮助']"], wait=1500)
        pg.screenshot(path=f"{OUT}/07-shortcuts.png")
        c_short = counts(pg, all_sels)
        print("COUNTS(shortcuts):", c_short)

        print("PAGE_ERRORS:", errors)
        print("CONSOLE_ERR/WARN:", console[:20])
        print("FAILED/4xx:", failed[:20])
        b.close()
    print("DONE")

if __name__ == "__main__":
    main()
