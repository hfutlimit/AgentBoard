"""Implementation Plan T2.2/T2.3 · 移交 API + 移除成员移交 回归测试。

Jason 拍板（2026-09-02）：**移交免确认、即生效**，通知与历史是 P2（T5.1/T5.2），
本文件只锁移交本身的行为与不变量。

关键不变量：
1. 移交只改 ``owner_user_id``，``created_by_user_id`` 纹丝不动 —— 后者是审计列；
2. 新 owner 必须是项目成员（T1.4 起维护的「owner ∈ ProjectMember」）；
3. 移除成员时，其名下 task/story 随移除移交 project owner —— 否则会留下
   owner 指向非成员的孤儿 item，且执行门不会拦（它只判 owner 相等）；
4. 最后一个 owner 不可删（原护栏，回归确认）。

运行：
    PYTHONPATH=src/backend-fastapi python -m pytest tests/test_m2_transfer.py -q
"""
import itertools
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "src", "backend-fastapi")
sys.path.insert(0, _ROOT)
sys.path.insert(0, _BACKEND)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from fastapi.testclient import TestClient  # noqa: E402

from agentboard import api, auth, service  # noqa: E402
from sqlalchemy import text  # noqa: E402

from agentboard.database import SessionLocal, init_db  # noqa: E402

init_db()

_SEQ = itertools.count(1)


def _client():
    return TestClient(api.app)


def _hdr(uid):
    return {"Authorization": f"Bearer {auth.make_token(uid)}"}


def _seed():
    """owner + insider（成员）+ outsider（非成员）+ story + task。"""
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"tr P{n}")
        owner = service.register_user(s, username=f"tr-owner{n}",
                                      password="password123")
        insider = service.register_user(s, username=f"tr-in{n}",
                                        password="password123")
        outsider = service.register_user(s, username=f"tr-out{n}",
                                         password="password123")
        service.add_project_member(s, project_id=p.id, user_id=owner.id,
                                   role="owner")
        service.add_project_member(s, project_id=p.id, user_id=insider.id,
                                   role="member")
        epic = service.create_epic(s, project_id=p.id, title=f"tr E{n}")
        st = service.create_story(s, epic_id=epic.id, title=f"tr S{n}",
                                  created_by_user_id=owner.id)
        t = service.create_task(s, project_id=p.id, story_id=st.id,
                                title=f"tr T{n}", type="dev",
                                created_by_user_id=owner.id)
        s.commit()
        return {
            "pid": p.id, "owner": owner.id, "insider": insider.id,
            "outsider": outsider.id, "story": st.id, "task": t.id,
        }


# ---------- 1. T2.3 service 层移交 ----------

def test_transfer_task_changes_owner_not_creator():
    d = _seed()
    with SessionLocal() as s:
        task, previous = service.transfer_task(
            s, d["task"], d["insider"], changed_by_user_id=d["owner"])
        assert task.owner_user_id == d["insider"]
        assert previous == d["owner"]
        assert task.created_by_user_id == d["owner"], "审计列不可变"


def test_transfer_story_changes_owner_not_creator():
    d = _seed()
    with SessionLocal() as s:
        st, previous = service.transfer_story(
            s, d["story"], d["insider"], changed_by_user_id=d["owner"])
        assert st.owner_user_id == d["insider"]
        assert previous == d["owner"]
        assert st.created_by_user_id == d["owner"]


def test_transfer_task_rejects_non_member():
    """新 owner 不是项目成员 → 拒绝（owner ∈ ProjectMember 不变量）。"""
    d = _seed()
    with SessionLocal() as s:
        with pytest.raises(service.InvalidValue, match="not a member"):
            service.transfer_task(s, d["task"], d["outsider"])
        # 失败不移交
        assert service.get_task(s, d["task"]).owner_user_id == d["owner"]


