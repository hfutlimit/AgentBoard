"""AgentBoard smoke test：前后端分离三端验证（service / REST API / Web / MCP）。

运行：PYTHONPATH=. python tests/test_smoke.py
"""
import os
import tempfile

# 必须在导入 agentboard 之前设置临时 SQLite 与 MCP 后端
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"  # MCP 直连 DB，无需启动 API

from agentboard.database import init_db, SessionLocal
from agentboard import service
from agentboard.models import ItemType, Status


def test_service_layer():
    init_db()
    with SessionLocal() as s:
        p = service.create_project(s, name="Demo", key="DEMO", description="# Demo")
        ep = service.create_epic(s, project_id=p.id, title="E1")
        st = service.create_story(s, epic_id=ep.id, title="S1")
        t = service.create_task(s, project_id=p.id, story_id=st.id, type=ItemType.TASK, title="T1")
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
        # epic / story 更新
        assert service.update_epic(s, ep.id, status=Status.TODO).status == Status.TODO
        assert service.update_story(s, st.id, title="S1-x").title == "S1-x"


def test_rest_api():
    from fastapi.testclient import TestClient
    from agentboard.api import app
    with TestClient(app) as c:
        assert c.get("/api/meta").json()["types"] == ["task", "bug"]

        # 全链路创建：project -> epic -> story -> task
        p = c.post("/api/projects", json={"name": "API-P", "key": "AP"}).json()
        e = c.post(f"/api/projects/{p['id']}/epics", json={"title": "E"}).json()
        st = c.post(f"/api/epics/{e['id']}/stories", json={"title": "S"}).json()
        t = c.post(f"/api/stories/{st['id']}/tasks",
                   json={"project_id": p["id"], "title": "Bug1", "type": "bug"}).json()
        assert t["type"] == "bug"

        # 编辑各类 ticket
        assert c.patch(f"/api/projects/{p['id']}", json={"description": "d"}).json()["description"] == "d"
        assert c.patch(f"/api/epics/{e['id']}", json={"status": "todo"}).json()["status"] == "todo"
        assert c.patch(f"/api/stories/{st['id']}", json={"title": "S2"}).json()["title"] == "S2"
        r = c.patch(f"/api/tasks/{t['id']}", json={"spec": "# spec"})
        assert r.json()["spec"] == "# spec"

        # 状态流转（合法/非法）
        assert c.put(f"/api/tasks/{t['id']}/status", json={"status": "todo"}).status_code == 200
        assert c.put(f"/api/tasks/{t['id']}/status", json={"status": "done"}).status_code == 400

        # 搜索
        assert len(c.get("/api/tasks", params={"q": "spec"}).json()) >= 1

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
        assert "生成的自任务" in upd["spec"]
        # MCP db 后端也可生成
        from agentboard import mcp_server as m
        again = m._task_generated(t["id"])
        assert isinstance(again, list)


def test_mcp_db_backend():
    from agentboard import mcp_server as m
    assert m.BACKEND == "db"
    proj = m._proj_create("MCP-P", None, "")
    assert proj["id"] > 0
    rows = m._proj_list()
    assert any(x["id"] == proj["id"] for x in rows)


if __name__ == "__main__":
    test_service_layer()
    test_rest_api()
    test_web_serving()
    test_generate_from_spec()
    test_mcp_db_backend()
    print("SMOKE OK")
