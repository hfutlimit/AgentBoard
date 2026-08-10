"""Epic 96 P0-2 · Proposal 问答工作台前端 UI — 真实浏览器 E2E。

补齐 Story 154 的前端部分：P0-1（Task 922）只交付了后端三表 + 状态机 + REST，
本测试证明 Web 端问答闭环可用。

自包含启动真实 API + Web（临时 SQLite，init_db 自动 alembic upgrade head），
用 Chromium 驱动 SPA 验证：

1. 侧栏「需求提案」入口可见并可导航到 /proposals；
2. 空态文案正确渲染；
3. 通过 UI 弹窗创建提案 → 跳转工作台 → 「派发给 Agent」推进 draft→queued；
4. 通过真实 REST 造 analyzing + 一轮 3 个问题（模拟 Agent 回写）；
5. 工作台右栏按轮次渲染问题卡片，左栏渲染正文与澄清进度时间线；
6. 逐条作答 + 勾选「暂不确定」，「一键提交本轮」批量提交；
7. 断言问题全部变为已答、proposal 状态由 awaiting 推进为 answered；
8. 全程 0 console error / 0 pageerror / 无非预期 404；截图留证。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic96_p02_proposal_workbench_e2e.py -q
未安装 playwright / Chromium 时自动 skip。
"""
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest

_HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
_RUN = importlib.util.find_spec("uvicorn") is not None and _HAS_PLAYWRIGHT

# 独立临时数据库（与其它 E2E 隔离，避免串数据）
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(not _RUN, reason="需要 uvicorn + playwright")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(app_import: str, port: int, extra_env: dict | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app_import,
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait(url: str, timeout: float = 30.0) -> None:
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"服务在 {url} 启动超时")


@pytest.fixture(scope="module")
def servers():
    api_port = _free_port()
    web_port = _free_port()
    api_proc = _start_server("agentboard.api:app", api_port)
    web_proc = _start_server(
        "agentboard.web_app:app", web_port,
        {"AGENTBOARD_API_URL": f"http://127.0.0.1:{api_port}"},
    )
    api_base = f"http://127.0.0.1:{api_port}"
    web_base = f"http://127.0.0.1:{web_port}"
    try:
        _wait(api_base + "/api/meta")
        _wait(web_base + "/")
        yield api_base, web_base
    finally:
        for p in (api_proc, web_proc):
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()


@pytest.fixture(scope="module")
def browser():
    if not _HAS_PLAYWRIGHT:
        pytest.skip("playwright 未安装")
    from playwright.sync_api import sync_playwright
    try:
        pw = sync_playwright().start()
        chromium = pw.chromium.launch(headless=True, args=["--no-proxy-server"])
    except Exception as e:
        pytest.skip(f"Chromium 不可用: {e}")
    try:
        yield chromium
    finally:
        try:
            chromium.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 960})
    pg = ctx.new_page()
    errors = []

    def _on_console(m):
        if m.type == "error":
            errors.append(("console", m.text))

    def _on_pageerror(e):
        errors.append(("pageerror", str(e)))

    pg.on("console", _on_console)
    pg.on("pageerror", _on_pageerror)
    pg._errors = errors  # type: ignore[attr-defined]
    try:
        yield pg
    finally:
        pg.close()
        ctx.close()


def _ui_login(page, base: str, username: str, password: str):
    page.goto(base + "/", wait_until="networkidle")
    page.wait_for_selector('input[name="username"]', state="visible", timeout=10000)
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click("button.login-submit")
    page.wait_for_selector("#home-new-project", state="visible", timeout=12000)


def _real_errors(page):
    return [
        e for e in page._errors  # type: ignore[attr-defined]
        if not (isinstance(e[1], str) and ("ERR_ABORTED" in e[1] or "favicon" in e[1].lower()))
    ]


