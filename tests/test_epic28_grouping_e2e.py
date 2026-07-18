"""Epic 28 / Story 64 / Task 836：任务列表分组 E2E 验证（真实 Chromium）。

验证 Story 任务列表「分组」下拉（不分组 / 按状态 / 按类型 / 按负责人）：
- 切换后渲染分组标题（.task-group-header）与计数（.task-group-count）；
- 各分组任务条目总数 == 可见任务总数；
- 三种分组模式均出现对应分组（状态 / 类型 / 负责人）；
- 无 pageerror / console error / .js|.css 4xx 失败请求。

复用 tests/test_playwright_e2e.py 的 servers / page fixture（真实 API + Web + 临时 SQLite）。
数据（Project/Epic/Story/Task）全部经 API 创建；任务列表经 URL 直达 /story/{sid} 触发
loadStory，reload 后重新拉取，规避侧栏「recent」依赖与登录 UI 重构。
运行：PYTHONPATH=. python -m pytest tests/test_epic28_grouping_e2e.py -q
"""
import json
import time

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")
import httpx

from tests.test_playwright_e2e import servers, page  # noqa: E402

STATUS_ORDER = ["backlog", "todo", "in_progress", "in_review", "verifying", "done"]


def _set_status(api_base: str, h: dict, tid: int, target: str) -> None:
    t = httpx.get(f"{api_base}/api/tasks/{tid}", headers=h, timeout=8).json()
    cur = t["status"]
    start = STATUS_ORDER.index(cur)
    end = STATUS_ORDER.index(target)
    for s in STATUS_ORDER[start + 1:end + 1]:
        httpx.put(f"{api_base}/api/tasks/{tid}/status", headers=h,
                  json={"status": s}, timeout=8)


def _create_task(api_base: str, h: dict, pid: int, sid: int,
                 title: str, status: str, ty: str, assignee: int | None) -> int:
    r = httpx.post(f"{api_base}/api/stories/{sid}/tasks", headers=h, timeout=8,
                   json={"project_id": pid, "type": ty, "title": title,
                         "priority": "medium"})
    r.raise_for_status()
    tid = r.json()["id"]
    # 显式设置负责人（None -> null 清空，避免默认继承创建者）
    httpx.patch(f"{api_base}/api/tasks/{tid}", headers=h, timeout=8,
                 json={"assignee_id": assignee})
    if status != "backlog":
        _set_status(api_base, h, tid, status)
    return tid


