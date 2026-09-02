"""Implementation Plan M0（T0.1a/b/c）· 评审真源统一回归测试。

背景
----
``core/application/service.py`` 与 ``features/scheduling/service.py`` 曾各自
维护一整簇评审函数。可达性分析 + 运行时验证确认：公开入口
（``review_task`` / ``assign_task_reviewer`` / ``scan_review_timeouts`` /
``assign_reviewer`` / ``submit_task_for_review``）全部已 re-export 自
features，core 侧那一簇是**死代码**，features 版才是唯一真源。

M0 的收敛方向因此是 **core 删除本地死定义、末尾统一转发到 features**
（与 Plan v2 T0.1a 原始描述的「features 转发到 core」方向相反）。

本文件锁定三条契约：
1. core facade 暴露的评审符号全部解析到 features（不会出现两份分叉实现）；
2. ``_online_reviewer_candidates`` 合并版同时具备 心跳过期 / enabled /
   runnable instance / 项目成员 四道过滤；
3. ``roles`` **不作为** reviewer 资格判据（既有架构决策，见
   ``features/scheduling/service.py`` 中 "roles 不参与 workload 授权" 注释），
   且 Story 实体不再被 majority 结算接受。

运行：
    PYTHONPATH=. python -m pytest tests/test_m0_review_truth_source.py -q
"""
import itertools
import os
import sys
import tempfile
from datetime import timedelta

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)
os.environ.pop("AGENTBOARD_REVIEW_MODE", None)
os.environ.pop("AGENTBOARD_REVIEW_QUORUM", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import service  # noqa: E402
from agentboard.core.application import service as core_facade  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402
from agentboard.features.scheduling import service as feat_service  # noqa: E402

init_db()

_SEQ = itertools.count(1)

# core facade 上必须统一转发到 features 的评审符号
_REVIEW_SYMBOLS = [
    "review_task", "assign_task_reviewer", "scan_review_timeouts", "assign_reviewer",
    "get_review_mode", "get_review_quorum",
    "_vote_majority", "_settle_majority_approved", "_settle_majority_rejected",
    "_is_reviewer_candidate", "_upsert_review_vote", "_review_vote_counts",
    "_clear_review_votes", "_online_reviewer_candidates",
    "_reassign_story_reviewer", "_reassign_task_reviewer",
]


def _seed_agent(n, *, agent_enabled=True, with_instance=True,
                worker_status="active", as_member=True, roles="[]",
                heartbeat_stale=False):
    """1 项目 + 1 成员 user + 1 agent（+ worker + instance），返回各 id。"""
    n = f"{next(_SEQ)}-{n}"
    with SessionLocal() as s:
        p = service.create_project(s, name=f"m0-p{n}")
        u = service.register_user(s, username=f"m0-u{n}", password="password123")
        if as_member:
            service.add_project_member(s, project_id=p.id, user_id=u.id, role="member")

        aid = f"m0-a{n}"
        service.register_agent(s, agent_id=aid, name=f"A{n}", roles=roles, user_id=u.id)
        service.agent_heartbeat(s, aid, user_id=u.id)

        if not agent_enabled:
            from agentboard.features.projects.models import Agent
            a = s.query(Agent).filter(Agent.agent_id == aid).first()
            a.enabled = False

        if with_instance:
            wid = f"m0-w{n}"
            service.register_worker(s, worker_id=wid, hostname="test",
                                    status=worker_status)
            inst = service.upsert_agent_instance(
                s, worker_id=wid, agent_id=aid, executor_type="fake")
            service.instance_heartbeat(
                s, inst.id, caller_worker_id=wid, probe_ok=True)

        if heartbeat_stale:
            # 必须放在 instance_heartbeat 之后：否则会被随后的心跳刷新覆盖。
            # Agent 逻辑在线 = 直连心跳新鲜 **或** 至少一个 enabled instance
            # 心跳新鲜（expire_stale_agent_heartbeats 语义），两者都要置陈旧。
            from agentboard.features.projects.models import Agent, AgentInstance
            from agentboard.core.common.models import utc_now
            old = utc_now() - timedelta(hours=2)
            a = s.query(Agent).filter(Agent.agent_id == aid).first()
            a.last_heartbeat = old
            for inst in s.query(AgentInstance).filter(
                    AgentInstance.agent_id == aid).all():
                inst.last_heartbeat = old

        s.commit()
        return p.id, u.id, aid


def _candidates(project_id):
    with SessionLocal() as s:
        return [
            a.agent_id
            for a in feat_service._online_reviewer_candidates(s, project_id)
        ]


# ---------------------------------------------------------------------------
# 1. 真源统一
# ---------------------------------------------------------------------------

def test_review_symbols_resolve_to_features():
    """core facade 上的评审符号必须全部指向 features（不存在第二份实现）。"""
    for name in _REVIEW_SYMBOLS:
        fn = getattr(core_facade, name, None)
        assert fn is not None, f"core facade 缺少符号 {name}"
        mod = getattr(fn, "__module__", None)
        assert mod == "agentboard.features.scheduling.service", (
            f"{name} 解析到 {mod}，期望 features/scheduling/service（真源）"
        )


def test_no_duplicate_definitions_in_core():
    """core 模块内不得再保留这些函数的本地 def（只允许末尾转发 import）。"""
    import ast
    src = open(
        os.path.join(_ROOT, "src/backend-fastapi/agentboard/core/application/service.py"),
        encoding="utf-8",
    ).read()
    defined = {
        n.name for n in ast.parse(src).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    leftovers = sorted(set(_REVIEW_SYMBOLS) & defined)
    assert not leftovers, f"core 仍存在重复的本地定义：{leftovers}"


# ---------------------------------------------------------------------------
# 2. _online_reviewer_candidates 合并版过滤语义
# ---------------------------------------------------------------------------

def test_eligible_agent_is_selected():
    """基线：成员 + 在线 + enabled + runnable instance → 入选。"""
    pid, _uid, aid = _seed_agent("ok")
    assert _candidates(pid) == [aid]


def test_disabled_agent_is_filtered_out():
    """enabled=False 的 agent 必须被筛掉（core 旧版缺失此过滤）。"""
    pid, _uid, _aid = _seed_agent("disabled", agent_enabled=False)
    assert _candidates(pid) == []


def test_agent_without_runnable_instance_is_filtered_out():
    """没有 runnable instance（无 worker 挂载）的 agent 必须被筛掉。"""
    pid, _uid, _aid = _seed_agent("noinst", with_instance=False)
    assert _candidates(pid) == []


def test_inactive_worker_instance_is_filtered_out():
    """instance 挂在 inactive worker 上 → 无 runnable instance，筛掉。"""
    pid, _uid, _aid = _seed_agent("inactive", worker_status="inactive")
    assert _candidates(pid) == []


def test_non_member_agent_is_filtered_out():
    """非项目成员的 agent 必须被筛掉（归属门）。"""
    pid, _uid, _aid = _seed_agent("nonmember", as_member=False)
    assert _candidates(pid) == []


def test_stale_heartbeat_is_expired_and_filtered_out():
    """心跳超时 → 被 expire_stale_agent_heartbeats 降级后筛掉。"""
    pid, _uid, _aid = _seed_agent("stale", heartbeat_stale=True)
    assert _candidates(pid) == []


# ---------------------------------------------------------------------------
# 3. roles 不作为资格判据 + Story 结算已下线
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("roles", ['[]', '["developer"]', '["reviewer"]'])
def test_roles_is_not_a_reviewer_gate(roles):
    """roles 不参与 workload 准入（既有架构决策）。

    ``features/scheduling/service.py`` 明确注释「roles 只在旧数据迁移期用于
    CLI executor 兼容推导，不参与 workload 授权」。若在此处加回
    ``"reviewer" in roles`` 过滤，存量 agent（roles 多为 ``[]``）会被全量筛掉，
    评审彻底卡死。本测试锁定「roles 不是筛除条件」这一契约。
    """
    pid, _uid, aid = _seed_agent(f"roles{roles}", roles=roles)
    assert _candidates(pid) == [aid]


def test_settle_majority_rejects_story_entity():
    """Story 评审已下线，majority 结算只接受 task 实体。"""
    # 从 feat_service 自身的命名空间取异常类：本仓库多个测试文件会在 import 期
    # 清掉 sys.modules["agentboard*"] 再重导入，跨文件批量跑时
    # ``from agentboard.core.exceptions import InvalidValue`` 拿到的可能是
    # **另一个模块对象**里的同名类，pytest.raises 反而抓不到（顺序依赖失败）。
    InvalidValue = service.InvalidValue

    pid, _uid, _aid = _seed_agent("settle")
    with SessionLocal() as s:
        from agentboard.features.projects.models import Story, Epic
        epic = Epic(project_id=pid, title="m0 epic")
        s.add(epic)
        s.flush()
        # 注：Story 的合法状态集合已不含 pending_review（STORY_REVIEW_STATUSES
        # 恒空，DB CHECK 直接拒绝）——这正是 Story 评审已下线、majority 结算
        # story 分支为死路径的硬证据。此处用 in_review 仅为构造实体。
        st = Story(epic_id=epic.id, title="m0 story", status="in_review")
        s.add(st)
        s.commit()

        with pytest.raises(InvalidValue):
            feat_service._settle_majority_approved(s, st, "story")
        with pytest.raises(InvalidValue):
            feat_service._settle_majority_rejected(s, st, "story")