def test_proposal_qa_workbench_e2e(page, servers):
    """端到端：创建提案 → Agent 提问 → 逐条作答 → 一键提交 → 状态推进 answered。"""
    import httpx

    api_base, web_base = servers
    ts = str(int(time.time()))
    user = "e2ewb" + ts
    password = "secret123"

    reg = httpx.post(f"{api_base}/api/auth/register",
                     json={"username": user, "password": password}, timeout=10)
    assert reg.status_code in (200, 201), f"注册应成功: {reg.status_code} {reg.text}"

    _ui_login(page, web_base, user, password)
    token = page.evaluate("localStorage.getItem('agentboard_token')")
    assert token, "登录后应写入 agentboard_token"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    proj = httpx.post(f"{api_base}/api/projects", headers=headers,
                      json={"name": "工作台项目" + ts, "description": "e2e"}, timeout=10)
    assert proj.status_code == 201, f"建项目应 201: {proj.status_code} {proj.text}"
    pid = proj.json()["id"]

    shot_dir = os.path.join(_ROOT, "screenshots")
    os.makedirs(shot_dir, exist_ok=True)

    # ---- 1) 侧栏入口 + 列表空态 ----
    page.wait_for_selector("#nav-proposals", state="visible", timeout=10000)
    page.click("#nav-proposals")
    page.wait_for_selector("#proposal-empty", state="visible", timeout=10000)
    assert "/proposals" in page.url, f"应导航到 /proposals，实际: {page.url}"
    empty_text = page.inner_text("#proposal-empty")
    assert "新建提案" in empty_text, f"空态应给出引导文案，实际: {empty_text}"

    # ---- 2) UI 弹窗创建提案 ----
    page.click("#new-proposal-btn")
    page.wait_for_selector("#proposal-new-title", state="visible", timeout=8000)
    page.select_option("#proposal-project", str(pid))
    page.fill("#proposal-new-title", "澄清回路端到端提案" + ts)
    page.fill("#proposal-new-content", "希望支持批量导出任务为 CSV。\n暂未确定字段范围与权限边界。")
    page.click("#proposal-create-submit")

    # 创建后跳转工作台
    page.wait_for_selector("#proposal-title", state="visible", timeout=12000)
    assert "/proposals/" in page.url, f"创建后应跳转详情，实际: {page.url}"
    prop_id = int(page.url.rstrip("/").split("/")[-1])
    assert page.inner_text("#proposal-status").strip() == "草稿", "新建提案应为草稿态"
    # 左栏正文渲染
    assert "批量导出任务为 CSV" in page.inner_text("#proposal-content"), "左栏应渲染需求正文"
    # 未派发时右栏应给出引导空态
    assert "派发给 Agent" in page.inner_text("#proposal-no-rounds"), "draft 态应引导派发"

    # ---- 3) UI 推进 draft -> queued ----
    page.click("#proposal-queue-btn")
    page.wait_for_function(
        "() => document.querySelector('#proposal-status')?.textContent.trim() === '已入队'",
        timeout=10000,
    )

    # ---- 4) 模拟 Agent：queued -> analyzing 并回写一轮 3 个问题 ----
    r = httpx.put(f"{api_base}/api/proposals/{prop_id}/status",
                  headers=headers, json={"status": "analyzing"}, timeout=10)
    assert r.status_code == 200, f"queued->analyzing 应 200: {r.status_code} {r.text}"

    questions = [
        "导出需要覆盖哪些字段？",
        "是否需要支持按筛选条件导出？",
        "导出权限是否限定为项目成员？",
    ]
    ask = httpx.post(f"{api_base}/api/proposals/{prop_id}/questions", headers=headers,
                     json={"questions": questions, "summary": "第一轮澄清", "agent": "e2e-agent"},
                     timeout=10)
    assert ask.status_code == 201, f"回写问题应 201: {ask.status_code} {ask.text}"

    # 提问后后端应把提案推进到 awaiting
    cur = httpx.get(f"{api_base}/api/proposals/{prop_id}", headers=headers, timeout=10).json()
    assert cur["status"] == "awaiting", f"提问后应为 awaiting，实际: {cur['status']}"

    # ---- 5) 工作台渲染问题卡片 ----
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".proposal-question", state="visible", timeout=12000)
    cards = page.query_selector_all(".proposal-question")
    assert len(cards) == 3, f"右栏应渲染 3 个问题卡片，实际 {len(cards)}"
    assert page.inner_text("#proposal-status").strip() == "待作答", "状态徽标应为待作答"
    # 轮次分组
    assert page.query_selector('.proposal-round[data-round="1"]'), "应存在第 1 轮分组"
    # 左栏时间线
    timeline = page.inner_text(".proposal-timeline")
    assert "第 1 轮" in timeline and "3 个问题" in timeline, f"时间线应含轮次信息: {timeline}"
    # 一键提交按钮计数
    submit_label = page.inner_text("#proposal-submit-round")
    assert "3" in submit_label, f"提交按钮应显示待处理计数，实际: {submit_label}"

    page.screenshot(path=os.path.join(shot_dir, "epic96_p02_workbench_awaiting.png"), full_page=False)

    # ---- 6) 逐条作答 + 标记不确定 ----
    qids = [int(c.get_attribute("data-qid")) for c in cards]

    # 前两条填写答案
    page.fill(f'textarea[data-answer-for="{qids[0]}"]', "标题、状态、负责人、截止日期")
    page.fill(f'textarea[data-answer-for="{qids[1]}"]', "需要，沿用列表当前筛选条件")
    # 第三条标记「暂不确定」
    page.check(f'input[data-unsure-for="{qids[2]}"]')
    # 勾选不确定后答案输入应被禁用
    assert page.is_disabled(f'textarea[data-answer-for="{qids[2]}"]'), "标记不确定后输入框应禁用"

    # ---- 7) 一键提交本轮 ----
    page.click("#proposal-submit-round")
    page.wait_for_selector("#proposal-all-done", state="visible", timeout=15000)

    # 全部问题应变为已答态
    answered = page.query_selector_all(".proposal-question.answered")
    assert len(answered) == 3, f"提交后 3 条问题都应为已答，实际 {len(answered)}"
    assert page.query_selector("#proposal-submit-round") is None, "无待处理问题时提交按钮应隐藏"

    # 状态徽标推进为「已作答」
    page.wait_for_function(
        "() => document.querySelector('#proposal-status')?.textContent.trim() === '已作答'",
        timeout=10000,
    )

    page.screenshot(path=os.path.join(shot_dir, "epic96_p02_workbench_answered.png"), full_page=False)

    # ---- 8) 服务端真值校验 ----
    final = httpx.get(f"{api_base}/api/proposals/{prop_id}", headers=headers, timeout=10).json()
    assert final["status"] == "answered", f"整轮作答后应推进 answered，实际: {final['status']}"

    rounds = httpx.get(f"{api_base}/api/proposals/{prop_id}/rounds",
                       headers=headers, timeout=10).json()
    assert len(rounds) == 1, f"应有 1 轮，实际 {len(rounds)}"
    qs = rounds[0]["questions"]
    assert len(qs) == 3
    assert all(q["answered_at"] for q in qs), "三条问题服务端都应标记已作答"
    assert qs[0]["answer"] == "标题、状态、负责人、截止日期"
    assert qs[1]["answer"] == "需要，沿用列表当前筛选条件"
    assert qs[2]["unsure"] is True, "第三条应被标记为不确定"

    # ---- 9) 回到列表：状态徽标与轮次正确 ----
    page.click("#nav-proposals")
    page.wait_for_selector(".proposal-row", state="visible", timeout=10000)
    row_text = page.inner_text(".proposal-row")
    assert "已作答" in row_text, f"列表应显示已作答徽标: {row_text}"
    assert "第 1 轮" in row_text, f"列表应显示轮次: {row_text}"
    page.screenshot(path=os.path.join(shot_dir, "epic96_p02_workbench_list.png"), full_page=False)

    # ---- 10) 前端零报错 ----
    real = _real_errors(page)
    assert not real, f"前端存在非预期错误: {real}"

    print(f"[E2E] proposal id={prop_id} 问答工作台全链路通过（3 问题 / 2 作答 + 1 不确定 → answered）")