def test_e2e_task_grouping(page, servers):
    """真实浏览器：分组下拉三种模式渲染正确、计数一致、零错误。"""
    api_base, web_base = servers
    ts = str(int(time.time()))
    user = "grp" + ts
    # 1) 通过 API 注册 + 登录拿 token（绕过登录 UI 重构）
    httpx.post(f"{api_base}/api/auth/register", timeout=8,
               json={"username": user, "password": "secret123",
                     "email": user + "@ab.io"}).raise_for_status()
    tok = httpx.post(f"{api_base}/api/auth/login", timeout=8,
                     json={"username": user, "password": "secret123"}).json()["token"]
    assert tok, "登录后未返回 token"
    h = {"Authorization": f"Bearer {tok}"}

    # 2) 取当前用户 id（用于按负责人分组）
    uid = httpx.get(f"{api_base}/api/auth/me", headers=h, timeout=8).json()["id"]

    # 3) 经 API 建 Project -> Epic -> Story
    pid = httpx.post(f"{api_base}/api/projects", headers=h, timeout=8,
                      json={"name": "GrpProj" + ts}).json()["id"]
    eid = httpx.post(f"{api_base}/api/projects/{pid}/epics", headers=h, timeout=8,
                       json={"title": "GE" + ts}).json()["id"]
    sid = httpx.post(f"{api_base}/api/epics/{eid}/stories", headers=h, timeout=8,
                       json={"title": "GS" + ts}).json()["id"]

    # 4) 注入 token，绕过登录 UI
    page.context.add_init_script(
        "localStorage.setItem('agentboard_token', " + json.dumps(tok) + ");")

    # 错误监听（在导航前挂载）
    errors = []
    failed_assets = []

    def _on_console(msg):
        if msg.type == "error":
            errors.append(msg.text)

    def _on_pageerror(exc):
        errors.append(f"pageerror: {exc}")

    def _on_response(resp):
        url = resp.url
        if (url.endswith(".js") or url.endswith(".css")) and resp.status >= 400:
            failed_assets.append(f"{resp.status} {url}")

    page.on("console", _on_console)
    page.on("pageerror", _on_pageerror)
    page.on("response", _on_response)

    # 5) URL 直达 Story 视图（触发 loadStory）
    page.goto(web_base + "/story/" + str(sid))
    page.wait_for_selector("#s-new-task", state="visible", timeout=15000)

    # 6) 经 API 建若干任务（状态 / 类型 / 负责人各异）
    specs = [
        ("G-T1", "todo", "task", uid),
        ("G-T2", "in_progress", "task", None),
        ("G-T3", "done", "bug", uid),
        ("G-T4", "verifying", "task", None),
        ("G-T5", "backlog", "task", uid),
        ("G-T6", "todo", "bug", None),
    ]
    for title, st, ty, asg in specs:
        _create_task(api_base, h, pid, sid, title, st, ty, asg)
    total = len(specs)

    # 7) reload 重新拉取任务
    page.reload()
    page.wait_for_selector("#s-new-task", state="visible", timeout=15000)
    page.wait_for_timeout(500)

    # 8) 默认不分组：标题不应出现
    assert page.locator(".task-group-header").count() == 0, "默认不应有分组标题"

    # 9) 按状态
    page.select_option(".task-group-select", "status")
    page.wait_for_timeout(300)
    headers = page.locator(".task-group-header")
    assert headers.count() >= 2, f"按状态至少 2 个分组，实际 {headers.count()}"
    counts = [int(page.locator(".task-group-count").nth(i).inner_text())
              for i in range(headers.count())]
    assert sum(counts) == total, f"分组计数和 {sum(counts)} != 总任务 {total}"
    page.screenshot(path="scripts/verify_group_status.png")

    # 10) 按类型
    page.select_option(".task-group-select", "type")
    page.wait_for_timeout(300)
    type_labels = page.locator(".task-group-header .task-group-label").all_inner_texts()
    assert "任务" in type_labels and "Bug" in type_labels, f"按类型缺分组：{type_labels}"

    # 11) 按负责人
    # DEBUG: 直接从浏览器上下文拉取 API 列表，确认 SPA 实际加载的 assignee_id
    api_list = page.evaluate(
        """async (sid) => {
            const base = window.AGENTBOARD_API || '';
            const tok = localStorage.getItem('agentboard_token');
            const r = await fetch(base + '/api/stories/' + sid + '/tasks',
                {headers: {Authorization: 'Bearer ' + tok}});
            const data = await r.json();
            return data.map(t => ({id: t.id, title: t.title, assignee_id: t.assignee_id}));
        }""", sid)
    print("DEBUG api list from browser:", api_list)
    page.select_option(".task-group-select", "assignee")
    page.wait_for_timeout(300)
    assign_labels = page.locator(".task-group-header .task-group-label").all_inner_texts()
    assert "未指派" in assign_labels, f"按负责人缺未指派：{assign_labels}"
    assert any(lbl != "未指派" for lbl in assign_labels), f"按负责人缺具名：{assign_labels}"
    page.screenshot(path="scripts/verify_group_assignee.png")

    # 12) 切回不分组
    page.select_option(".task-group-select", "none")
    page.wait_for_timeout(200)
    assert page.locator(".task-group-header").count() == 0, "切回不分组应无标题"

    # 13) 错误断言（仅计 .js|.css 4xx 与 page/console error）
    assert not failed_assets, f"静态资源 4xx：{failed_assets}"
    assert not errors, f"控制台/页面错误：{errors}"
