"""Epic 98 P0 — 发布产物「解压即部署」端到端验证。

test_epic98_release_artifact_parity.py 做的是**文本级**比对（产物是否等于源码）。
本模块更进一步做**运行级**验证：把 `dist/agentboard-webapi.zip` 解压到临时目录，
完全脱离仓库源码、只用产物里的文件真实拉起服务，然后回答三个问题：

1. 产物能不能跑起来、Epic 96 的提案接口在产物里是否真实可用
   （历史事故：`domains/proposals` 与其 Alembic 迁移压根没进包 → 生产 ImportError / 缺表）。
2. **产物里的** `mcp_server.py` 被真正调用时会不会抛 `NameError`
   （历史事故：Epic 97 修好了源码，产物还是旧的 → 部署等于把 bug 装回去）。
   这一步用子进程加载产物内的模块，避免与仓库源码的 `agentboard` 包在 sys.modules 里打架。
3. 产物托管的前端页面能否正常登录、渲染核心视图，且控制台零报错。

只有这三问全部为「是」，才算发布产物真的可交付。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic98_release_artifact_e2e.py -q

注意：本用例全程自起进程、自带临时 SQLite 库，不依赖也不触碰任何既有服务
（尤其不碰 18001 上的 MCP 容器）。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import zipfile
from pathlib import Path

import httpx
import pytest

_ROOT = Path(__file__).resolve().parent.parent
_ZIP = _ROOT / "dist" / "agentboard-webapi.zip"

pytestmark = pytest.mark.skipif(not _ZIP.is_file(), reason="dist/agentboard-webapi.zip 不存在，请先打包")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2)
            if r.status_code < 500:
                return
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(0.3)
    raise RuntimeError(f"服务在 {url} 启动超时，最后一次错误：{last}")


@pytest.fixture(scope="module")
def artifact():
    """把发布 zip 解压到临时目录，并只用产物内容拉起 API + Web 两个服务。"""
    tmp = tempfile.mkdtemp(prefix="agb-artifact-")
    root = Path(tmp) / "app"
    with zipfile.ZipFile(_ZIP) as z:
        z.extractall(root)

    # 关键：确认我们跑的是产物，而不是意外落回了仓库源码
    assert (root / "agentboard" / "api.py").is_file(), "解压后的产物缺少 agentboard/api.py"

    db = Path(tmp) / "artifact.db"
    env = os.environ.copy()
    env["AGENTBOARD_DB_URL"] = f"sqlite:///{db.as_posix()}"
    env["PYTHONPATH"] = str(root)          # 只暴露产物目录
    env["AGENTBOARD_SECRET"] = "epic98-artifact-secret"
    env.pop("AGENTBOARD_MCP_TOKEN", None)

    api_port, web_port = _free_port(), _free_port()
    api_base = f"http://127.0.0.1:{api_port}"
    web_base = f"http://127.0.0.1:{web_port}"

    web_env = env.copy()
    web_env["AGENTBOARD_API_URL"] = api_base

    procs = []
    try:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "agentboard.api:app",
             "--host", "127.0.0.1", "--port", str(api_port), "--log-level", "warning"],
            cwd=str(root), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        ))
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "agentboard.web_app:app",
             "--host", "127.0.0.1", "--port", str(web_port), "--log-level", "warning"],
            cwd=str(root), env=web_env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        ))
        _wait_ready(api_base + "/api/meta")
        _wait_ready(web_base + "/")

        c = httpx.Client(base_url=api_base, timeout=30)
        c.post("/api/auth/register", json={"username": "e98admin", "password": "e98admin123"})
        r = c.post("/api/auth/login", json={"username": "e98admin", "password": "e98admin123"})
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        c.headers.update({"Authorization": f"Bearer {token}"})

        pid = c.post("/api/projects", json={"name": "Epic98 产物验收"}).json()["id"]
        eid = c.post(f"/api/projects/{pid}/epics", json={"title": "产物冒烟"}).json()["id"]
        sid = c.post(f"/api/epics/{eid}/stories", json={"title": "冒烟 Story"}).json()["id"]
        tids = [
            c.post(f"/api/stories/{sid}/tasks",
                   json={"project_id": pid, "title": f"产物任务 {i}", "type": "task"}).json()["id"]
            for i in range(2)
        ]

        yield {
            "root": root, "c": c, "token": token, "api_base": api_base, "web_base": web_base,
            "project_id": pid, "story_id": sid, "task_ids": tids, "env": env,
            "username": "e98admin", "password": "e98admin123",
        }
        c.close()
    finally:
        for p in procs:
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                p.kill()


# ---------------- 1. Epic 96 的提案能力确实在产物里 ----------------

def test_packaged_artifact_serves_proposals_api(artifact):
    """产物解压即用：提案表建得出来、增删查改状态机全通。

    这条用例在修复前会失败——因为 `domains/proposals` 与其迁移根本没进包。
    """
    c, pid = artifact["c"], artifact["project_id"]

    r = c.post("/api/proposals", json={
        "project_id": pid, "title": "产物里的提案", "content": "验证提案能力已随包发布",
    })
    assert r.status_code == 201, f"产物中提案创建失败（多半是 proposals 表/模块缺失）：{r.text}"
    proposal = r.json()

    r = c.get("/api/proposals", params={"project_id": pid})
    assert r.status_code == 200, r.text
    assert any(p["id"] == proposal["id"] for p in r.json()), "新建提案未出现在列表中"

    # 状态机在产物里同样生效
    r = c.put(f"/api/proposals/{proposal['id']}/status", json={"status": "queued"})
    assert r.status_code == 200, f"提案状态迁移失败：{r.text}"
    assert r.json()["status"] == "queued"

    r = c.get("/api/proposals/pending")
    assert r.status_code == 200, r.text
    assert any(p["id"] == proposal["id"] for p in r.json()), "queued 提案未出现在 pending 队列"


def test_packaged_artifact_has_proposal_tables(artifact):
    """三张提案表真的被 Alembic 迁移建了出来（迁移文件在包里且被执行）。"""
    code = textwrap.dedent("""
        import json
        from sqlalchemy import inspect
        from agentboard.database import engine
        print(json.dumps(sorted(inspect(engine).get_table_names())))
    """)
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(artifact["root"]), env=artifact["env"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, f"读取产物数据库表失败：{r.stderr}"
    tables = json.loads(r.stdout.strip().splitlines()[-1])
    for t in ("proposals", "proposal_rounds", "proposal_questions"):
        assert t in tables, f"产物部署后缺少表 {t}（迁移未随包发布）；实际表：{tables}"


# ---------------- 2. 产物里的 MCP 工具真跑不抛 NameError ----------------

def test_packaged_mcp_tools_run_without_nameerror(artifact):
    """用**产物内**的 mcp_server 真调曾经损坏的工具，断言无 NameError。

    这是 Epic 97 事故的终局验证：不看源码、只看即将部署出去的那份文件。
    子进程加载，避免与仓库源码的 agentboard 包冲突。
    """
    code = textwrap.dedent("""
        import json, os
        from agentboard import mcp_server
        mcp_server.API_URL = os.environ["ARTIFACT_API"]
        pid = int(os.environ["ARTIFACT_PID"])
        out = {}
        def run(label, fn):
            try:
                out[label] = {"ok": True, "value": repr(fn())[:200]}
            except Exception as e:
                out[label] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        run("search_tasks_enhanced_single",
            lambda: mcp_server.search_tasks_enhanced(project_id=pid, status="backlog"))
        run("search_tasks_enhanced_multi",
            lambda: mcp_server.search_tasks_enhanced(project_id=pid, status=["backlog","todo"]))
        run("export_project_data", lambda: mcp_server.export_project_data(pid))
        run("list_audit_logs", lambda: mcp_server.list_audit_logs(entity_type="task", limit=5))
        run("list_webhooks", lambda: mcp_server.list_webhooks(project_id=pid))
        print("RESULT_JSON:" + json.dumps(out))
    """)
    env = artifact["env"].copy()
    env["ARTIFACT_API"] = artifact["api_base"]
    env["ARTIFACT_PID"] = str(artifact["project_id"])
    env["AGENTBOARD_MCP_TOKEN"] = artifact["token"]

    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(artifact["root"]), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, f"加载产物内 mcp_server 失败：\n{r.stdout}\n{r.stderr}"
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT_JSON:")]
    assert line, f"未取得调用结果：\n{r.stdout}\n{r.stderr}"
    results = json.loads(line[-1][len("RESULT_JSON:"):])

    broken = {k: v for k, v in results.items() if not v["ok"]}
    assert not broken, (
        "产物内的 MCP 工具调用失败（Epic 97 的修复没有进入发布产物）：\n  "
        + "\n  ".join(f"{k}: {v['error']}" for k, v in broken.items())
    )
    # 额外钉死：即便没抛异常，也不允许返回体里藏着 NameError 文本
    for k, v in results.items():
        assert "not defined" not in v["value"], f"{k} 返回里出现 NameError 痕迹：{v['value']}"


# ---------------- 3. 产物托管的前端可登录、可渲染、零报错 ----------------

def test_packaged_frontend_renders_without_console_errors(artifact):
    """Playwright 打开产物托管的前端，登录并检查核心视图渲染 + 控制台零报错。"""
    sync_api = pytest.importorskip("playwright.sync_api", reason="未安装 playwright")

    console_errors, failed_requests = [], []
    shot = _ROOT / "screenshots" / "epic98_artifact_frontend.png"
    shot.parent.mkdir(exist_ok=True)

    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        page = browser.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

        def _on_failed(req):
            if req.resource_type in ("script", "stylesheet", "document"):
                failed_requests.append(f"{req.resource_type} {req.url}")
        page.on("requestfailed", _on_failed)

        try:
            page.goto(artifact["web_base"], wait_until="domcontentloaded", timeout=60000)

            # 未登录会被 SPA 重定向到登录页
            page.wait_for_selector("input[name=username]", timeout=30000)
            page.fill("input[name=username]", artifact["username"])
            page.fill("input[name=password]", artifact["password"])
            page.click("button.login-submit")

            # 等骨架屏消失（侧栏整棵树预加载较慢）
            page.wait_for_function(
                "!document.querySelector('.skeleton')", timeout=60000,
            )
            page.wait_for_selector("#sidebar", timeout=30000)
            page.screenshot(path=str(shot), full_page=True)

            body = page.inner_text("body")
            assert "Epic98 产物验收" in body or page.locator("#sidebar").count() > 0, (
                "登录后未渲染出项目侧栏"
            )
        finally:
            browser.close()

    # 忽略与本次无关的噪声（favicon / 良性中断的 API 轮询）
    real_errors = [
        e for e in console_errors
        if "favicon" not in e.lower() and "ERR_ABORTED" not in e
    ]
    assert not real_errors, "产物前端控制台报错：\n  " + "\n  ".join(real_errors)
    assert not failed_requests, "产物前端静态资源加载失败：\n  " + "\n  ".join(failed_requests)