def test_proposal_status_filter_and_dark_theme(page, servers):
    """状态筛选可用 + 暗色主题下工作台可读（徽标/卡片不塌陷）。"""
    import httpx

    api_base, web_base = servers
    ts = str(int(time.time() * 1000))[-9:]
    user = "e2ewb2" + ts
    password = "secret123"

    httpx.post(f"{api_base}/api/auth/register",
               json={"username": user, "password": password}, timeout=10)
    _ui_login(page, web_base, user, password)
    token = page.evaluate("localStorage.getItem('agentboard_token')")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    pid = httpx.post(f"{api_base}/api/projects", headers=headers,
                     json={"name": "筛选项目" + ts}, timeout=10).json()["id"]

    # 造两条不同状态的提案：一条 draft，一条推进到 analyzing
    p_draft = httpx.post(f"{api_base}/api/proposals", headers=headers,
                         json={"project_id": pid, "title": "草稿提案" + ts}, timeout=10).json()
    p_run = httpx.post(f"{api_base}/api/proposals", headers=headers,
                       json={"project_id": pid, "title": "分析中提案" + ts}, timeout=10).json()
    for st in ("queued", "analyzing"):
        httpx.put(f"{api_base}/api/proposals/{p_run['id']}/status",
                  headers=headers, json={"status": st}, timeout=10)

    page.goto(web_base + "/proposals", wait_until="networkidle")
    page.wait_for_selector(".proposal-row", state="visible", timeout=12000)
    assert len(page.query_selector_all(".proposal-row")) == 2, "应列出 2 条提案"

    # 按 analyzing 过滤
    page.select_option("#proposal-status-filter", "analyzing")
    page.wait_for_function(
        "() => document.querySelectorAll('.proposal-row').length === 1",
        timeout=10000,
    )
    assert "分析中提案" in page.inner_text(".proposal-row"), "过滤后应只剩分析中提案"

    # 关键词搜索（本地即时过滤）：搜不到的关键词 -> 空态
    page.select_option("#proposal-status-filter", "")
    page.wait_for_function(
        "() => document.querySelectorAll('.proposal-row').length === 2", timeout=10000)
    page.fill("#proposal-search", "草稿")
    page.wait_for_function(
        "() => document.querySelectorAll('.proposal-row').length === 1", timeout=10000)
    assert "草稿提案" in page.inner_text(".proposal-row")

    # 暗色主题下打开工作台，确认关键元素仍可见
    page.evaluate("document.documentElement.classList.add('dark'); "
                  "document.body.classList.add('dark')")
    page.goto(web_base + f"/proposals/{p_draft['id']}", wait_until="networkidle")
    page.wait_for_selector("#proposal-status", state="visible", timeout=12000)
    assert page.is_visible("#proposal-content"), "暗色主题下正文区应可见"
    assert page.is_visible("#proposal-status"), "暗色主题下状态徽标应可见"

    shot_dir = os.path.join(_ROOT, "screenshots")
    os.makedirs(shot_dir, exist_ok=True)
    page.screenshot(path=os.path.join(shot_dir, "epic96_p02_workbench_dark.png"), full_page=False)

    real = _real_errors(page)
    assert not real, f"前端存在非预期错误: {real}"
