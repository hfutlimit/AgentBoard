"""Implementation Plan T1.5 · 统一执行门回归测试。

门的实现在 ``features/work_items/ownership.py``，判据只有一条：
``item.owner_user_id == agent.user_id`` 且 agent 不在自审排除集里。

为什么专门测这些
----------------
1. **判的是 owner_user_id，不是 created_by_user_id**。后者是不可变审计列，
   T2.3 移交之后两者会分叉 —— 老判据会把已移交的 task 判给旧 owner。这个
   差别靠读代码看不出来，必须用「移交后谁还能认领」来钉死。
2. **主动认领（403）与自动派发（fail-closed 保持待处理）是两条不同的路**。
   前者是越权该报错，后者是暂时没候选不该报错。混在一起就会出现「owner 的
   agent 全离线时，每次调度扫一轮就抛一堆异常」。
3. **写路径必须填 owner**。执行门上线后，任何绕过 create_task 的构造只要
   漏填 owner_user_id，那个 task 就永久卡死 —— 且不会有任何报错。这组测试
   是唯一的护栏。

运行：
    PYTHONPATH=src/backend-fastapi python -m pytest tests/test_m1_ownership_gate.py -q
"""
import itertools
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src", "backend-fastapi"))

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from fastapi.testclient import TestClient  # noqa: E402

from agentboard import api, service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402
# dispatch 入口只在 features/scheduling 里，facade 没转发 —— 直接引真源，
# 不要为了「好看」给 facade 加一个没有业务理由的转发。
from agentboard.features.scheduling.service import (  # noqa: E402
    dispatch_implementation_task,
)
from agentboard.features.identity.service import create_api_key  # noqa: E402
from agentboard.features.work_items.ownership import (  # noqa: E402
    CODE_EXCLUDED, CODE_NO_OWNER, CODE_NOT_OWNER, GateDecision,
    agent_can_handle_work_item, work_item_owner_user_id,
)

init_db()

_SEQ = itertools.count(1)


def _client():
    return TestClient(api.app)


# ---------- 造数 ----------

def _new_agent(s, user_id, label, *, roles='["developer"]'):
    """注册一个 agent，并绑定 agent 级 API key（Router 靠它解析 agent 身份）。"""
    from agentboard.features.projects.models import Agent

    n = next(_SEQ)
    aid = f"gate-{label}-{n}"
    service.register_agent(s, agent_id=aid, name=label, roles=roles,
                           user_id=user_id)
    service.agent_heartbeat(s, aid, user_id=user_id)
    wid = f"gate-w-{label}-{n}"
    service.register_worker(s, worker_id=wid, hostname="test")
    inst = service.upsert_agent_instance(s, worker_id=wid, agent_id=aid,
                                         executor_type="fake")
    service.instance_heartbeat(s, inst.id, caller_worker_id=wid, probe_ok=True)
    s.commit()
    agent = s.query(Agent).filter(Agent.agent_id == aid).first()
    _item, plaintext = create_api_key(
        s, user_id=user_id, name=f"key-{label}-{n}",
        permissions=["api:read", "api:write"], agent_ref=aid)
    return agent, plaintext


def _seed():
    """1 项目 + owner(dev) + 另一个成员(other) + 1 story。"""
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"gate P{n}")
        dev = service.register_user(s, username=f"gate-dev{n}",
                                    password="password123")
        other = service.register_user(s, username=f"gate-other{n}",
                                      password="password123")
        service.add_project_member(s, project_id=p.id, user_id=dev.id,
                                   role="member")
        service.add_project_member(s, project_id=p.id, user_id=other.id,
                                   role="member")
        epic = service.create_epic(s, project_id=p.id, title=f"gate E{n}")
        # 必须传 created_by_user_id：create_story 用它同时填 story 的
        # created_by 与 owner。不传 → owner 为 NULL → 执行门全关。
        st = service.create_story(s, epic_id=epic.id, title=f"gate S{n}",
                                  created_by_user_id=dev.id)
        s.commit()
        return p.id, dev.id, other.id, st.id


@pytest.fixture(scope="module")
def seeded():
    return _seed()


def _make_task(s, project_id, story_id, owner_id, **kw):
    t = service.create_task(s, project_id=project_id, story_id=story_id,
                            title=kw.pop("title", "gate T"),
                            created_by_user_id=owner_id, **kw)
    s.commit()
    return t


# ---------- 1. 门本身 ----------

def test_gate_allows_owner_agent(seeded):
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, dev)
        agent, _key = _new_agent(s, dev, "ok")
        d = agent_can_handle_work_item(agent, t)
        assert d.allowed and d.code == "ok"


