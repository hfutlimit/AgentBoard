"""Implementation Plan T6.3 · WorkerProjectMapping 一级实体 回归测试。

设计要点（写测试前先想清楚要锁什么）：
1. (worker_id, project_id) 唯一 —— 一台机器对一个项目至多一条授权；
2. **不含 user_id**（T6.3 明确去冗余）：归属随 agent 走，映射只答
   「这台机器在不在项目工作面里」；
3. 引用完整性：worker_id / project_id 必须真实存在，悬空映射 404；
4. enabled 开关：临时下线一台机器不需要删映射重建。

运行：
    PYTHONPATH=src/backend-fastapi python -m pytest tests/test_m6_worker_project_mapping.py -q
"""
import itertools
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "src", "backend-fastapi")
sys.path.insert(0, _BACKEND)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from sqlalchemy.exc import IntegrityError  # noqa: E402

from agentboard import service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402
from agentboard.features.projects.models import WorkerProjectMapping  # noqa: E402

init_db()

_SEQ = itertools.count(1)


def _seed():
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"wpm P{n}")
        w = service.register_worker(s, worker_id=f"wpm-w{n}", hostname="test")
        s.commit()
        return {"pid": p.id, "worker_id": w.worker_id}


# ---------- 1. 基本映射 ----------

def test_map_and_query():
    d = _seed()
    with SessionLocal() as s:
        m = service.map_worker_to_project(s, worker_id=d["worker_id"],
                                          project_id=d["pid"])
        assert m.enabled is True
        assert service.worker_in_project(s, d["worker_id"], d["pid"])
        assert service.list_project_workers(s, d["pid"]) == [d["worker_id"]]


def test_map_is_idempotent():
    d = _seed()
    with SessionLocal() as s:
        m1 = service.map_worker_to_project(s, worker_id=d["worker_id"],
                                           project_id=d["pid"])
        m2 = service.map_worker_to_project(s, worker_id=d["worker_id"],
                                           project_id=d["pid"])
        assert m1.id == m2.id, "重复映射返回同一行，不是插第二行"


def test_unique_constraint_enforced_by_db():
    """绕过 service 直接插，DB 约束仍要拦（服务层校验不是唯一防线）。"""
    d = _seed()
    with SessionLocal() as s:
        service.map_worker_to_project(s, worker_id=d["worker_id"],
                                      project_id=d["pid"])
        s.add(WorkerProjectMapping(worker_id=d["worker_id"],
                                   project_id=d["pid"]))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()


def test_map_unknown_worker_or_project_404():
    """悬空映射拒绝创建 —— 否则「为什么派发不到」变成猜谜。"""
    d = _seed()
    with SessionLocal() as s:
        with pytest.raises(service.NotFound, match="worker"):
            service.map_worker_to_project(s, worker_id="ghost-worker",
                                          project_id=d["pid"])
        with pytest.raises(service.NotFound, match="project"):
            service.map_worker_to_project(s, worker_id=d["worker_id"],
                                          project_id=999999)


# ---------- 2. enabled 开关 ----------

def test_disable_then_enable_via_remap():
    d = _seed()
    with SessionLocal() as s:
        service.map_worker_to_project(s, worker_id=d["worker_id"],
                                      project_id=d["pid"])
        assert service.worker_in_project(s, d["worker_id"], d["pid"])
        # 临时下线：enabled=False（不删映射）
        service.map_worker_to_project(s, worker_id=d["worker_id"],
                                      project_id=d["pid"], enabled=False)
        assert not service.worker_in_project(s, d["worker_id"], d["pid"])
        assert service.list_project_workers(s, d["pid"]) == []
        # enabled_only=False 仍能查到（审计口径）
        assert service.list_project_workers(s, d["pid"], enabled_only=False) \
            == [d["worker_id"]]
        # 恢复
        service.map_worker_to_project(s, worker_id=d["worker_id"],
                                      project_id=d["pid"], enabled=True)
        assert service.worker_in_project(s, d["worker_id"], d["pid"])


# ---------- 3. 解除映射 ----------

def test_unmap_removes_row_and_is_idempotent():
    d = _seed()
    with SessionLocal() as s:
        service.map_worker_to_project(s, worker_id=d["worker_id"],
                                      project_id=d["pid"])
        assert service.unmap_worker_from_project(s, worker_id=d["worker_id"],
                                                 project_id=d["pid"]) is True
        assert service.unmap_worker_from_project(s, worker_id=d["worker_id"],
                                                 project_id=d["pid"]) is False
        assert service.list_project_workers(s, d["pid"]) == []


# ---------- 4. 多 worker 多 project ----------

def test_many_to_many_shape():
    """映射是 (worker, project) 多对多：一台机器多项目、一个项目多机器。"""
    d = _seed()
    n = next(_SEQ)
    with SessionLocal() as s:
        w2 = service.register_worker(s, worker_id=f"wpm-w2-{n}", hostname="t2")
        p2 = service.create_project(s, name=f"wpm P2-{n}")
        s.commit()
        service.map_worker_to_project(s, worker_id=d["worker_id"],
                                      project_id=p2.id)
        service.map_worker_to_project(s, worker_id=w2.worker_id,
                                      project_id=d["pid"])
        # 一台机器两个项目（w1: p1 是 _seed 里映射的？不 —— _seed 只建不映射）
        assert not service.worker_in_project(s, d["worker_id"], d["pid"]), \
            "没映射过就不该在"
        assert service.worker_in_project(s, d["worker_id"], p2.id)
        assert service.worker_in_project(s, w2.worker_id, d["pid"])
        # 一个项目一台机器（本测试只给 p1 映了 w2）；p2 只有 w1
        assert service.list_project_workers(s, d["pid"]) == [w2.worker_id]
        assert service.list_project_workers(s, p2.id) == [d["worker_id"]]


def test_model_has_no_user_id_column():
    """T6.3 明确去冗余 user_id —— 归属随 agent 走，映射表不该长这个列。"""
    cols = {c.name for c in WorkerProjectMapping.__table__.columns}
    assert "user_id" not in cols
    assert "worker_id" in cols and "project_id" in cols
