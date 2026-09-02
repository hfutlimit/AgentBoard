"""Implementation Plan T3.1/T3.2 · 候选不足 blocked + 解锁钩子 回归测试。

为什么 blocked 而不是一直留在 todo 排队：看板必须能区分「排队等调度」和
「根本没人能干」。前者是正常流，后者是异常态 —— 混在一起，owner 看不出
自己的 agent 掉线了，任务在 todo 里一躺好几天。

为什么解锁目标不自定（R10）：状态机进 blocked 时会记 ``previous_status``，
出 blocked 按它恢复 —— 这套已经实现好（state_machine 注册了 4 个解锁迁移），
T3.2 直接复用，不自己决定恢复成 todo 还是 in_progress。

为什么「能不能跑」复用 _pick_implementation_agent 判定：它包含在线/enabled/
runnable instance/capability/动态排斥全套，与派发口径一致。单独写一套
「agent 可用」判断必然漂移。

运行：
    PYTHONPATH=src/backend-fastapi python -m pytest tests/test_m3_insufficient_agents.py -q
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

import json  # noqa: E402

from sqlalchemy import text  # noqa: E402

from agentboard import service  # noqa: E402
from agentboard.core.common.enums import Status, StatusReason  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402
from agentboard.features.scheduling.service import (  # noqa: E402
    dispatch_implementation_task, unblock_insufficient_agent_tasks,
)

init_db()

_SEQ = itertools.count(1)


def _register_offline_agent(s, user_id, label):
    """注册一个**不上线**的 agent（无 worker/instance）。"""
    n = next(_SEQ)
    aid = f"m3-{label}-{n}"
    service.register_agent(s, agent_id=aid, name=label, roles='["developer"]',
                           user_id=user_id)
    s.flush()
    from agentboard.features.projects.models import Agent
    return s.query(Agent).filter(Agent.agent_id == aid).first()


def _bring_agent_online(s, agent):
    """让 agent 上线：worker + runnable instance + 自报心跳。"""
    n = next(_SEQ)
    wid = f"m3-w-{agent.agent_id}-{n}"
    service.register_worker(s, worker_id=wid, hostname="test")
    inst = service.upsert_agent_instance(s, worker_id=wid,
                                         agent_id=agent.agent_id,
                                         executor_type="fake")
    service.instance_heartbeat(s, inst.id, caller_worker_id=wid, probe_ok=True)


def _seed():
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"m3 P{n}")
        owner = service.register_user(s, username=f"m3-owner{n}",
                                      password="password123")
        service.add_project_member(s, project_id=p.id, user_id=owner.id,
                                   role="member")
        epic = service.create_epic(s, project_id=p.id, title=f"m3 E{n}")
        st = service.create_story(s, epic_id=epic.id, title=f"m3 S{n}",
                                  created_by_user_id=owner.id,
                                  create_default_tasks=False)
        t = service.create_task(s, project_id=p.id, story_id=st.id,
                                title=f"m3 T{n}", type="dev",
                                created_by_user_id=owner.id)
        s.commit()
        return {"pid": p.id, "owner": owner.id, "story": st.id, "task": t.id}


def _deferred(s, task_id):
    raw = s.get(service.Task, task_id).assignment_deferred_reason
    return json.loads(raw) if raw else None


# ---------- T3.1 ----------

def test_dispatch_no_agent_blocks_task():
    """owner 名下无在线 agent → 走状态机转 blocked，带 insufficient_agents。"""
    d = _seed()
    with SessionLocal() as s:
        assert dispatch_implementation_task(s, d["task"]) is None
        t = s.get(service.Task, d["task"])
        assert t.status == "blocked"
        assert t.status_reason == StatusReason.INSUFFICIENT_AGENTS.value
        # 状态机 entry side-effect：记录 previous_status 供 T3.2 恢复
        assert t.previous_status == "todo"
        # deferred reason 仍保留（排障细节）
        assert _deferred(s, d["task"])["code"] == "no_runnable_agent"
        assert _deferred(s, d["task"])["owner_user_id"] == d["owner"]


def test_dispatch_no_owner_stays_todo_not_blocked():
    """owner 为 NULL → 保持 todo（人工补 owner），不转 blocked。

    blocked 后没有任何自动恢复路径（解锁钩子按 owner 找 agent，owner 都没有
    找谁），反而把待办藏起来 —— 这是刻意的不对称。
    """
    d = _seed()
    with SessionLocal() as s:
        t = s.get(service.Task, d["task"])
        t.owner_user_id = None
        s.commit()
        assert dispatch_implementation_task(s, d["task"]) is None
        fresh = s.get(service.Task, d["task"])
        assert fresh.status == "todo"
        assert fresh.status_reason is None
        assert _deferred(s, d["task"])["code"] == "no_owner"


def test_blocked_task_distinguishes_from_queued():
    """看板口径：blocked(insufficient_agents) 与排队 todo 可区分。"""
    d = _seed()
    with SessionLocal() as s:
        dispatch_implementation_task(s, d["task"])
        rows = s.execute(text(
            "SELECT status, status_reason FROM tasks WHERE id = :i"),
            {"i": d["task"]}).all()
        assert rows[0] == ("blocked", StatusReason.INSUFFICIENT_AGENTS.value)


# ---------- T3.2 ----------

def test_agent_online_unblocks_to_previous_status():
    """agent 上线 → 解锁钩子按 previous_status 恢复 todo。"""
    d = _seed()
    with SessionLocal() as s:
        agent = _register_offline_agent(s, d["owner"], "a")
        assert dispatch_implementation_task(s, d["task"]) is None
        assert s.get(service.Task, d["task"]).status == "blocked"
        # agent 上线：worker + instance + 心跳（instance 路径触发 _sync_agent_online）
        _bring_agent_online(s, agent)
        fresh = s.get(service.Task, d["task"])
        assert fresh.status == "todo", "agent 上线后应自动恢复"
        assert fresh.previous_status is None, "出 blocked 后 previous_status 清空"
        assert fresh.assignment_deferred_reason is None


def test_unblock_restores_previous_status_not_always_todo():
    """R10：恢复目标不自定。in_review 时被 block → 恢复回 in_review。"""
    d = _seed()
    with SessionLocal() as s:
        # 造一个 in_review 的 task，手动以 insufficient_agents block
        t = s.get(service.Task, d["task"])
        t.status = "in_review"
        t.reviewer_id = d["owner"]
        s.commit()
        service.set_status(
            s, d["task"], "blocked",
            status_reason=StatusReason.INSUFFICIENT_AGENTS.value,
            reason="test: simulating scheduling block from in_review")
        assert s.get(service.Task, d["task"]).previous_status == "in_review"
        # owner 无在线 agent → 解锁钩子不动它
        assert unblock_insufficient_agent_tasks(s, d["owner"]) == 0
        # agent 上线 → 恢复 in_review，不是 todo
        agent = _register_offline_agent(s, d["owner"], "rv")
        _bring_agent_online(s, agent)
        fresh = s.get(service.Task, d["task"])
        assert fresh.status == "in_review"
        assert fresh.previous_status is None


def test_unblock_skips_when_still_no_runnable_agent():
    """agent 在线但没有 runnable instance → 不解锁（不为凑数放行）。"""
    d = _seed()
    with SessionLocal() as s:
        agent = _register_offline_agent(s, d["owner"], "a")
        dispatch_implementation_task(s, d["task"])
        assert s.get(service.Task, d["task"]).status == "blocked"
        # 只把 agent 置 online，不给 runnable instance → 派发口径判定无候选
        service.agent_heartbeat(s, agent.agent_id, user_id=d["owner"])
        assert s.get(service.Task, d["task"]).status == "blocked"


def test_unblock_skips_human_blocked_tasks():
    """人工 blocked（其他 reason）不被解锁钩子碰 —— 它只管 insufficient_agents。"""
    d = _seed()
    with SessionLocal() as s:
        service.set_status(
            s, d["task"], "blocked",
            status_reason=StatusReason.OUT_OF_SCOPE.value,
            reason="test: human decision")
        agent = _register_offline_agent(s, d["owner"], "a")
        _bring_agent_online(s, agent)
        fresh = s.get(service.Task, d["task"])
        assert fresh.status == "blocked", "人工 blocked 不被自动恢复"
        assert fresh.status_reason == StatusReason.OUT_OF_SCOPE.value


def test_unblock_ignores_other_owners_tasks():
    """解锁只针对该 owner 名下的 task，别人的 blocked 不动。"""
    d = _seed()
    n = next(_SEQ)
    with SessionLocal() as s:
        stranger = service.register_user(s, username=f"m3-str{n}",
                                         password="password123")
        service.add_project_member(s, project_id=d["pid"], user_id=stranger.id,
                                   role="member")
        t2 = service.create_task(s, project_id=d["pid"], story_id=d["story"],
                                 title=f"m3 T2-{n}", type="dev",
                                 created_by_user_id=stranger.id)
        s.commit()
        dispatch_implementation_task(s, t2.id)
        assert s.get(service.Task, t2.id).status == "blocked"
        # owner（不是 stranger）的 agent 上线 → stranger 的 task 不动
        agent = _register_offline_agent(s, d["owner"], "a")
        _bring_agent_online(s, agent)
        assert s.get(service.Task, t2.id).status == "blocked"


def test_unblock_respects_capability_gate():
    """在线 agent 但能力不达标 → 不解锁（与派发口径一致）。

    注：这里原本想测「动态排斥」——但自审排斥只作用于 review 工作负载，
    dev 派发不受排斥，解锁反而是对的。改成能力不匹配这个等价场景：
    task 要求 agent 没有的 capability，rank 判不合格，候选池为空。
    """
    d = _seed()
    with SessionLocal() as s:
        # task 声明 agent 不具备的能力
        t = s.get(service.Task, d["task"])
        t.needed_capabilities = json.dumps(["no-such-cap"],
                                           ensure_ascii=False)
        s.commit()
        agent = _register_offline_agent(s, d["owner"], "a")
        dispatch_implementation_task(s, d["task"])
        assert s.get(service.Task, d["task"]).status == "blocked"
        # agent 上线，但能力不匹配 → 仍无合格候选 → 不解锁
        _bring_agent_online(s, agent)
        fresh = s.get(service.Task, d["task"])
        assert fresh.status == "blocked"
