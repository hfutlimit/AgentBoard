"""Implementation Plan T5.1/T5.2 · owner_transferred 通知 + 移交历史 回归测试。

三重白名单（DB CHECK / service valid_types / pydantic pattern）必须同批改 ——
本测试直接用 create_notification 发一条 owner_transferred，任何一层没改都会
在这里炸。历史表写入点：transfer_task / transfer_story / remove_project_member
三处（Plan T5.2 验收「可查谁/何时/哪个/从谁→谁」）。

运行：
    PYTHONPATH=src/backend-fastapi python -m pytest tests/test_m5_owner_transfer_history.py -q
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

from sqlalchemy import text  # noqa: E402

from agentboard import service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402
from agentboard.features.projects.models import OwnerTransferHistory  # noqa: E402
from agentboard.features.projects.service import record_owner_transfer  # noqa: E402

init_db()

_SEQ = itertools.count(1)


def _seed():
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"t5 P{n}")
        owner = service.register_user(s, username=f"t5-owner{n}",
                                      password="password123")
        insider = service.register_user(s, username=f"t5-in{n}",
                                        password="password123")
        service.add_project_member(s, project_id=p.id, user_id=owner.id,
                                   role="owner")
        service.add_project_member(s, project_id=p.id, user_id=insider.id,
                                   role="member")
        epic = service.create_epic(s, project_id=p.id, title=f"t5 E{n}")
        st = service.create_story(s, epic_id=epic.id, title=f"t5 S{n}",
                                  created_by_user_id=owner.id,
                                  create_default_tasks=False)
        t = service.create_task(s, project_id=p.id, story_id=st.id,
                                title=f"t5 T{n}", type="dev",
                                created_by_user_id=owner.id)
        s.commit()
        return {"pid": p.id, "owner": owner.id, "insider": insider.id,
                "story": st.id, "task": t.id}


def _history(s, entity_type, entity_id):
    return s.query(OwnerTransferHistory).filter(
        OwnerTransferHistory.entity_type == entity_type,
        OwnerTransferHistory.entity_id == entity_id,
    ).order_by(OwnerTransferHistory.id.desc()).all()


def _notifs(s, user_id, ntype="owner_transferred"):
    return s.execute(text(
        "SELECT title, content FROM notifications"
        " WHERE user_id = :u AND type = :t ORDER BY id DESC"),
        {"u": user_id, "t": ntype}).all()


# ---------- 1. 三重白名单 ----------

def test_owner_transferred_notification_passes_all_layers():
    """直接经 create_notification 发 owner_transferred —— DB CHECK /
    service valid_types 任意一层没改都在这里炸。"""
    d = _seed()
    with SessionLocal() as s:
        n = service.create_notification(
            s, user_id=d["insider"], notif_type="owner_transferred",
            title="移交提醒", content="x")
        s.commit()
        assert n.type == "owner_transferred"


# ---------- 2. 移交写历史 + 通知 ----------

def test_transfer_task_writes_history_and_notifies():
    d = _seed()
    with SessionLocal() as s:
        service.transfer_task(s, d["task"], d["insider"],
                              changed_by_user_id=d["owner"])
        rows = _history(s, "task", d["task"])
        assert len(rows) == 1
        r = rows[0]
        assert r.from_owner_user_id == d["owner"]
        assert r.to_owner_user_id == d["insider"]
        assert r.changed_by_user_id == d["owner"]
        assert r.project_id == d["pid"]
        # 新 owner 收到通知；旧 owner 不收
        assert _notifs(s, d["insider"]), "新 owner 应收到移交通知"
        assert not _notifs(s, d["owner"])


def test_transfer_story_writes_history_and_notifies():
    d = _seed()
    with SessionLocal() as s:
        service.transfer_story(s, d["story"], d["insider"],
                               changed_by_user_id=d["owner"])
        rows = _history(s, "story", d["story"])
        assert len(rows) == 1
        assert (rows[0].from_owner_user_id, rows[0].to_owner_user_id) == \
            (d["owner"], d["insider"])
        assert _notifs(s, d["insider"])


def test_transfer_history_queryable_who_when_what():
    """T5.2 验收：可查「谁/何时/哪个/从谁→谁」。"""
    d = _seed()
    with SessionLocal() as s:
        service.transfer_task(s, d["task"], d["insider"],
                              changed_by_user_id=d["owner"])
        rows = _history(s, "task", d["task"])
        r = rows[0]
        assert r.entity_type == "task"
        assert r.entity_id == d["task"]
        assert r.created_at is not None
        assert r.from_owner_user_id is not None
        assert r.to_owner_user_id is not None


# ---------- 3. 移除成员：逐条历史 + 合并通知 ----------

def test_member_removal_writes_per_item_history_and_one_notification():
    d = _seed()
    n = next(_SEQ)
    with SessionLocal() as s:
        mover = service.register_user(s, username=f"t5-mv{n}",
                                      password="password123")
        service.add_project_member(s, project_id=d["pid"], user_id=mover.id,
                                   role="member")
        epic = service.create_epic(s, project_id=d["pid"], title=f"t5 E2{n}")
        st = service.create_story(s, epic_id=epic.id, title=f"t5 S2{n}",
                                  created_by_user_id=mover.id,
                                  create_default_tasks=False)
        t = service.create_task(s, project_id=d["pid"], story_id=st.id,
                                title=f"t5 T2-{n}", type="dev",
                                created_by_user_id=mover.id)
        s.commit()
        tid, sid = t.id, st.id
        # mover 是上一个 session 的 ORM 对象，session 关了再取 .id 会
        # DetachedInstanceError —— 进 int 先
        mover_id = int(mover.id)

    with SessionLocal() as s:
        service.remove_project_member(s, d["pid"], mover_id)
        # 逐 item 历史
        assert len(_history(s, "task", tid)) == 1
        assert len(_history(s, "story", sid)) == 1
        # 合并通知：接收方恰好一条，不是每 item 一条
        notifs = _notifs(s, d["owner"])
        assert len(notifs) == 1, f"合并通知应为 1 条，实际 {len(notifs)}"
        assert "移交" in notifs[0][0]


# ---------- 4. 可回滚 ----------

def test_history_survives_transfer_idempotency():
    """重复移交产生多条历史（审计日志语义，不是状态覆盖）。"""
    d = _seed()
    with SessionLocal() as s:
        service.transfer_task(s, d["task"], d["insider"])
        service.transfer_task(s, d["task"], d["owner"])
        service.transfer_task(s, d["task"], d["insider"])
        rows = _history(s, "task", d["task"])
        assert len(rows) == 3
        # 最新一条是当前归属
        assert rows[0].to_owner_user_id == d["insider"]
        assert rows[0].from_owner_user_id == d["owner"]
