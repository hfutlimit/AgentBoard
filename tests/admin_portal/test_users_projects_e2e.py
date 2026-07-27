"""Admin Portal 用户管理页 + 项目管理页 端到端验证 (Task 859 / Task 860)。

复用 tests/admin_portal/_harness.py 的统一装配; 通过直连 API(58125) 复核权限切换。
"""
import sys
import requests

from _harness import BASE, start_browser, login_ui, check_errors, report

API = "http://127.0.0.1:58125"


def api_get_user(uid, token):
    data = requests.get(
        API + "/api/admin/users?limit=200",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    items = data.get("items", []) if isinstance(data, dict) else data
    return next((u for u in items if u["id"] == uid), None)


def main():
    pw, browser, page, errors, resp_401 = start_browser()
    try:
        login_ui(page)
        token = page.evaluate("localStorage.getItem('admin_portal_token')")
        assert token and token.strip() and token != "undefined", "登录后未写入 token"

        # 2) 导航到用户管理
        page.click('a[routerlink="/users"]')
        page.wait_for_url("**/users", timeout=15000)
        page.wait_for_selector(".tbl tbody tr", timeout=15000)
        rows = page.locator(".tbl tbody tr")
        n = rows.count()
        assert n >= 1, f"用户表格未渲染行, count={n}"
        head = page.inner_text(".tbl thead")
        for col in ["ID", "用户名", "角色", "创建时间", "操作"]:
            assert col in head, f"用户表缺少列头: {col}"

        # 3) 切换第一个「非管理员」用户的角色, 并经 API 复核
        target_row = None
        target_uid = None
        for i in range(n):
            r = rows.nth(i)
            btn = r.locator("button.btn-toggle")
            if btn.inner_text().strip() == "设为管理员":
                target_row = r
                target_uid = int(r.locator("td").first.inner_text().strip())
                break
        assert target_row is not None, "未找到可切换的非管理员用户"
        assert target_uid is not None

        orig = api_get_user(target_uid, token)
        assert orig is not None and orig["is_admin"] is False, f"预期目标用户为非管理员, got {orig}"

        target_row.locator("button.btn-toggle").click()
        target_row.locator("span.badge-admin").wait_for(state="visible", timeout=10000)
        assert "管理员" in target_row.inner_text(), "切换后 UI 未显示管理员徽章"

        after_on = api_get_user(target_uid, token)
        assert after_on is not None and after_on.get("is_admin") is True, (
            f"API 复核失败: is_admin={after_on.get('is_admin') if after_on else None}"
        )

        # 4) 还原 (取消管理员) 以不污染数据
        target_row.locator("button.btn-toggle").click()
        target_row.locator("span.badge-user").wait_for(state="visible", timeout=10000)
        after_off = api_get_user(target_uid, token)
        assert after_off is not None and after_off.get("is_admin") is False, (
            f"还原失败: is_admin={after_off.get('is_admin') if after_off else None}"
        )

        # 5) 导航到项目管理
        page.click('a[routerlink="/projects"]')
        page.wait_for_url("**/projects", timeout=15000)
        page.wait_for_selector(".tbl tbody tr", timeout=15000)
        prows = page.locator(".tbl tbody tr")
        pn = prows.count()
        assert pn >= 1, f"项目表格未渲染行, count={pn}"
        phead = page.inner_text(".tbl thead")
        for col in ["ID", "名称", "Key", "可见性", "成员"]:
            assert col in phead, f"项目表缺少列头: {col}"

        # 退出登录以干净收尾
        page.click('button.btn-ghost')
        page.wait_for_url("**/login", timeout=15000)

        problems = check_errors(errors, resp_401, allow_login_401=False)
        ok = report("users_projects", problems)
        print(f"   (users={n}, projects={pn})")
        browser.close()
        pw.stop()
        sys.exit(0 if ok else 1)
    except AssertionError as e:
        print("FAILED [users_projects]:", e)
        browser.close()
        pw.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