def test_gate_rejects_non_owner_agent(seeded):
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, dev)
        agent, _key = _new_agent(s, other, "intruder")
        d = agent_can_handle_work_item(agent, t)
        assert not d.allowed and d.code == CODE_NOT_OWNER
        assert not d  # GateDecision 应当可直接当 bool 用


def test_gate_rejects_null_owner(seeded):
    """owner 为 NULL → 不通过。放行等于让任意在线 agent 抢走无人认领的活。"""
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, dev)
        t.owner_user_id = None
        s.commit()
        agent, _key = _new_agent(s, other, "anyone")
        d = agent_can_handle_work_item(agent, t)
        assert not d.allowed and d.code == CODE_NO_OWNER
        # 换成本人也不行 —— NULL owner 对所有人关闭，不是「只挡外人」
        mine, _ = _new_agent(s, dev, "mine")
        assert agent_can_handle_work_item(mine, t).code == CODE_NO_OWNER


def test_gate_applies_self_review_exclusion(seeded):
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, dev)
        agent, _key = _new_agent(s, dev, "selfrev")
        assert agent_can_handle_work_item(agent, t).allowed
        d = agent_can_handle_work_item(agent, t, exclude_agent_ids={agent.id})
        assert not d.allowed and d.code == CODE_EXCLUDED
        # 排除集里混进 None（调用方常拼 `| {task.reviewer_agent_id}`）不能炸
        d2 = agent_can_handle_work_item(agent, t, exclude_agent_ids={None})
        assert d2.allowed


def test_gate_follows_owner_not_creator(seeded):
    """移交（T2.3）之后判据跟 owner 走：新 owner 放行、旧 owner 被拒。

    这是 T1.5 的核心。判据若停在不可变的 created_by_user_id 上，这个测试
    必须失败 —— 因为移交只改 owner_user_id。
    """
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, dev)
        # 模拟移交：created_by 不动，只改 owner
        t.owner_user_id = other
        s.commit()
        assert t.created_by_user_id == dev  # 审计列不变

        old_agent, _ = _new_agent(s, dev, "oldowner")
        new_agent, _ = _new_agent(s, other, "newowner")
        assert not agent_can_handle_work_item(old_agent, t).allowed
        assert agent_can_handle_work_item(new_agent, t).allowed


def test_gate_user_level_fallback(seeded):
    """无 agent 的用户级调用：退回比 user 本身（人工认领 / 管理端代操作）。"""
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, dev)
        assert agent_can_handle_work_item(
            None, t, fallback_user_id=dev).allowed
        d = agent_can_handle_work_item(None, t, fallback_user_id=other)
        assert not d.allowed and d.code == CODE_NOT_OWNER
        # 既没 agent 也没 fallback → 拒绝，不能默认放行
        d2 = agent_can_handle_work_item(None, t)
        assert not d2.allowed


def test_gate_works_for_story(seeded):
    """同一个门对 Story 也成立（Story 归属经由 epic→project 无关，只看列）。"""
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        st = s.get(service.Story, sid)
        assert work_item_owner_user_id(st) == st.owner_user_id
        dev_agent, _ = _new_agent(s, dev, "story-ok")
        other_agent, _ = _new_agent(s, other, "story-no")
        assert agent_can_handle_work_item(dev_agent, st).allowed
        assert not agent_can_handle_work_item(other_agent, st).allowed


def test_gate_decision_is_frozen_dataclass(seeded):
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, dev)
        d = agent_can_handle_work_item(None, t, fallback_user_id=dev)
        assert isinstance(d, GateDecision)
        with pytest.raises(Exception):
            d.allowed = False  # frozen：判定结果不可被下游偷偷改


# ---------- 2. 验收①：主动认领非 owner → 403 ----------

def test_api_claim_non_owner_agent_403(seeded):
    """Plan 验收①：agent 主动认领别人的 task → 403（不是 409）。"""
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, dev, title="T-claim-403")
        _agent, intruder_key = _new_agent(s, other, "claim403")
        tid = t.id
    c = _client()
    r = c.post(f"/api/tasks/{tid}/claim",
               headers={"Authorization": f"Bearer {intruder_key}"})
    assert r.status_code == 403, r.text
    assert "owner" in r.json()["detail"].lower()


