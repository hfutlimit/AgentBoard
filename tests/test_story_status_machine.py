"""Ticket 全流程（2026-08-09）：Story 状态机工单化测试。

覆盖：
1. STORY_STATUSES / STORY_TRANSITIONS 定义（8 值 + 强制迁移）；
2. update_story 状态迁移校验（非法迁移 IllegalTransition，取值非法 InvalidValue）；
3. confirm_story：backlog→confirmed CAS、幂等、非 backlog 拒绝；
4. complete_story：任意非 done/blocked → done（自动收尾）、blocked 拒绝、幂等；
5. set_story_status：单步查表 + blocked 全向 + 状态历史记录；
6. story_status_history 记录与查询（含 changed_by/reason）；
7. 废弃端点语义：assign_reviewer / review_story 抛「评审已下线」。

运行：
    PYTHONPATH=. python -m pytest tests/test_story_status_machine.py -q
"""
import os
import sys
import tempfile
import uuid

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402
from agentboard.service import (  # noqa: E402
    IllegalTransition, InvalidValue, STORY_STATUSES, STORY_TRANSITIONS,
)

init_db()


def _seed():
    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as s:
        p = service.create_project(s, name=f"SSM-{tag}", key=f"SSM{tag[:6]}")
        e = service.create_epic(s, project_id=p.id, title="Epic")
        st = service.create_story(s, epic_id=e.id, title="Story")
        return p.id, e.id, st.id


def test_story_status_definition():
    assert STORY_STATUSES == {"backlog", "confirmed", "todo", "in_progress",
                              "in_review", "verifying", "done", "blocked"}
    assert "pending_review" not in STORY_STATUSES and "ready" not in STORY_STATUSES
    assert STORY_TRANSITIONS["backlog"] == {"confirmed", "blocked"}
    assert STORY_TRANSITIONS["confirmed"] == {"todo", "blocked"}
    assert STORY_TRANSITIONS["blocked"] == {"todo", "in_progress"}


def test_update_story_rejects_illegal_transition():
    _, _, sid = _seed()
    with SessionLocal() as s:
        with pytest.raises(IllegalTransition):
            service.update_story(s, sid, status="done")  # backlog→done 非法
        with pytest.raises(IllegalTransition):
            service.update_story(s, sid, status="in_progress")  # 跳过 confirm 闸门
        # 合法：backlog→confirmed
        st = service.update_story(s, sid, status="confirmed")
        assert st.status == "confirmed"


def test_update_story_rejects_invalid_value():
    _, _, sid = _seed()
    with SessionLocal() as s:
        with pytest.raises(InvalidValue):
            service.update_story(s, sid, status="pending_review")  # 已下线状态


def test_update_story_status_records_history():
    """PATCH status（update_story）亦记状态历史（所有写路径统一）。"""
    _, _, sid = _seed()
    with SessionLocal() as s:
        service.update_story(s, sid, status="confirmed")
        service.update_story(s, sid, status="todo")
        hist = service.list_story_status_history(s, sid)
        assert [(h.from_status, h.to_status) for h in hist] == [
            ("confirmed", "todo"),
            ("backlog", "confirmed"),
        ]


def test_confirm_story_cas_and_idempotent():
    _, _, sid = _seed()
    with SessionLocal() as s:
        st = service.confirm_story(s, sid)
        assert st.status == "confirmed"
        # 幂等：已 confirmed 直接返回
        st2 = service.confirm_story(s, sid)
        assert st2.status == "confirmed"
        # 历史两条？不——幂等不重复记录，只有 backlog→confirmed 一条
        hist = service.list_story_status_history(s, sid)
        assert [(h.from_status, h.to_status) for h in hist] == [("backlog", "confirmed")]


def test_confirm_story_rejects_non_backlog():
    _, _, sid = _seed()
    with SessionLocal() as s:
        # 合法路径绕开闸门：backlog → confirmed → todo
        service.confirm_story(s, sid)
        service.update_story(s, sid, status="todo")
        with pytest.raises(IllegalTransition):
            service.confirm_story(s, sid)


def test_complete_story_auto_finalize():
    _, _, sid = _seed()
    with SessionLocal() as s:
        service.confirm_story(s, sid)  # backlog→confirmed
        # confirmed → done 不在 TRANSITIONS，但 complete_story 允许（自动收尾）
        st = service.complete_story(s, sid)
        assert st.status == "done"
        hist = service.list_story_status_history(s, sid)
        assert [(h.from_status, h.to_status) for h in hist][0] == ("confirmed", "done")


def test_complete_story_rejects_blocked():
    _, _, sid = _seed()
    with SessionLocal() as s:
        service.update_story(s, sid, status="blocked")  # backlog→blocked 合法
        with pytest.raises(IllegalTransition):
            service.complete_story(s, sid)


def test_set_story_status_history_recorded():
    _, _, sid = _seed()
    with SessionLocal() as s:
        st = service.set_story_status(s, sid, "confirmed", reason="测试确认")
        assert st.status == "confirmed"
        st = service.set_story_status(s, sid, "todo", reason="开始开发")
        # 历史按 id 倒序返回（最新在前）
        hist = service.list_story_status_history(s, sid)
        assert [(h.from_status, h.to_status, h.reason) for h in hist] == [
            ("confirmed", "todo", "开始开发"),
            ("backlog", "confirmed", "测试确认"),
        ]


def test_set_story_status_blocked_all_directions():
    _, _, sid = _seed()
    with SessionLocal() as s:
        # 任意状态 → blocked 全向可达（backlog→blocked 亦合法）
        st = service.set_story_status(s, sid, "blocked", reason="突发阻塞")
        assert st.status == "blocked"
        # 解除 blocked 仅限 todo/in_progress
        with pytest.raises(IllegalTransition):
            service.set_story_status(s, sid, "done")
        st = service.set_story_status(s, sid, "todo")
        assert st.status == "todo"


def test_assign_reviewer_deprecated():
    _, _, sid = _seed()
    with SessionLocal() as s:
        with pytest.raises(InvalidValue, match="评审已下线"):
            service.assign_reviewer(s, sid)


def test_review_story_deprecated():
    _, _, sid = _seed()
    with SessionLocal() as s:
        with pytest.raises(InvalidValue, match="评审已下线"):
            service.review_story(s, story_id=sid, reviewer_user_id=1,
                                 verdict="approve", comment="x")
