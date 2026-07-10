"""AgentBoard 后端自动化测试：针对【已启动的真实 API 服务】做端到端验证。

特点：
- 用 uvicorn 在真实空闲端口拉起 API 服务（真实 TCP / HTTP，非进程内 TestClient）。
- 所有用例通过 httpx 对该已运行的服务发请求，覆盖注册/登录 + 全链路 CRUD。

运行：
    PYTHONPATH=. python -m pytest tests/test_backend_flow.py -q
    PYTHONPATH=. python tests/test_backend_flow.py        # 直接运行（同样先起服务再测）

本模块自带独立临时 SQLite，运行前强制重载 agentboard，避免与其它测试共享 engine。
"""
import os
import sys
import socket
import subprocess
import tempfile
import time

import httpx
import pytest

# 独立临时数据库（与 test_smoke.py 隔离）
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

# 强制重载 agentboard，使 engine 绑定到上面的临时库
for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard.database import init_db
from agentboard.models import ItemType, Status

init_db()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int) -> subprocess.Popen:
    """以独立子进程真实拉起 uvicorn（等同命令行启动 API）。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    # 重定向到 DEVNULL：避免 pytest 捕获导致子进程 sys.stdout 为 None，
    # 进而触发 uvicorn 日志 formatter 的 sys.stdout.isatty() 崩溃。
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc


def _wait_ready(base: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(base + "/api/meta", timeout=1)
            if r.status_code == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"API 服务在 {base} 启动超时")


@pytest.fixture(scope="module")
def api_url():
    """真实启动的 API 服务地址（module 级，三个用例共用同一已启动服务）。"""
    port = _free_port()
    proc = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


# ---------------- 注册 / 登录 ----------------
def test_register_and_login(api_url):
    with httpx.Client(base_url=api_url) as c:
        # 注册
        r = c.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["username"] == "alice"
        assert body["token"]
        token = body["token"]

        # 重复注册 -> 409
        dup = c.post("/api/auth/register", json={"username": "alice", "password": "other"})
        assert dup.status_code == 409, dup.text

        # 登录（正确密码）
        r = c.post("/api/auth/login", json={"username": "alice", "password": "secret123"})
        assert r.status_code == 200, r.text
        assert r.json()["token"]

        # 登录（错误密码）-> 401
        bad = c.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
        assert bad.status_code == 401, bad.text

        # /api/auth/me 不带 token -> 401
        assert c.get("/api/auth/me").status_code == 401

        # /api/auth/me 带 token -> 200
        me = c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        assert me.json()["username"] == "alice"

        # 伪造 token -> 401
        fake = c.get("/api/auth/me", headers={"Authorization": "Bearer 1.deadbeef"})
        assert fake.status_code == 401, fake.text


def test_auth_edge_cases_via_api(api_url):
    """通过 REST API 验证：重复注册、错误密码、注册后即可登录（哈希可校验）。"""
    with httpx.Client(base_url=api_url) as c:
        # 注册 bob
        r = c.post("/api/auth/register", json={"username": "bob", "password": "pw-bob"})
        assert r.status_code == 201, r.text
        assert r.json()["token"]

        # 重复注册 -> 409
        dup = c.post("/api/auth/register", json={"username": "bob", "password": "other"})
        assert dup.status_code == 409, dup.text

        # 错误密码 -> 401（证明密码非明文、哈希校验生效）
        bad = c.post("/api/auth/login", json={"username": "bob", "password": "wrong"})
        assert bad.status_code == 401, bad.text

        # 注册后正确密码可登录（哈希 round-trip 通过 API 验证）
        ok = c.post("/api/auth/login", json={"username": "bob", "password": "pw-bob"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["token"]

        # 不存在的用户登录 -> 401
        nope = c.post("/api/auth/login", json={"username": "ghost", "password": "x"})
        assert nope.status_code == 401, nope.text


# ---------------- 全链路 CRUD（含 task/bug） ----------------
def test_full_crud_flow(api_url):
    with httpx.Client(base_url=api_url) as c:
        # 先登录拿到 token（仅演示鉴权接口可用，CRUD 接口当前为单用户开放）
        reg = c.post("/api/auth/register", json={"username": "carol", "password": "pw"})
        token = reg.json()["token"]

        # project
        p = c.post("/api/projects", json={"name": "Flow 项目", "key": "FLOW"}).json()
        assert p["id"] > 0 and p["key"] == "FLOW"

        # epic
        e = c.post(f"/api/projects/{p['id']}/epics", json={"title": "Epic A"}).json()
        assert e["id"] > 0

        # story
        st = c.post(f"/api/epics/{e['id']}/stories", json={"title": "Story 1"}).json()
        assert st["id"] > 0

        # task（type=task）
        t = c.post(f"/api/stories/{st['id']}/tasks",
                   json={"project_id": p["id"], "title": "实现登录", "type": "task"}).json()
        assert t["type"] == ItemType.TASK

        # bug（type=bug）
        b = c.post(f"/api/stories/{st['id']}/tasks",
                   json={"project_id": p["id"], "title": "登录页崩溃", "type": "bug"}).json()
        assert b["type"] == ItemType.BUG

        # 列表同时含 task 与 bug
        rows = c.get(f"/api/stories/{st['id']}/tasks").json()
        assert len(rows) == 2

        # 状态流转：task 合法迁移
        assert c.put(f"/api/tasks/{t['id']}/status", json={"status": "todo"}).status_code == 200
        assert c.put(f"/api/tasks/{t['id']}/status", json={"status": "in_progress"}).status_code == 200
        # 非法迁移（in_progress -> backlog）-> 400
        assert c.put(f"/api/tasks/{t['id']}/status", json={"status": "backlog"}).status_code == 400

        # bug 额外状态 verifying 可用（需遵循状态机：backlog->todo->in_progress->verifying）
        assert c.put(f"/api/tasks/{b['id']}/status", json={"status": "todo"}).status_code == 200
        assert c.put(f"/api/tasks/{b['id']}/status", json={"status": "in_progress"}).status_code == 200
        assert c.put(f"/api/tasks/{b['id']}/status", json={"status": "verifying"}).status_code == 200

        # 搜索按 type 过滤
        bugs = c.get("/api/tasks", params={"project_id": p["id"], "type": "bug"}).json()
        assert len(bugs) == 1 and bugs[0]["id"] == b["id"]

        # 删除 task 不影响 bug
        assert c.delete(f"/api/tasks/{t['id']}").status_code == 200
        remain = c.get(f"/api/stories/{st['id']}/tasks").json()
        assert len(remain) == 1 and remain[0]["id"] == b["id"]

        # 级联删除 project（epic/story/bug 一并删除）
        assert c.delete(f"/api/projects/{p['id']}").status_code == 200
        assert c.get(f"/api/epics/{e['id']}").status_code == 404
        assert c.get(f"/api/stories/{st['id']}").status_code == 404

        # token 仍然有效（用户与项目数据相互独立）
        me = c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200 and me.json()["username"] == "carol"


if __name__ == "__main__":
    port = _free_port()
    proc = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        test_register_and_login(base)
        test_auth_edge_cases_via_api(base)
        test_full_crud_flow(base)
        print("BACKEND FLOW OK (live server)")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