def test_transfer_story_rejects_non_member():
    d = _seed()
    with SessionLocal() as s:
        with pytest.raises(service.InvalidValue, match="not a member"):
            service.transfer_story(s, d["story"], d["outsider"])


def test_transfer_keeps_inflight_assignment():
    """在途 run 跑完再转：移交不动 assignee / current_assignment。"""
    d = _seed()
    with SessionLocal() as s:
        t = service.get_task(s, d["task"])
        t.assignee_id = d["owner"]  # 模拟在途
        s.commit()
        service.transfer_task(s, d["task"], d["insider"],
                              changed_by_user_id=d["owner"])
        fresh = service.get_task(s, d["task"])
        assert fresh.assignee_id == d["owner"], "在途执行不被移交打断"
        assert fresh.owner_user_id == d["insider"]


# ---------- 2. T2.3 HTTP 层 ----------

def test_api_transfer_by_owner_ok():
    d = _seed()
    r = _client().post(f"/api/tasks/{d['task']}/transfer",
                       headers=_hdr(d["owner"]),
                       json={"new_owner_user_id": d["insider"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["owner_user_id"] == d["insider"]
    assert body["previous_owner_user_id"] == d["owner"]


def test_api_transfer_by_project_owner_ok():
    """project owner 也能移交别人的 task（免确认的应有语义）。"""
    d = _seed()
    r = _client().post(f"/api/tasks/{d['task']}/transfer",
                       headers=_hdr(d["owner"]),
                       json={"new_owner_user_id": d["insider"]})
    assert r.status_code == 200, r.text


def test_api_transfer_by_unrelated_user_403():
    """非成员/无关用户 → 403。"""
    d = _seed()
    r = _client().post(f"/api/tasks/{d['task']}/transfer",
                       headers=_hdr(d["outsider"]),
                       json={"new_owner_user_id": d["insider"]})
    assert r.status_code == 403, r.text


def test_api_transfer_to_non_member_422():
    d = _seed()
    r = _client().post(f"/api/tasks/{d['task']}/transfer",
                       headers=_hdr(d["owner"]),
                       json={"new_owner_user_id": d["outsider"]})
    assert r.status_code == 422, r.text
    assert "not a member" in r.json()["detail"]


def test_api_transfer_story_ok():
    d = _seed()
    r = _client().post(f"/api/stories/{d['story']}/transfer",
                       headers=_hdr(d["owner"]),
                       json={"new_owner_user_id": d["insider"]})
    assert r.status_code == 200, r.text
    assert r.json()["owner_user_id"] == d["insider"]


# ---------- 3. T2.2 移除成员 → 移交 ----------

def _seed_multi():
    """project owner + 普通成员 + 被移除者（名下有 task/story）。"""
    d = _seed()
    n = next(_SEQ)
    with SessionLocal() as s:
        mover = service.register_user(s, username=f"tr-mover{n}",
                                      password="password123")
        service.add_project_member(s, project_id=d["pid"], user_id=mover.id,
                                   role="member")
        # mover 名下的 task / story
        epic = service.create_epic(s, project_id=d["pid"], title=f"tr E2{n}")
        # create_default_tasks=False：create_story 默认会自动建 design/dev 两个
        # task，不关掉的话 mover 名下 task 数是 3 而不是 1，断言没法写死
        st = service.create_story(s, epic_id=epic.id, title=f"tr S2{n}",
                                  created_by_user_id=mover.id,
                                  create_default_tasks=False)
        t = service.create_task(s, project_id=d["pid"], story_id=st.id,
                                title=f"tr T2{n}", type="dev",
                                created_by_user_id=mover.id)
        s.commit()
        d.update({"mover": mover.id, "mover_story": st.id, "mover_task": t.id})
    return d


def test_remove_member_transfers_owned_items():
    """移除成员 → 其 owned task/story 移交 project owner（T2.2 核心验收）。"""
    d = _seed_multi()
    with SessionLocal() as s:
        result = service.remove_project_member(s, d["pid"], d["mover"])
        assert result["transferred_tasks"] == 1
        assert result["transferred_stories"] == 1
        assert result["receiver"] == d["owner"]
        assert service.get_task(s, d["mover_task"]).owner_user_id == d["owner"]
        assert service.get_story(s, d["mover_story"]).owner_user_id == d["owner"]
        # 不变量：owner ∈ ProjectMember 依然成立
        bad = s.execute(text(
            "SELECT COUNT(*) FROM tasks t WHERE t.project_id = :p"
            " AND t.owner_user_id IS NOT NULL AND NOT EXISTS ("
            " SELECT 1 FROM project_members pm WHERE pm.project_id = t.project_id"
            " AND pm.user_id = t.owner_user_id)"), {"p": d["pid"]}).scalar()
        assert bad == 0


from sqlalchemy import text  # noqa: E402


def test_remove_member_without_receiver_blocked():
    """项目没有其他 owner 可接收 → 拒绝移除（不留孤儿）。"""
    d = _seed_multi()
    with SessionLocal() as s:
        # 把 owner 降成 member → 项目里没有任何 owner
        s.query(service.ProjectMember).filter(
            service.ProjectMember.project_id == d["pid"],
            service.ProjectMember.user_id == d["owner"],
        ).update({"role": "member"}, synchronize_session=False)
        s.commit()
        with pytest.raises(service.InvalidValue, match="no other owner"):
            service.remove_project_member(s, d["pid"], d["mover"])
        # 移除没发生
        assert service.user_is_project_member(s, d["pid"], d["mover"])


def test_remove_last_owner_still_blocked():
    """原护栏回归：最后一个 owner 不可删。"""
    d = _seed()
    with SessionLocal() as s:
        with pytest.raises(service.InvalidValue,
                           match="cannot remove the last owner"):
            service.remove_project_member(s, d["pid"], d["owner"])


def test_remove_owner_transfers_to_surviving_owner():
    """多 owner 场景：删掉其中一个，其 item 移给**剩余** owner（排除法）。"""
    d = _seed_multi()
    n = next(_SEQ)
    with SessionLocal() as s:
        # 再加一个 owner2（比 owner 晚加入）
        owner2 = service.register_user(s, username=f"tr-o2{n}",
                                       password="password123")
        service.add_project_member(s, project_id=d["pid"], user_id=owner2.id,
                                   role="owner")
        # owner 名下放一个 task
        epic = service.create_epic(s, project_id=d["pid"], title=f"tr E3{n}")
        st = service.create_story(s, epic_id=epic.id, title=f"tr S3{n}",
                                  created_by_user_id=d["owner"])
        t = service.create_task(s, project_id=d["pid"], story_id=st.id,
                                title=f"tr T3{n}", type="dev",
                                created_by_user_id=d["owner"])
        s.commit()
        result = service.remove_project_member(s, d["pid"], d["owner"])
        assert result["receiver"] == owner2.id, "接收方 = 剩余 owner"
        assert service.get_task(s, t.id).owner_user_id == owner2.id


def test_remove_member_with_no_items_needs_no_receiver():
    """被移除者名下没有 item → 即使没有其他 owner 也允许纯移除。"""
    d = _seed()
    n = next(_SEQ)
    with SessionLocal() as s:
        looser = service.register_user(s, username=f"tr-loose{n}",
                                       password="password123")
        service.add_project_member(s, project_id=d["pid"], user_id=looser.id,
                                   role="member")
        # 把 owner 降级 → 项目无 owner
        s.query(service.ProjectMember).filter(
            service.ProjectMember.project_id == d["pid"],
            service.ProjectMember.user_id == d["owner"],
        ).update({"role": "member"}, synchronize_session=False)
        s.commit()
        result = service.remove_project_member(s, d["pid"], looser.id)
        assert result["transferred_tasks"] == 0
        assert result["transferred_stories"] == 0
