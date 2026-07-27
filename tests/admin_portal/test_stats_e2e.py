"""Admin Portal 统计页端到端验证 (Task 861: 实现统计页)。

覆盖: 汇总卡片 / 创建-完成双系列柱状图 / 日-周-月聚合切换 / 项目下拉切换。
复用 tests/admin_portal/_harness.py 的统一装配。
"""
import sys

from _harness import BASE, start_browser, login_ui, check_errors, report


def main():
    pw, browser, page, errors, resp_401 = start_browser()
    try:
        login_ui(page)

        # 1) 进入统计页
        page.goto(BASE + "/stats", wait_until="networkidle")
        page.wait_for_selector(".page-head h1", timeout=15000)
        assert "统计" in page.inner_text(".page-head h1"), "统计页标题缺失"

        # 2) 汇总卡片渲染 (5 张)
        cards = page.locator(".grid .card.stat")
        assert cards.count() == 5, f"汇总卡片数应为 5, 实际 {cards.count()}"
        total_text = page.locator(".grid .card.stat .stat-value").first.inner_text()
        assert total_text.strip().isdigit() and int(total_text) > 0, (
            f"任务总数无效: {total_text!r}"
        )

        # 3) 默认趋势图渲染 (全部项目聚合)
        page.wait_for_selector(".bar.created", timeout=20000)
        created_bars = page.locator(".bar.created")
        done_bars = page.locator(".bar.done")
        assert created_bars.count() > 0, "创建柱状图未渲染"
        assert done_bars.count() > 0, "完成柱状图未渲染"
        assert created_bars.count() >= 1, "日期桶数量为 0"

        # 4) 切换聚合维度: 周 / 月
        page.click('.seg button:has-text("周")')
        page.wait_for_timeout(400)
        assert page.locator(".bar.created").count() > 0, "周聚合柱状图为空"
        page.click('.seg button:has-text("月")')
        page.wait_for_timeout(400)
        assert page.locator(".bar.created").count() > 0, "月聚合柱状图为空"
        page.click('.seg button:has-text("日")')
        page.wait_for_timeout(300)

        # 5) 切换具体项目 (id=3) 并刷新图表
        sel = page.locator(".select")
        assert sel.count() == 1, "项目下拉框缺失"
        sel.select_option(value="3")
        page.wait_for_selector(".bar.created", timeout=20000)
        proj_total = page.locator(".grid .card.stat .stat-value").first.inner_text()
        assert proj_total.strip().isdigit() and int(proj_total) > 0, (
            f"项目 3 任务总数无效: {proj_total!r}"
        )
        assert page.locator(".bar.created").count() > 0, "切换项目后柱状图为空"

        # 6) 导航高亮
        assert page.locator('.nav-links a.active:has-text("统计")').count() == 1, "统计导航未高亮"

        problems = check_errors(errors, resp_401, allow_login_401=False)
        ok = report("stats", problems)
        browser.close()
        pw.stop()
        sys.exit(0 if ok else 1)
    except AssertionError as e:
        print("FAILED [stats]:", e)
        browser.close()
        pw.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