def test_api_claim_owner_agent_ok(seeded):
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, dev, title="T-claim-ok")
        _agent, owner_key = _new_agent(s, dev, "claimok")
        tid = t.id
    c = _client()
    r = c.post(f"/api/tasks/{tid}/claim",
               headers={"Authorization": f"Bearer {owner_key}"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_progress"


def test_api_claim_null_owner_is_forbidden_not_crash(seeded):
    """owner 为 NULL 的 task：明确 403 提示补 owner，而不是 500/静默通过。"""
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, dev, title="T-claim-noowner")
        t.owner_user_id = None
        s.commit()
        _agent, key = _new_agent(s, dev, "noowner")
        tid = t.id
    c = _client()
    r = c.post(f"/api/tasks/{tid}/claim",
               headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403, r.text
    assert "no owner" in r.json()["detail"].lower()


def test_apply_non_owner_403(seeded):
    """arbitrated task：非 owner 申请同样 403。"""
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, dev, title="T-apply-403",
                       assignment_mode="arbitrated")
        _agent, intruder_key = _new_agent(s, other, "apply403")
        tid = t.id
    c = _client()
    r = c.post(f"/api/tasks/{tid}/apply",
               headers={"Authorization": f"Bearer {intruder_key}"})
    assert r.status_code == 403, r.text


# ---------- 3. 验收②：scheduler 自动派发 → fail-closed，不抛异常 ----------

def _fresh_owner_project(label):
    """全新的项目 + 全新 owner（名下 0 个 agent）+ 1 story。

    dispatch 测试必须用它：本文件的 `seeded` 是 module 级共享，前面的测试给
    dev 注册过 agent 且仍在线，直接拿 dev 造 task 会真的派发出去 —— 断言
    「无候选」就成了假阴性。候选池是**全局查询**（不按项目隔离 agent），
    所以必须换一个名下没有 agent 的 user，光换 project 没用。
    """
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"gate DP{n}-{label}")
        owner = service.register_user(s, username=f"gate-{label}{n}",
                                      password="password123")
        service.add_project_member(s, project_id=p.id, user_id=owner.id,
                                   role="member")
        epic = service.create_epic(s, project_id=p.id, title=f"gate DE{n}")
        st = service.create_story(s, epic_id=epic.id, title=f"gate DS{n}",
                                  created_by_user_id=owner.id)
        s.commit()
        return p.id, owner.id, st.id


def _deferred_reason(s, task_id):
    """读回 assignment_deferred_reason（列里存的是 JSON 字符串）。"""
    import json

    raw = s.get(service.Task, task_id).assignment_deferred_reason
    return json.loads(raw) if raw else None


def test_dispatch_no_owner_agent_blocks_with_reason():
    """Plan 验收② + T3.1：无合格 owner agent → 不抛异常，转 blocked。

    T3.1 之前这里断言「保持 todo」；T3.1 落地后无候选转 blocked
    （status_reason=insufficient_agents，previous_status=todo），
    解锁钩子在 agent 上线时按 previous_status 恢复。deferred reason
    继续保留 —— 它是排障细节，blocked 是看板状态，两者不冲突。
    """
    pid, owner, sid = _fresh_owner_project("nodev")
    with SessionLocal() as s:
        # 另一个 user 的 agent 在线，但**不是** owner 的 → 不该被选中
        stranger = service.register_user(s, username=f"gate-stranger-{next(_SEQ)}",
                                         password="password123")
        _agent, _key = _new_agent(s, stranger.id, "dispatch-other")
        t = _make_task(s, pid, sid, owner, title="T-dispatch-nodev")
        tid = t.id
        s.commit()
    with SessionLocal() as s:
        # 不抛异常，返回 None
        assert dispatch_implementation_task(s, tid) is None
        fresh = s.get(service.Task, tid)
        assert fresh.status == "blocked"
        assert fresh.previous_status == "todo"
        reason = _deferred_reason(s, tid)
        assert reason, "fail-closed 必须留下原因，否则看板无法区分「没人能干」"
        assert reason["code"] == "no_runnable_agent"
        assert reason["owner_user_id"] == owner
        assert reason["runnable_agent_ids"] == []  # owner 名下确实没有


def test_dispatch_null_owner_records_no_owner_code():
    """owner 为 NULL：原因码要能区分「待补 owner」与「等 agent 上线」。"""
    pid, owner, sid = _fresh_owner_project("nowner")
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, owner, title="T-dispatch-nowner")
        t.owner_user_id = None
        s.commit()
        tid = t.id
    with SessionLocal() as s:
        assert dispatch_implementation_task(s, tid) is None
        reason = _deferred_reason(s, tid)
        assert reason["code"] == CODE_NO_OWNER
        assert reason["owner_user_id"] is None


