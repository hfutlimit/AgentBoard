"""AgentBoard · MariaDB 真实集成测试（可选）

验证 AgentBoard 在真实 MariaDB 11 下的建表 DDL 兼容性与服务层冒烟。
仅在设置 AGENTBOARD_TEST_MARIAODB 时运行，避免无 MariaDB 环境误跑。

前置：
  - 一个空的 MariaDB 数据库（不会 drop，仅在其上建表）。
  - pymysql 可达该库。

运行（指向一个专用测试库，不要指向生产库）：
  export AGENTBOARD_TEST_MARIAODB="mysql+pymysql://agentboard:agentboard@localhost:13306/agentboard_ci"
  PYTHONPATH=. python -m pytest tests/test_mariadb_integration.py -q

init_db() 会执行 Alembic upgrade head，并可幂等重跑。
"""
import os
import sys

import pytest

MARIADB_URL = os.environ.get("AGENTBOARD_TEST_MARIAODB")
skip_no_mariadb = pytest.mark.skipif(
    not MARIADB_URL,
    reason="set AGENTBOARD_TEST_MARIAODB=mysql+pymysql://... to run MariaDB integration tests",
)
pytestmark = skip_no_mariadb

if MARIADB_URL:
    os.environ["AGENTBOARD_DB_URL"] = MARIADB_URL
    os.environ["AGENTBOARD_MCP_BACKEND"] = "db"
    # 强制重载 agentboard，使 engine 绑定到上面的 MariaDB URL
    for _m in list(sys.modules):
        if _m == "agentboard" or _m.startswith("agentboard."):
            del sys.modules[_m]

    from agentboard.database import init_db, SessionLocal, engine
    from agentboard import models, service
    from agentboard.models import ItemType, Status, Priority


@pytest.fixture(scope="module")
def session():
    init_db()
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def test_all_tables_exist(session):
    """Alembic 在 MariaDB 上建出全部 6 张业务表。"""
    from sqlalchemy import inspect
    existing = set(inspect(engine).get_table_names())
    for t in ("projects", "epics", "stories", "tasks", "users", "comments"):
        assert t in existing, f"缺少表 {t}"


def test_crud_status_search_comments(session):
    """项目树 CRUD + 状态机 + 搜索 + 评论 + spec 生成子任务 + 级联删除。"""
    # project
    p = service.create_project(session, name="MariaDB 集成项目", key="MDB")
    assert p.id > 0 and p.key == "MDB"

    # epic / story
    ep = service.create_epic(session, project_id=p.id, title="Epic M")
    st = service.create_story(session, epic_id=ep.id, title="Story M")

    # task（type=task, priority=high）
    t = service.create_task(session, project_id=p.id, story_id=st.id,
                            title="接入 MariaDB", type=ItemType.TASK, priority=Priority.HIGH)
    assert t.type == ItemType.TASK and t.priority == Priority.HIGH

    # bug
    b = service.create_task(session, project_id=p.id, story_id=st.id,
                            title="连接超时", type=ItemType.BUG)
    assert b.type == ItemType.BUG

    # 状态机合法迁移
    assert service.set_status(session, t.id, Status.TODO).status == Status.TODO
    assert service.set_status(session, t.id, Status.IN_PROGRESS).status == Status.IN_PROGRESS
    # 非法迁移应抛 IllegalTransition
    with pytest.raises(service.IllegalTransition):
        service.set_status(session, t.id, Status.BACKLOG)

    # 搜索：按 type 过滤
    bugs = service.search_tasks(session, project_id=p.id, type=ItemType.BUG)
    assert len(bugs) == 1 and bugs[0].id == b.id
    # 搜索：按 priority 过滤
    highs = service.search_tasks(session, project_id=p.id, priority=Priority.HIGH)
    assert [x.id for x in highs] == [t.id]

    # 评论
    cm = service.create_comment(session, task_id=t.id, author="codex", content="实现中")
    assert cm.id > 0
    assert service.list_comments(session, t.id)[0].content == "实现中"

    # spec -> 子任务（OpenSpec 风格）
    service.set_task_spec(session, t.id, "- [ ] 建连接池\n- [ ] 加健康检查")
    subs = service.generate_tasks_from_spec(session, t.id)
    assert len(subs) == 2
    assert all(s.source_spec_id == t.id for s in subs)

    # 级联删除 project（epic/story/task/bug/comment 一并删除）
    # 先捕获整数主键，避免删除后访问过期实例的属性触发刷新
    p_id, ep_id, st_id, t_id, b_id = p.id, ep.id, st.id, t.id, b.id
    assert service.delete_project(session, p_id) is True
    # 用全新 session 校验（避免复用已过期的一级缓存实例）
    s2 = SessionLocal()
    try:
        assert s2.get(models.Project, p_id) is None
        assert s2.get(models.Epic, ep_id) is None
        assert s2.get(models.Story, st_id) is None
        assert s2.get(models.Task, t_id) is None
        assert s2.get(models.Task, b_id) is None
        # 评论随 task 删除
        assert s2.query(models.Comment).filter_by(task_id=t_id).first() is None
    finally:
        s2.close()
