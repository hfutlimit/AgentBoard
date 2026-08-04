"""Epic 78 Story 107: Agent 记忆自动加载（get_project_memory / append_agent_memory MCP 工具）

验证：
1. append_agent_memory 首次创建 type=memory 文档（项目级 / Agent 级 title 约定）；
2. 二次 append 同一 title 幂等累积（content 追加而非新建文档）；
3. get_project_memory 返回 combined 含累积内容；
4. Agent 级隔离：agent=A 取不到 agent=B 的专属记忆，但都能取到项目级记忆；
5. AST 静态护栏：模块内无未定义调用（Epic 97 防 _api 改名漏改复发）；
6. 新工具注册为 MCP 工具（get_project_memory / append_agent_memory 存在）。

自包含：真实 uvicorn 子进程 + httpx + 直接调 MCP 工具 .fn，不依赖 18001 / 18000 / 28080。
"""
import asyncio
import ast
import importlib.util
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = Path(__file__).resolve().parent.parent
_HAS_UVI = importlib.util.find_spec("uvicorn") is not None and \
    importlib.util.find_spec("httpx") is not None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_api(port: int, db_path: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["AGENTBOARD_DB_URL"] = f"sqlite:///{db_path}"
    env["AGENTBOARD_MCP_BACKEND"] = "db"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_http(url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except Exception:
            time.sleep(0.3)
    return False


@pytest.fixture(scope="module")
def api_base(tmp_path_factory):
    if not _HAS_UVI:
        pytest.skip("uvicorn/httpx not installed")
    port = _free_port()
    db = str(tmp_path_factory.mktemp("s107") / "test.db")
    proc = _start_api(port, db)
    assert _wait_http(f"http://127.0.0.1:{port}/api/meta"), "api not up"
    # 先注册 u107 并注入 MCP 服务账号 token（._fn 直调无 FastMCP 上下文，走 env 回退）
    token = _auth_token(f"http://127.0.0.1:{port}")
    os.environ["AGENTBOARD_MCP_TOKEN"] = token
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


@pytest.fixture(scope="module")
def mcp_fns(api_base):
    """直接 import mcp_server 并调用工具 .fn（不启动 MCP 服务，只测函数体）。

    通过环境变量 API_URL 让 _http 打到自起的 uvicorn 子进程（与 18001 容器解耦）。
    """
    import agentboard.mcp_server as ms
    ms.API_URL = api_base
    return ms


def _auth_token(api_base: str) -> str:
    """登录 u107；未注册则先注册（幂等）。"""
    import httpx
    with httpx.Client(base_url=api_base, timeout=10) as c:
        r = c.post("/api/auth/login", json={"username": "u107", "password": "p107pass"})
        if r.status_code == 200:
            return r.json()["token"]
        r = c.post("/api/auth/register",
                   json={"username": "u107", "password": "p107pass"})
        return r.json()["token"]


def _make_project(api_base: str) -> int:
    """建一个独立项目（key 随机避免跨测试冲突）。"""
    import httpx
    token = _auth_token(api_base)
    with httpx.Client(base_url=api_base, timeout=10) as c:
        r = c.post("/api/projects",
                   json={"name": f"P107MEM{uuid4().hex[:4]}", "key": f"P107{uuid4().hex[:4]}"},
                   headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 201, r.text
        return r.json()["id"]


def test_tools_registered(mcp_fns):
    """get_project_memory / append_agent_memory 注册为 MCP 工具。"""
    tools = asyncio.run(mcp_fns.mcp.list_tools())
    names = {t.name for t in tools}
    assert "get_project_memory" in names
    assert "append_agent_memory" in names


def test_append_creates_and_accumulates(api_base, mcp_fns):
    """首次 append 创建文档；二次 append 同一 title 幂等累积（同一文档追加）。"""
    pid = _make_project(api_base)

    r1 = mcp_fns.append_agent_memory(pid, "约定：提交信息用 feat(scope): 描述")
    assert r1["appended"] is False
    assert r1["title"] == "项目记忆"
    doc_id = r1["document_id"]
    assert r1["content_length"] > 0

    r2 = mcp_fns.append_agent_memory(pid, "踩坑：18001 是 MCP 端口，禁止触碰")
    assert r2["appended"] is True
    assert r2["document_id"] == doc_id

    docs = mcp_fns.list_documents(project_id=pid, type="memory")
    target = [d for d in docs if d["id"] == doc_id][0]
    assert "约定：提交信息用 feat(scope)" in target["content"]
    assert "踩坑：18001 是 MCP 端口" in target["content"]
    assert len([d for d in docs if d["title"] == "项目记忆"]) == 1


def test_get_memory_combined(api_base, mcp_fns):
    """get_project_memory 返回 combined，含累积内容与标题标注。"""
    pid = _make_project(api_base)
    mcp_fns.append_agent_memory(pid, "第一条记忆")
    mcp_fns.append_agent_memory(pid, "第二条记忆")

    got = mcp_fns.get_project_memory(pid)
    assert got["project_id"] == pid
    assert got["agent"] is None
    assert "第一条记忆" in got["combined"]
    assert "第二条记忆" in got["combined"]
    assert "[项目记忆]" in got["combined"]
    assert any(d["title"] == "项目记忆" for d in got["documents"])


def test_agent_level_isolation(api_base, mcp_fns):
    """Agent 级隔离：agent=A 取不到 agent=B 的专属记忆；两者都能取项目级。"""
    pid = _make_project(api_base)
    mcp_fns.append_agent_memory(pid, "团队规范：主分支直推需先过测试", agent=None)
    mcp_fns.append_agent_memory(pid, "A 擅长：Angular 性能优化", agent="agent-a")
    mcp_fns.append_agent_memory(pid, "B 擅长：Alembic 迁移", agent="agent-b")

    got_a = mcp_fns.get_project_memory(pid, agent="agent-a")
    titles_a = {d["title"] for d in got_a["documents"]}
    assert "项目记忆" in titles_a
    assert "Agent 记忆 · agent-a" in titles_a
    assert "Agent 记忆 · agent-b" not in titles_a
    assert "A 擅长：Angular 性能优化" in got_a["combined"]
    assert "B 擅长：Alembic 迁移" not in got_a["combined"]

    got_all = mcp_fns.get_project_memory(pid)
    assert len(got_all["documents"]) == 3


def test_ast_no_undefined_calls(mcp_fns):
    """AST 静态护栏：mcp_server.py 内所有 foo(...) 调用可解析（防 _api 改名漏改复发）。"""
    import builtins
    src_path = Path(mcp_fns.__file__)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    module_globals = {
        n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
    } | set(dir(mcp_fns)) | set(dir(builtins))
    unresolved = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in module_globals:
                unresolved.append(node.func.id)
    assert not unresolved, f"未定义调用: {unresolved}"