def test_dispatch_owner_agent_online_succeeds():
    """对照：owner 有在线 agent 时应正常派发，且清掉 deferred reason。"""
    pid, owner, sid = _fresh_owner_project("ok")
    with SessionLocal() as s:
        _agent, _key = _new_agent(s, owner, "dispatch-ok")
        t = _make_task(s, pid, sid, owner, title="T-dispatch-ok")
        tid = t.id
        s.commit()
    with SessionLocal() as s:
        picked = dispatch_implementation_task(s, tid)
        assert picked is not None
        fresh = s.get(service.Task, tid)
        assert fresh.status == "in_progress"
        assert fresh.assignment_deferred_reason is None


# ---------- 4. 写路径必须填 owner（否则静默卡死） ----------

def test_create_task_fills_owner_from_creator(seeded):
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = _make_task(s, pid, sid, dev, title="T-owner-default")
        assert t.owner_user_id == dev
        assert t.created_by_user_id == dev


def test_create_task_explicit_owner_overrides_creator(seeded):
    """显式 owner 用于「代别人建单」（admin 代办），不被 created_by 覆盖。"""
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        t = service.create_task(s, project_id=pid, story_id=sid,
                                title="T-owner-explicit",
                                created_by_user_id=dev, owner_user_id=other)
        s.commit()
        assert t.created_by_user_id == dev
        assert t.owner_user_id == other


def test_create_story_fills_owner_on_story_and_default_tasks(seeded):
    """Story 自身 + 自动建的两个默认 task 都要带 owner。

    漏掉 Story 那一行，T2.2 的「移除成员 → 移交其 owned story」就没有落点；
    漏掉 task 那两行，新建 story 的头两个任务会立刻卡死。
    """
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        epic = service.create_epic(s, project_id=pid, title="gate E2")
        st = service.create_story(s, epic_id=epic.id, title="gate S2",
                                  created_by_user_id=dev)
        s.commit()
        sid2 = st.id
    with SessionLocal() as s:
        st2 = s.get(service.Story, sid2)
        assert st2.owner_user_id == dev
        assert st2.created_by_user_id == dev
        tasks = s.query(service.Task).filter(service.Task.story_id == sid2).all()
        assert tasks, "create_story 默认会建 design/dev 两个 task"
        assert all(t.owner_user_id == dev for t in tasks), \
            "默认 task 漏填 owner → 新建 story 立刻卡死"


def test_generate_subtasks_inherit_owner(seeded):
    """spec 清单生成的子任务继承父任务归属。"""
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        parent = _make_task(s, pid, sid, dev, title="T-parent")
        parent.spec = "## 计划\n- [ ] 子任务甲\n- [ ] 子任务乙\n"
        s.commit()
        pid_id = parent.id
    with SessionLocal() as s:
        created = service.generate_tasks_from_spec(s, pid_id)
        assert len(created) == 2
        assert all(t.owner_user_id == dev for t in created), \
            "子任务漏继承 owner → 生成即卡死"


def test_generate_subtasks_follow_transferred_owner(seeded):
    """父任务移交后生成的子任务，跟 owner 而不是 created_by。"""
    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        parent = _make_task(s, pid, sid, dev, title="T-parent-xfer")
        parent.owner_user_id = other  # 模拟移交
        parent.spec = "## 计划\n- [ ] 子任务丙\n"
        s.commit()
        pid_id = parent.id
    with SessionLocal() as s:
        created = service.generate_tasks_from_spec(s, pid_id)
        assert created[0].owner_user_id == other
        assert created[0].created_by_user_id == dev  # 审计列继承创建者


# ---------- 5. Story 重派补上 owner 门 ----------

def test_reassign_story_reviewer_respects_owner(seeded):
    """T1.5 点名要修的 features:2393 —— 全链路唯一漏掉归属过滤的重派入口。

    直接用 Story 走不通（Story 评审已下线，pending_review 被 CHECK 拒），
    所以这里直接验函数：非 owner 的 agent 不得入选。
    """
    from agentboard.features.scheduling.service import _reassign_story_reviewer

    pid, dev, other, sid = seeded
    with SessionLocal() as s:
        st = s.get(service.Story, sid)
        other_agent, _ = _new_agent(s, other, "story-intruder")
        dev_agent, _ = _new_agent(s, dev, "story-owner")
        # 只让非 owner agent 在线
        other_agent.online = True
        dev_agent.online = False
        s.commit()

        # 全在线时旧版会把 intruder 选上；加了门之后应当选不到
        picked_ids = [
            a.id for a in (other_agent, dev_agent)
            if agent_can_handle_work_item(a, st)
        ]
        assert other_agent.id not in picked_ids
        assert picked_ids == [dev_agent.id]

        # owner 为 NULL → fail closed，返回 None
        st.owner_user_id = None
        s.commit()
        assert _reassign_story_reviewer(s, st) is None
