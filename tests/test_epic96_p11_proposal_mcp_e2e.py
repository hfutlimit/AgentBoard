"""Epic 96 P1-1 · Proposal MCP Worker 工具面 — 真实浏览器 E2E。

单测（test_epic96_p11_proposal_mcp_tools.py）只证明工具对 REST 有效；
本测试补上**跨端闭环**这一环：无头 Agent 经 MCP 工具写入的澄清问题，
必须能被真实用户在 Web 问答工作台看到、作答，作答结果又能被 Agent
经 `proposal_get` 全量重放读回，最终 `proposal_finalize` 收敛的结果
再回到 Web 页面上。

    MCP(claim/ask) → Web(渲染/作答) → MCP(重放读回/finalize) → Web(已收敛)

自包含启动真实 API + Web（临时 SQLite），Chromium 驱动 SPA，
全程断言 0 console error / 0 pageerror。不依赖也不触碰 18001 上的 MCP 容器。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic96_p11_proposal_mcp_e2e.py -q
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

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

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
    pg.on("console", lambda m: errors.append(("console", m.text)) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(("pageerror", str(e))))
    pg._errors = errors  # type: ignore[attr-defined]
    try:
        yield pg
    finally:
        pg.close()
        ctx.close()


def _real_errors(page):
    return [
        e for e in page._errors  # type: ignore[attr-defined]
        if not (isinstance(e[1], str) and ("ERR_ABORTED" in e[1] or "favicon" in e[1].lower()))
    ]


def _ui_login(page, base: str, username: str, password: str):
    page.goto(base + "/", wait_until="networkidle")
    page.wait_for_selector('input[name="username"]', state="visible", timeout=10000)
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click("button.login-submit")
    page.wait_for_selector("#home-new-project", state="visible", timeout=12000)


def test_mcp_worker_loop_visible_in_web_workbench(page, servers):
    """无头 Agent 经 MCP 驱动澄清，用户在 Web 工作台完成闭环。"""
    import httpx

    from agentboard import mcp_server

    api_base, web_base = servers
    ts = str(int(time.time()))
    user = "e2emcp" + ts
    password = "secret123"

    r = httpx.post(f"{api_base}/api/auth/register",
                   json={"username": user, "password": password}, timeout=10)
    assert r.status_code in (200, 201), f"注册应成功: {r.status_code} {r.text}"

    _ui_login(page, web_base, user, password)
    token = page.evaluate("localStorage.getItem('agentboard_token')")
    assert token, "登录后应写入 agentboard_token"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    pid = httpx.post(f"{api_base}/api/projects", headers=headers,
                     json={"name": "MCP 澄清闭环" + ts}, timeout=10).json()["id"]

    # 让 MCP 工具指向这套真实栈（模拟 Worker 用服务账号 abk_ key 接入）
    prev_url, prev_token = mcp_server.API_URL, os.environ.get("AGENTBOARD_MCP_TOKEN")
    mcp_server.API_URL = api_base
    os.environ["AGENTBOARD_MCP_TOKEN"] = token

    shot_dir = os.path.join(_ROOT, "screenshots")
    os.makedirs(shot_dir, exist_ok=True)

    try:
        # ---- 1) 用户提交提案并派发（Web 端行为，用 REST 直造以聚焦本任务范围） ----
        prop = httpx.post(f"{api_base}/api/proposals", headers=headers, json={
            "project_id": pid, "title": "MCP 驱动的澄清提案" + ts,
            "content": "希望给看板加一个泳道视图，按负责人分组。",
        }, timeout=10).json()
        prop_id = prop["id"]
        httpx.put(f"{api_base}/api/proposals/{prop_id}/status",
                  headers=headers, json={"status": "queued"}, timeout=10)

        # ---- 2) Worker 侧：MCP 轮询 → 认领 → 全量重放 → 回写问题 ----
        pending = mcp_server.proposal_pending(limit=50)
        assert prop_id in [p["id"] for p in pending], "Worker 应能轮询到待认领提案"

        claimed = mcp_server.proposal_claim(prop_id, agent="e2e-worker")
        assert claimed.get("ok") is True, f"认领失败: {claimed!r}"

        ctx = mcp_server.proposal_get(prop_id)
        assert "泳道视图" in ctx["content"], "重放上下文应含原始需求正文"
        assert ctx["open_questions"] == [], "首轮尚无问题"

        questions = [
            "泳道是否需要支持折叠？",
            "未指派的任务放在哪条泳道？",
            "泳道顺序按什么排序？",
        ]
        asked = mcp_server.proposal_ask(
            prop_id, questions=questions, summary="首轮澄清：泳道交互", agent="e2e-worker")
        assert asked["round"]["round_no"] == 1, f"首轮 round_no 应为 1: {asked!r}"
        qids = [q["id"] for q in asked["questions"]]
        assert len(qids) == 3

        # ---- 3) Web 工作台：MCP 写入的问题必须渲染出来 ----
        page.goto(web_base + f"/proposals/{prop_id}", wait_until="networkidle")
        page.wait_for_selector(".proposal-question", state="visible", timeout=15000)
        cards = page.query_selector_all(".proposal-question")
        assert len(cards) == 3, f"工作台应渲染 MCP 写入的 3 个问题，实际 {len(cards)}"
        assert page.inner_text("#proposal-status").strip() == "待作答", "状态徽标应为待作答"
        assert page.query_selector('.proposal-round[data-round="1"]'), "应存在第 1 轮分组"

        body = page.inner_text(".proposal-round[data-round='1']")
        for q in questions:
            assert q in body, f"问题「{q}」未渲染到工作台"

        rendered_qids = sorted(int(c.get_attribute("data-qid")) for c in cards)
        assert rendered_qids == sorted(qids), "页面上的问题应与 MCP 写入的是同一批"

        page.screenshot(path=os.path.join(shot_dir, "epic96_p11_mcp_questions.png"))

        # ---- 4) 用户在 UI 逐条作答（含一条标记不确定）并一键提交 ----
        page.fill(f'textarea[data-answer-for="{qids[0]}"]', "需要支持折叠，默认展开")
        page.fill(f'textarea[data-answer-for="{qids[1]}"]', "放在最左侧「未指派」泳道")
        page.check(f'input[data-unsure-for="{qids[2]}"]')
        page.click("#proposal-submit-round")
        page.wait_for_selector("#proposal-all-done", state="visible", timeout=15000)
        page.wait_for_function(
            "() => document.querySelector('#proposal-status')?.textContent.trim() === '已作答'",
            timeout=10000,
        )

        # ---- 5) Agent 侧全量重放：必须读回用户答案与不确定标记 ----
        ctx2 = mcp_server.proposal_get(prop_id)
        assert ctx2["status"] == "answered", f"作答后状态应为 answered: {ctx2['status']}"
        assert ctx2["total_questions"] == 3
        assert ctx2["answered_count"] == 3, "三条都应已处理"
        assert ctx2["open_questions"] == [], "不应还有待答问题"

        by_text = {h["question"]: h for h in ctx2["history"]}
        assert by_text["泳道是否需要支持折叠？"]["answer"] == "需要支持折叠，默认展开", \
            "Agent 未能读回用户在 UI 填写的答案"
        assert by_text["未指派的任务放在哪条泳道？"]["answer"] == "放在最左侧「未指派」泳道"
        assert by_text["泳道顺序按什么排序？"]["unsure"] is True, \
            "Agent 未能读回用户勾选的「暂不确定」标记"

        # ---- 6) Agent 收敛定稿 → Web 端可见「已收敛」 ----
        spec = ("## 看板泳道视图\n- 按负责人分组，支持折叠（默认展开）\n"
                "- 未指派任务置于最左侧泳道\n- 排序规则待定（用户标记不确定）")
        fin = mcp_server.proposal_finalize(prop_id, spec)
        assert fin["status"] == "converged", f"收敛失败: {fin!r}"

        page.reload(wait_until="networkidle")
        page.wait_for_function(
            "() => document.querySelector('#proposal-status')?.textContent.trim() === '已收敛'",
            timeout=15000,
        )
        page.screenshot(path=os.path.join(shot_dir, "epic96_p11_mcp_converged.png"))

        server_row = httpx.get(f"{api_base}/api/proposals/{prop_id}",
                               headers=headers, timeout=10).json()
        assert server_row["converged_spec"] == spec, "收敛规格未落库"
        assert server_row["story_id"] is None, "P1 不应自动建 Story（人工终审留到 P3）"

        # ---- 7) 列表页状态徽标同步 ----
        page.click("#nav-proposals")
        page.wait_for_selector(".proposal-row", state="visible", timeout=10000)
        assert "已收敛" in page.inner_text(".proposal-row"), "列表应显示已收敛徽标"

        # ---- 8) 前端零报错 ----
        real = _real_errors(page)
        assert not real, f"前端存在非预期错误: {real}"

        print(f"[E2E] proposal id={prop_id} MCP→Web 澄清闭环通过"
              f"（MCP 提 3 问 → UI 作答 → MCP 重放读回 → finalize 已收敛）")
    finally:
        mcp_server.API_URL = prev_url
        if prev_token is None:
            os.environ.pop("AGENTBOARD_MCP_TOKEN", None)
        else:
            os.environ["AGENTBOARD_MCP_TOKEN"] = prev_token
