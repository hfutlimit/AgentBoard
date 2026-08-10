"""Epic 96 P1-2 · Worker 驱动的澄清回路 — 真实浏览器 E2E。

与 P0-2 的 E2E 区别：那一版由测试代码手工调 REST「扮演 Agent」，只证明了
前端能渲染。本测试**真的跑一个 `ProposalWorker`**（含真实子进程 Agent CLI），
证明整条自动化链路在浏览器可见层面成立：

1. 用户在 Web 端建提案 → 点「派发给 Agent」推进 queued；
2. Worker 轮询发现 queued → 认领 → 拉起无头 Agent 子进程 → 回写一轮问题；
3. 浏览器刷新后，Worker 写入的问题卡片正确渲染，状态徽标变「待作答」；
4. 用户在 UI 上逐条作答并一键提交 → 状态推进「已作答」；
5. Worker 再轮询一次 → 全量重放（能读到用户答案）→ finalize；
6. 浏览器刷新后状态徽标为「已收敛」，左栏展示收敛规格；
7. 全程 0 console error / 0 pageerror，截图留证。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic96_p12_proposal_worker_e2e.py -q
未安装 playwright / Chromium 时自动 skip。自包含，不触碰 18001。
"""
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time

import pytest

_HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
_RUN = importlib.util.find_spec("uvicorn") is not None and _HAS_PLAYWRIGHT

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

pytestmark = pytest.mark.skipif(not _RUN, reason="需要 uvicorn + playwright")


# 无头 Agent 桩：读 stdin 的 prompt，按「有无历史问答」决定提问还是收敛。
# 走真实子进程，自证 SubprocessAgentInvoker 的 stdin/stdout 协议。
_FAKE_AGENT = textwrap.dedent('''
    import sys
    prompt = sys.stdin.read()
    sys.stderr.write("[agent] prompt %d chars\\n" % len(prompt))
    if "历史问答（全量重放）" in prompt:
        print("分析完成，需求已清晰。")
        print('{"action":"finalize","converged_spec":"## 批量导出\\\\n- 字段：标题/状态/负责人\\\\n- 权限：仅项目成员"}')
    else:
        print("先澄清几个关键点。")
        print('{"action":"ask","questions":["导出需要覆盖哪些字段？","导出权限如何限定？"],'
              '"summary":"第一轮：字段与权限"}')
''')


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
def fake_agent(tmp_path_factory):
    p = tmp_path_factory.mktemp("p12agent") / "agent.py"
    p.write_text(_FAKE_AGENT, encoding="utf-8")
    return str(p)


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


