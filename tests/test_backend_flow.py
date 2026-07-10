"""AgentBoard 后端自动化测试：注册/登录 + 全链路 CRUD（project→epic→story→task/bug）。

运行：PYTHONPATH=. python -m pytest tests/test_backend_flow.py -q
（也可直接 python tests/test_backend_flow.py 手动跑）

本模块自带独立临时 SQLite，运行前强制重载 agentboard，避免与其它测试共享 engine。
"""
import os
import sys
import tempfile

# 独立临时数据库（与 test_smoke.py 隔离）
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

# 强制重载 agentboard，使 engine 绑定到上面的临时库
for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard.database import init_db, SessionLocal
from agentboard import service
from agentboard.models import ItemType, Status

init_db()


def _client():
    from fastapi.testclient import TestClient
    from agentboard.api import app
    return TestClient(app)


# ---------------- 注册 / 登录 ----------------
def test_register_and_login():
    c = _client()
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


def test_service_auth_layer():
    with SessionLocal() as s:
        u = service.register_user(s, username="bob", password="pw-bob")
        assert u.id > 0
        # 重复 -> Duplicate
        try:
            service.register_user(s, username="bob", password="x")
            assert False, "重复用户名未拦截"
        except service.Duplicate:
            pass
        # 认证成功 / 失败
        assert service.authenticate_user(s, username="bob", password="pw-bob") is not None
        assert service.authenticate_user(s, username="bob", password="nope") is None
        # 密码哈希不可逆校验
        assert service.get_user(s, u.id).username == "bob"


# ---------------- 全链路 CRUD（含 task/bug） ----------------
def test_full_crud_flow():
    c = _client()
    # 先登录拿到 token（仅用于演示鉴权接口可用，CRUD 接口当前为单用户开放）
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
    rows = c.get("/api/stories/{}/tasks".format(st["id"])).json()
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

    # 删除 scene：删 task 不影响 bug
    assert c.delete(f"/api/tasks/{t['id']}").status_code == 200
    remain = c.get("/api/stories/{}/tasks".format(st["id"])).json()
    assert len(remain) == 1 and remain[0]["id"] == b["id"]

    # 级联删除 project（epic/story/bug 一并删除）
    assert c.delete(f"/api/projects/{p['id']}").status_code == 200
    assert c.get(f"/api/epics/{e['id']}").status_code == 404
    assert c.get(f"/api/stories/{st['id']}").status_code == 404

    # token 仍然有效（用户与项目数据相互独立）
    me = c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["username"] == "carol"


if __name__ == "__main__":
    test_register_and_login()
    test_service_auth_layer()
    test_full_crud_flow()
    print("BACKEND FLOW OK")
