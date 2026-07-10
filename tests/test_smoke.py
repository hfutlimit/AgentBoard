"""AgentBoard smoke test：数据层 + service + Web API 基本可用。

运行：python tests/test_smoke.py
（CI 亦可：python -m pytest tests/ -q）
"""
import os
import tempfile

# 必须在导入 agentboard 之前设置临时 SQLite，避免污染本地库
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{tempfile.mktemp(suffix='.db')}"

from agentboard.database import init_db, SessionLocal
from agentboard import service
from agentboard.models import ItemType, Status


def test_crud_and_spec():
    init_db()
    with SessionLocal() as s:
        p = service.create_project(s, name="Demo", key="DEMO", description="# Demo")
        assert p.id > 0
        ep = service.create_epic(s, project_id=p.id, title="E1")
        st = service.create_story(s, epic_id=ep.id, title="S1")
        t = service.create_task(s, project_id=p.id, story_id=st.id,
                                type=ItemType.TASK, title="T1")
        service.set_task_spec(s, t.id, "## Spec\n做这件事")
        got = service.get_task(s, t.id)
        assert got.spec.startswith("## Spec")

        # 状态迁移
        service.set_status(s, t.id, Status.TODO)
        service.set_status(s, t.id, Status.IN_PROGRESS)

        # 非法迁移应失败
        try:
            service.set_status(s, t.id, Status.BACKLOG)  # IN_PROGRESS -> BACKLOG 不合法
            assert False, "illegal transition 未拦截"
        except service.IllegalTransition:
            pass

        # 搜索
        res = service.search_tasks(s, project_id=p.id, q="这件事")
        assert len(res) >= 1

        # 级联删除
        assert service.delete_project(s, p.id) is True
        assert service.get_project(s, p.id) is None


def test_api():
    from fastapi.testclient import TestClient
    from agentboard.api import app
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "AgentBoard" in r.text


if __name__ == "__main__":
    test_crud_and_spec()
    test_api()
    print("SMOKE OK")