def test_worker_driven_clarification_visible_in_web(page, servers, fake_agent):
    """Worker 自动推动的澄清回路，在浏览器里一步步可见。"""
    import httpx
    from agentboard.worker import ProposalWorker, SubprocessAgentInvoker, WorkerConfig

    api_base, web_base = servers
    ts = str(int(time.time()))
    user, password = "e2ew12" + ts, "secret123"

    reg = httpx.post(f"{api_base}/api/auth/register",
                     json={"username": user, "password": password}, timeout=10)
    assert reg.status_code in (200, 201), f"注册应成功: {reg.status_code} {reg.text}"

    _ui_login(page, web_base, user, password)
    token = page.evaluate("localStorage.getItem('agentboard_token')")
    assert token, "登录后应写入 agentboard_token"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    proj = httpx.post(f"{api_base}/api/projects", headers=headers,
                      json={"name": "Worker 闭环项目" + ts}, timeout=10)
    assert proj.status_code == 201, f"建项目应 201: {proj.status_code} {proj.text}"
    project_id = proj.json()["id"]

    shot_dir = os.path.join(_ROOT, "screenshots")
    os.makedirs(shot_dir, exist_ok=True)

    # ---- 1) UI 建提案并派发 ----
    page.wait_for_selector("#nav-proposals", state="visible", timeout=10000)
    page.click("#nav-proposals")
    page.wait_for_selector("#new-proposal-btn", state="visible", timeout=10000)
    page.click("#new-proposal-btn")
    page.wait_for_selector("#proposal-new-title", state="visible", timeout=8000)
    page.select_option("#proposal-project", str(project_id))
    page.fill("#proposal-new-title", "Worker 驱动澄清" + ts)
    page.fill("#proposal-new-content", "希望支持批量导出任务为 CSV，字段范围与权限边界待定。")
    page.click("#proposal-create-submit")
    page.wait_for_selector("#proposal-title", state="visible", timeout=12000)
    prop_id = int(page.url.rstrip("/").split("/")[-1])

    page.click("#proposal-queue-btn")
    page.wait_for_function(
        "() => document.querySelector('#proposal-status')?.textContent.trim() === '已入队'",
        timeout=10000,
    )

    # ---- 2) 真实 Worker + 真实 Agent 子进程：第一轮提问 ----
    cfg = WorkerConfig(
        api_url=api_base, token=token, agent="e2e-worker", poll_interval=0.01,
        agent_cmd=f'"{sys.executable}" "{fake_agent}"', agent_timeout=120,
    )
    with ProposalWorker(cfg) as worker:
        assert isinstance(worker.invoker, SubprocessAgentInvoker), "应走真实子进程适配器"
        summary = worker.poll_once()
        assert {"proposal_id": prop_id, "outcome": "asked"} in summary["handled"], \
            f"Worker 应认领并提问，实际: {summary}"

        # ---- 3) 浏览器看到 Worker 写入的问题 ----
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".proposal-question", state="visible", timeout=12000)
        cards = page.query_selector_all(".proposal-question")
        assert len(cards) == 2, f"应渲染 Worker 写入的 2 个问题，实际 {len(cards)}"
        assert page.inner_text("#proposal-status").strip() == "待作答"
        body_text = page.inner_text(".proposal-pane--right")
        assert "导出需要覆盖哪些字段？" in body_text, "问题文本应来自无头 Agent 子进程"
        page.screenshot(path=os.path.join(shot_dir, "epic96_p12_worker_asked.png"))

        # ---- 4) 用户在 UI 上作答并一键提交 ----
        qids = [int(c.get_attribute("data-qid")) for c in cards]
        page.fill(f'textarea[data-answer-for="{qids[0]}"]', "标题、状态、负责人")
        page.fill(f'textarea[data-answer-for="{qids[1]}"]', "仅项目成员可导出")
        page.click("#proposal-submit-round")
        page.wait_for_function(
            "() => document.querySelector('#proposal-status')?.textContent.trim() === '已作答'",
            timeout=15000,
        )

        # ---- 5) Worker 第二轮：全量重放读到答案 → 收敛 ----
        summary = worker.poll_once()
        assert {"proposal_id": prop_id, "outcome": "converged"} in summary["handled"], \
            f"Worker 应从 answered 续轮并收敛，实际: {summary}"

    # ---- 6) 浏览器看到收敛结果 ----
    page.reload(wait_until="networkidle")
    page.wait_for_function(
        "() => document.querySelector('#proposal-status')?.textContent.trim() === '已收敛'",
        timeout=12000,
    )
    left = page.inner_text(".proposal-pane--left")
    assert "收敛规格" in left, "左栏应展示收敛规格区块"
    assert "仅项目成员" in left, f"收敛规格内容应落地，实际左栏: {left[:300]}"
    page.screenshot(path=os.path.join(shot_dir, "epic96_p12_worker_converged.png"))

    # ---- 7) 服务端真值 ----
    final = httpx.get(f"{api_base}/api/proposals/{prop_id}", headers=headers, timeout=10).json()
    assert final["status"] == "converged", f"服务端状态应为 converged，实际 {final['status']}"
    assert "批量导出" in final["converged_spec"]

    errs = _real_errors(page)
    assert not errs, f"页面存在报错: {errs}"
