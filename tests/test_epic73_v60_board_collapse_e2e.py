"""Epic 73 v6.0 — 看板视图列全折叠/全展开 端到端验证.

验证：
  1. 看板视图下工具栏出现「全折叠/全展开」按钮（仅看板模式可见）
  2. 点击「全折叠」→ 7 个状态列全部折叠（.kanban-col.collapsed）
  3. 按钮文案切换为「全展开」
  4. 点击「全展开」→ 全部展开
  5. 折叠状态持久化（localStorage agentboard_collapsed_cols），刷新后仍折叠
  6. 全程 0 pageerror / 0 console error / 0 .js+.css 404
"""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8090"
TOKEN = "v1.18.1785402279.aeb931bf1c8e6e3815c6215ee4bcd627d79bf94ea2eab4ccbd0dc9da1e324c93"
SEED_STORY = 112  # AUTODEV73 / Story 73.1，含 3 个 seed 任务

def main():
    errors, failed = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                 if m.type in ("error", "warning") else None)
        page.on("requestfailed", lambda r: failed.append(r.url)
                 if r.url.endswith((".js", ".css")) else None)
        page.on("response", lambda r: failed.append(r.url)
                 if r.status >= 400 and r.url.endswith((".js", ".css")) else None)

        page.add_init_script(
            f"localStorage.setItem('agentboard_token','{TOKEN}');"
            f"localStorage.setItem('agentboard_story_view','board');"
        )

        page.goto(f"{BASE}/story/{SEED_STORY}", wait_until="domcontentloaded")
        page.wait_for_selector(".kanban-col", timeout=20000)

        cols = page.query_selector_all(".kanban-col")
        assert len(cols) == 7, f"期望 7 个状态列, 实际 {len(cols)}"

        # 看板专属按钮可见
        toggle = page.wait_for_selector("#boardColsToggle", timeout=10000)
        assert toggle.is_visible(), "看板列全折叠/全展开按钮应可见"

        label0 = page.text_content("#boardColsToggle")
        assert "全折叠" in label0, f"初始应为『全折叠』, 实际『{label0}』"

        collapsed_before = page.eval_on_selector_all(
            ".kanban-col", "els => els.filter(e => e.classList.contains('collapsed')).length")
        assert collapsed_before == 0, f"初始应无折叠列, 实际 {collapsed_before}"

        # 点击 全折叠
        page.click("#boardColsToggle")
        page.wait_for_timeout(350)
        collapsed_after = page.eval_on_selector_all(
            ".kanban-col", "els => els.filter(e => e.classList.contains('collapsed')).length")
        assert collapsed_after == 7, f"点击全折叠后应 7 列全折叠, 实际 {collapsed_after}"

        label1 = page.text_content("#boardColsToggle")
        assert "全展开" in label1, f"折叠后应为『全展开』, 实际『{label1}』"

        # 点击 全展开
        page.click("#boardColsToggle")
        page.wait_for_timeout(350)
        collapsed_final = page.eval_on_selector_all(
            ".kanban-col", "els => els.filter(e => e.classList.contains('collapsed')).length")
        assert collapsed_final == 0, f"点击全展开后应 0 折叠, 实际 {collapsed_final}"

        label2 = page.text_content("#boardColsToggle")
        assert "全折叠" in label2, f"展开后应为『全折叠』, 实际『{label2}』"

        # 持久化：再折叠 → 刷新 → 仍折叠
        page.click("#boardColsToggle")
        page.wait_for_timeout(250)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".kanban-col", timeout=20000)
        collapsed_persist = page.eval_on_selector_all(
            ".kanban-col", "els => els.filter(e => e.classList.contains('collapsed')).length")
        assert collapsed_persist == 7, f"刷新后应持久化 7 列折叠, 实际 {collapsed_persist}"

        page.screenshot(path="v60_board_collapse.png")
        browser.close()

    real = [e for e in errors if "favicon" not in e]
    if real:
        print("CONSOLE/PAGE ERRORS:")
        for e in real:
            print("  ", e)
        sys.exit(1)
    if failed:
        print("FAILED .js/.css REQUESTS:")
        for u in failed:
            print("  ", u)
        sys.exit(1)
    print("ALL PASS — v6.0 看板列全折叠/全展开 验证通过")


if __name__ == "__main__":
    main()
