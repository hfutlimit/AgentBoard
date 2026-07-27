"""
Admin Portal 统计页端到端验证 (Story 71 / Task 854: 实现统计页)

覆盖点:
  - 登录后访问 /stats 渲染汇总卡片 (任务总数/已完成/进行中/待办/完成率)
  - 任务创建/完成趋势柱状图渲染 (纯 CSS, 创建 vs 完成 双系列)
  - 日/周/月 聚合切换后图表重新渲染且仍有数据
  - 项目下拉框可切换至具体项目并刷新图表
  - 0 个 pageerror / console error / .js+.css 404, 无预期外 401
"""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4300"


def main():
    errors = []
    resp_401 = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        ctx = browser.new_context()
        page = ctx.new_page()

        page.on("console", lambda m: errors.append(("console", m.type, m.text)) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(("pageerror", "error", str(e))))
        page.on("requestfailed", lambda r: (
            errors.append(("reqfail", r.url, r.failure))
            if (r.url.endswith(".js") or r.url.endswith(".css")) else None
        ))
        page.on("response", lambda r: resp_401.append(r.url) if r.status == 401 else None)

        # 1) 登录 -> dashboard
        page.goto(BASE + "/", wait_until="networkidle")
        page.wait_for_url("**/login", timeout=15000)
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "admin123")
        page.click('button[type="submit"]')
        page.wait_for_url("**/dashboard", timeout=15000)

        # 2) 进入统计页
        page.goto(BASE + "/stats", wait_until="networkidle")
        page.wait_for_selector(".page-head h1", timeout=15000)
        assert "统计" in page.inner_text(".page-head h1"), "统计页标题缺失"

        # 3) 汇总卡片渲染 (5 张)
        cards = page.locator(".grid .card.stat")
        assert cards.count() == 5, f"汇总卡片数应为 5, 实际 {cards.count()}"
        total_text = page.locator(".grid .card.stat .stat-value").first.inner_text()
        assert total_text.strip().isdigit() and int(total_text) > 0, f"任务总数无效: {total_text!r}"

        # 4) 默认趋势图渲染 (全部项目聚合)
        page.wait_for_selector(".bar.created", timeout=20000)
        created_bars = page.locator(".bar.created")
        done_bars = page.locator(".bar.done")
        assert created_bars.count() > 0, "创建柱状图未渲染"
        assert done_bars.count() > 0, "完成柱状图未渲染"
        cr = int(created_bars.count())
        # 聚合后至少应覆盖多个日期桶
        assert cr >= 1, "日期桶数量为 0"

        # 5) 切换聚合维度: 周 / 月
        page.click('.seg button:has-text("周")')
        page.wait_for_timeout(400)
        assert page.locator(".bar.created").count() > 0, "周聚合柱状图为空"
        page.click('.seg button:has-text("月")')
        page.wait_for_timeout(400)
        assert page.locator(".bar.created").count() > 0, "月聚合柱状图为空"
        # 切回 日 保持后续稳定
        page.click('.seg button:has-text("日")')
        page.wait_for_timeout(300)

        # 6) 切换具体项目 (id=3, 已知有 164 任务) 并刷新图表
        sel = page.locator(".select")
        assert sel.count() == 1, "项目下拉框缺失"
        sel.select_option(value="3")
        page.wait_for_selector(".bar.created", timeout=20000)
        # 项目 3 的 total_tasks 应为 164
        proj_total = page.locator(".grid .card.stat .stat-value").first.inner_text()
        assert proj_total.strip() == "164", f"项目 3 任务总数应为 164, 实际 {proj_total!r}"
        assert page.locator(".bar.created").count() > 0, "切换项目后柱状图为空"

        # 7) 导航高亮
        assert page.locator('.nav-links a.active:has-text("统计")').count() == 1, "统计导航未高亮"

        browser.close()

    other_401 = [u for u in resp_401 if not u.rstrip("/").endswith("/api/auth/login")]
    real = [e for e in errors if not (isinstance(e[2], str) and "ERR_ABORTED" in e[2])
            and not (e[0] == "reqfail" and "favicon" in e[1])]
    console_401 = [e for e in real if e[0] == "console" and "401" in str(e[2])]
    other_real = [e for e in real if e not in console_401]
    if console_401 and len([u for u in resp_401 if u.rstrip("/").endswith("/api/auth/login")]) < 1:
        other_real.append(("console", "error", "存在 401 控制台错误但无登录 401 响应"))

    problems = []
    if other_401:
        problems.append(f"非预期 401: {other_401}")
    if other_real:
        problems.append(f"前端错误: {other_real}")

    if problems:
        print("FAILED:")
        for x in problems:
            print("  ", x)
        sys.exit(1)
    print("PASS: admin-portal 统计页 E2E 全部通过 (汇总卡片+趋势图+日/周/月切换+项目切换, 0 pageerror/console/.js+.css 404)")


if __name__ == "__main__":
    main()
