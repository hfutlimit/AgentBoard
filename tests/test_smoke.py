"""AgentBoard smoke test：前后端分离三端验证（service / REST API / Web / MCP）。

运行：PYTHONPATH=. python tests/test_smoke.py
"""
import os
import tempfile
import hashlib

# 必须在导入 agentboard 之前设置临时 SQLite 与 MCP 后端
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"  # MCP 直连 DB，无需启动 API

from agentboard.database import init_db, SessionLocal
from agentboard import service
from agentboard.models import ItemType, Status, Priority


def test_password_hash_compatibility():
    from agentboard import auth

    new_hash = auth.hash_password("password123")
    assert new_hash.startswith("pbkdf2_sha256$600000$")
    assert auth.verify_password("password123", new_hash)
    assert not auth.password_needs_rehash(new_hash)

    salt = "00" * 16
    legacy_digest = hashlib.pbkdf2_hmac(
        "sha256", b"password123", bytes.fromhex(salt), 100_000
    ).hex()
    legacy_hash = f"pbkdf2_sha256${salt}${legacy_digest}"
    assert auth.verify_password("password123", legacy_hash)
    assert auth.password_needs_rehash(legacy_hash)


def test_service_layer():
    init_db()
    with SessionLocal() as s:
        p = service.create_project(s, name="Demo", key="DEMO", description="# Demo")
        ep = service.create_epic(s, project_id=p.id, title="E1")
        st = service.create_story(s, epic_id=ep.id, title="S1")
        t = service.create_task(s, project_id=p.id, story_id=st.id, type=ItemType.TASK,
                                title="T1", priority=Priority.HIGH)
        assert t.priority == Priority.HIGH
        service.set_task_spec(s, t.id, "## Spec\n做这件事")
        assert service.get_task(s, t.id).spec.startswith("## Spec")

        service.set_status(s, t.id, Status.TODO)
        service.set_status(s, t.id, Status.IN_PROGRESS)
        try:
            service.set_status(s, t.id, Status.BACKLOG)
            assert False, "illegal transition 未拦截"
        except service.IllegalTransition:
            pass

        assert len(service.search_tasks(s, project_id=p.id, q="这件事")) >= 1
        assert service.search_tasks(s, project_id=p.id, priority=Priority.HIGH)[0].id == t.id
        comment = service.create_comment(s, task_id=t.id, author="codex", content="开始实现")
        assert service.list_comments(s, t.id)[0].id == comment.id
        assert service.delete_comment(s, comment.id)
        # epic / story 更新
        assert service.update_epic(s, ep.id, status=Status.TODO).status == Status.TODO
        assert service.update_story(s, st.id, title="S1-x").title == "S1-x"


def test_rest_api():
    from fastapi.testclient import TestClient
    from agentboard.api import app
    with TestClient(app) as c:
        assert c.get("/api/meta").json()["types"] == ["task", "bug"]
        # 分页
        assert len(c.get("/api/projects", params={"limit": 1}).json()) <= 1

        # 全链路创建：project -> epic -> story -> task
        p = c.post("/api/projects", json={"name": "API-P", "key": "AP"}).json()
        e = c.post(f"/api/projects/{p['id']}/epics", json={"title": "E"}).json()
        st = c.post(f"/api/epics/{e['id']}/stories", json={"title": "S"}).json()
        t = c.post(f"/api/stories/{st['id']}/tasks",
                   json={"project_id": p["id"], "title": "Bug1", "type": "bug",
                         "priority": "highest"}).json()
        assert t["type"] == "bug" and t["priority"] == "highest"

        # 编辑各类 ticket
        assert c.patch(f"/api/projects/{p['id']}", json={"description": "d"}).json()["description"] == "d"
        assert c.patch(f"/api/epics/{e['id']}", json={"status": "todo"}).json()["status"] == "todo"
        assert c.patch(f"/api/stories/{st['id']}", json={"title": "S2"}).json()["title"] == "S2"
        r = c.patch(f"/api/tasks/{t['id']}", json={"spec": "# spec", "priority": "low"})
        assert r.json()["spec"] == "# spec" and r.json()["priority"] == "low"
        assert c.patch(f"/api/tasks/{t['id']}", json={"priority": "urgent"}).status_code == 422

        # 人类和 Agent 共用评论流
        cm = c.post(f"/api/tasks/{t['id']}/comments",
                    json={"author": "workbuddy", "content": "已完成复现"})
        assert cm.status_code == 201
        comments = c.get(f"/api/tasks/{t['id']}/comments").json()
        assert len(comments) == 1 and comments[0]["author"] == "workbuddy"
        assert c.delete(f"/api/comments/{comments[0]['id']}").status_code == 200

        # 状态流转（合法/非法）
        assert c.put(f"/api/tasks/{t['id']}/status", json={"status": "todo"}).status_code == 200
        assert c.put(f"/api/tasks/{t['id']}/status", json={"status": "done"}).status_code == 400

        # 搜索
        assert len(c.get("/api/tasks", params={"q": "spec", "priority": "low"}).json()) >= 1

        # 删除
        assert c.delete(f"/api/tasks/{t['id']}").status_code == 200
        assert c.get(f"/api/tasks/{t['id']}").status_code == 404


def test_web_serving():
    from fastapi.testclient import TestClient
    from agentboard.web_app import app
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "AgentBoard" in r.text
        assert "__API_URL__" not in r.text  # 已注入
        assert c.get("/static/app.js").status_code == 200


def test_generate_from_spec():
    from fastapi.testclient import TestClient
    from agentboard.api import app
    with TestClient(app) as c:
        p = c.post("/api/projects", json={"name": "G1"}).json()
        e = c.post(f"/api/projects/{p['id']}/epics", json={"title": "E"}).json()
        st = c.post(f"/api/epics/{e['id']}/stories", json={"title": "S"}).json()
        t = c.post(f"/api/stories/{st['id']}/tasks",
                   json={"project_id": p["id"], "title": "Spec 任务",
                         "spec": "# 提案\n## 任务清单\n- [ ] 子任务A\n- [ ] 子任务B\n"}).json()
        created = c.post(f"/api/tasks/{t['id']}/generate-subtasks").json()
        assert len(created) == 2
        assert created[0]["source_spec_id"] == t["id"]
        # 源 spec 已回写链接
        upd = c.get(f"/api/tasks/{t['id']}").json()
        assert "生成的子任务" in upd["spec"]
        # MCP db 后端也可生成
        from agentboard import mcp_server as m
        again = m._task_generated(t["id"])
        assert again == []  # 同一 spec 重复调用保持幂等


def test_domain_validation():
    from fastapi.testclient import TestClient
    from agentboard.api import app
    with TestClient(app) as c:
        p1 = c.post("/api/projects", json={"name": "P1"}).json()
        p2 = c.post("/api/projects", json={"name": "P2"}).json()
        ep = c.post(f"/api/projects/{p1['id']}/epics", json={"title": "E"}).json()
        st = c.post(f"/api/epics/{ep['id']}/stories", json={"title": "S"}).json()

        cross = c.post(f"/api/stories/{st['id']}/tasks", json={
            "project_id": p2["id"], "title": "wrong project",
        })
        assert cross.status_code == 422
        invalid_type = c.post(f"/api/stories/{st['id']}/tasks", json={
            "project_id": p1["id"], "title": "wrong type", "type": "anything",
        })
        assert invalid_type.status_code == 422
        assert c.patch(f"/api/epics/{ep['id']}", json={"status": "anything"}).status_code == 422
        assert c.get("/api/projects", params={"limit": 201}).status_code == 422

        weak = c.post("/api/auth/register", json={"username": "weak", "password": "short"})
        assert weak.status_code == 422


def test_mcp_extra_and_pagination():
    from agentboard import mcp_server as m
    assert m.BACKEND == "db"
    proj = m._proj_create("MCP-X", None, "")
    epic = m._epic_create(proj["id"], "E", "")
    story = m._story_create(epic["id"], "S", "")
    task = m._task_create(proj["id"], story["id"], "Agent task", "task", "", "", "high")
    assert task["priority"] == "high"
    comment = m._comment_create(task["id"], "qoder", "处理中")
    assert comment["author"] == "qoder"
    assert m._comment_list(task["id"])[0]["id"] == comment["id"]
    assert m._comment_delete(comment["id"])["ok"] is True
    # get / update epic
    assert m._epic_get(epic["id"])["id"] == epic["id"]
    assert m._epic_update(epic["id"], {"status": "todo"})["status"] == "todo"
    # get / update story
    assert m._story_get(story["id"])["id"] == story["id"]
    assert m._story_update(story["id"], {"title": "S2"})["title"] == "S2"
    # 分页
    assert isinstance(m._proj_list(limit=1), list)
    # 删除（级联）
    assert m._epic_delete(epic["id"]).get("ok") is True


def test_mcp_db_backend():
    from agentboard import mcp_server as m
    assert m.BACKEND == "db"
    proj = m._proj_create("MCP-P", None, "")
    assert proj["id"] > 0
    rows = m._proj_list()
    assert any(x["id"] == proj["id"] for x in rows)


if __name__ == "__main__":
    test_password_hash_compatibility()
    test_service_layer()
    test_rest_api()
    test_web_serving()
    test_generate_from_spec()
    test_domain_validation()
    test_mcp_extra_and_pagination()
    test_mcp_db_backend()
    print("SMOKE OK")
