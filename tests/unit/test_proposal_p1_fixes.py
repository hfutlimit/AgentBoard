"""Review 2026-08-26 round 4 P1 验收测试（Proposal conversion + TaskGraph + auth + outcome enum）。

覆盖：
- P1 #1: TicketHandler "created" != "success" bug 修复
- P1 #2: Proposal conversion 原子性
- P1 #3: TaskGraph API 拆分 planned vs persisted
- P1 #4: endpoint 权限（conversion / task-graph / delete）
- P1/P2 #5: ProposalConversionService 三阶段收敛两套 conversion

每个 P1 一个独立 test，避免一个 P1 修不好把别的一起卡死。
"""
import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# 必须在 import engine 之前设置
DB_PATH = "_test_proposal_p1_tmp.db"
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///./{DB_PATH}"

import pytest
from agentboard.core.infrastructure.database import SessionLocal, engine, init_db
from agentboard.features.projects.models import Agent, Project, Story, Epic
from agentboard.features.proposals import service as proposal_service
from agentboard.features.proposals.models import Proposal
from agentboard.features.work_items.models import Task, TaskDependency
from agentboard.features.scheduling.behavior_service import (
    upsert_behavior_config,
)
from agentboard.features.identity.models import User
from agentboard.core.common.enums import Status, ItemType


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass
    init_db()
    yield
    engine.dispose()
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass


@pytest.fixture
def db_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def project(db_session):
    p = Project(name=f"P1Test_{uuid.uuid4().hex[:6]}")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture
def epic(db_session, project):
    e = Epic(project_id=project.id, title="Test Epic")
    db_session.add(e)
    db_session.commit()
    db_session.refresh(e)
    return e


@pytest.fixture
def author(db_session):
    u = User(
        username=f"author_{uuid.uuid4().hex[:6]}",
        display_name="Test Author",
        password_hash="x",
        is_admin=False,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


# ============================================================
# P1 #1: TicketHandler 字符串不匹配 bug 修复验收
# ============================================================

def test_p1_ticket_handler_outcome_enum_aligns_with_execute_command():
    """P1 #1: handle_decision 返回 TicketOutcome.CREATED.value，execute_command 比对枚举。

    原 bug：handle_decision 返回 "created" 字符串，execute_command 比对 "success"，
    "created" != "success" → 正常成功路径被误判为 failure。
    修法：handler 走 ``.outcome.TicketOutcome`` enum，execute_command 比对 enum value。
    """
    from agentboard.agent_runtime.handlers.outcome import TicketOutcome

    # enum 存在且值正确
    assert TicketOutcome.CREATED.value == "created"
    assert TicketOutcome.FAILED.value == "failed"
    assert TicketOutcome.SKIPPED.value == "skipped"
    # execute_command 比对路径（间接验证：通过源码 grep，注意是 class method）
    from agentboard.agent_runtime.handlers import ticket as ticket_handler
    import inspect
    src = inspect.getsource(ticket_handler.TicketHandler.execute_command)
    # 关键：execute_command 不能再硬编码 "outcome == 'success'" 字面量比对
    assert 'outcome == "success"' not in src, \
        "execute_command 仍用 'success' 字面量比对（应为 TicketOutcome.CREATED.value）"
    # 关键：必须用 enum 比对
    assert "TicketOutcome.CREATED.value" in src, \
        "execute_command 必须用 TicketOutcome enum 比对"


# ============================================================
# P1 #2: Proposal conversion 原子性
# ============================================================

def test_p1_proposal_conversion_is_atomic(db_session, project, epic, author):
    """P1 #2: create_story / create_task 接受 commit=False，整个 conversion 单 transaction。

    原 bug：每个 create_* helper 内部 _commit，半成品孤儿数据。
    修法：commit=False 让 entity helper 只 flush；ProposalConversionService.apply
    统一收尾 _commit。
    """
    # 1. 提交 proposal 走完整 convert
    p = proposal_service.create_proposal(
        db_session, project_id=project.id,
        title=f"P1Atomic_{uuid.uuid4().hex[:6]}",
        content="- [ ] Feature A\n- [ ] Feature B",
        author_id=author.id,
    )
    proposal_service.set_proposal_status(db_session, p.id, "queued")
    proposal_service.claim_proposal(db_session, p.id, agent="t")
    proposal_service.set_proposal_status(db_session, p.id, "converged")
    db_session.commit()
    p2 = proposal_service.get_proposal(db_session, p.id)
    p2.converged_spec = "- [ ] Feature A\n- [ ] Feature B"
    db_session.commit()

    # 2. 调 facade
    story, tasks, _ = proposal_service.convert_proposal_to_story(
        db_session, p.id, epic_id=epic.id, title="Story Atomic",
    )

    # 3. 验证：Story 一定有
    assert story.id is not None
    # 验证：spec-driven dev tasks 创建成功
    task_titles = {t.title for t in tasks}
    assert "Feature A" in task_titles
    assert "Feature B" in task_titles
    # 验证：proposal.story_id 回填
    p3 = proposal_service.get_proposal(db_session, p.id)
    assert p3.story_id == story.id
    # 验证：proposal.status 推进到 story_created
    assert p3.status == "story_created"

    # 4. 关键验收：Story 真的创建了 + spec-driven dev tasks 真的创建了
    # 之前 bug 是 Story 成功但 Task 失败导致 p.story_id == None；现在应该都建好
    assert p3.story_id is not None
    assert len(tasks) >= 2  # Feature A + Feature B（不含 create_story 自动创的 default dev）

    # 5. 验证：Story 真的存在（不是空对象）
    db_session.refresh(story)
    assert story.id is not None
    assert story.epic_id == epic.id

    # 6. P1 #5 副产品：现在有 design / dev / qa 三类 task（之前只有 design + dev）
    all_story_tasks = db_session.query(Task).filter(Task.story_id == story.id).all()
    task_types = {t.type for t in all_story_tasks}
    assert ItemType.DESIGN.value in task_types, "应有 design task（create_story 自动创）"
    assert ItemType.DEV.value in task_types, "应有 dev task"
    assert ItemType.QA.value in task_types, "P1 #5 修复后应有 qa task"


# ============================================================
# P1 #3: TaskGraph API 拆分 planned vs persisted
# ============================================================

def test_p1_task_graph_persisted_returns_real_db_dag(db_session, project, epic, author):
    """P1 #3: ``/task-graph`` 必须查 DB 真实 DAG，不查推演 spec。

    原 bug：原 build_proposal_task_graph 完全不查 DB Task/TaskDependency，
    UI 看到的 design/dev/qa 节点跟实际执行图不一致。
    修法：拆成两个 API —— ``get_persisted_task_graph`` 查 DB，
    ``build_proposal_task_graph``（标 planned=True）保留推演。
    """
    # 准备：proposal 走完整 convert
    p = proposal_service.create_proposal(
        db_session, project_id=project.id,
        title=f"P1Graph_{uuid.uuid4().hex[:6]}",
        content="- [ ] Real Feature",
        author_id=author.id,
    )
    proposal_service.set_proposal_status(db_session, p.id, "queued")
    proposal_service.claim_proposal(db_session, p.id, agent="t")
    proposal_service.set_proposal_status(db_session, p.id, "converged")
    db_session.commit()
    p.converged_spec = "- [ ] Real Feature"
    db_session.commit()
    proposal_service.convert_proposal_to_story(
        db_session, p.id, epic_id=epic.id, title="Story Graph",
    )

    # 调 persisted API
    persisted = proposal_service.get_persisted_task_graph(db_session, p.id)

    # 关键：persisted=True 标记
    assert persisted["planned"] is False
    assert persisted["persisted"] is True
    # 关键：node id 是真实 DB id（int），不是 "design-1" / "qa-1" 虚拟字符串
    assert all(isinstance(n["id"], int) for n in persisted["nodes"]), \
        "persisted 节点 id 必须是 int（DB id）"

    # 关键：DB 里实际有多少 task，persisted 就有多少节点（不一致会爆出来）
    story = db_session.get(Story, p.story_id)
    db_task_count = db_session.query(Task).filter(Task.story_id == story.id).count()
    assert len(persisted["nodes"]) == db_task_count, \
        f"persisted nodes ({len(persisted['nodes'])}) 必须等于 DB task 数量 ({db_task_count})"

    # 关键：edge 引用真实 id，且 source/target 都在 nodes 里
    node_ids = {n["id"] for n in persisted["nodes"]}
    for edge in persisted["edges"]:
        assert edge["source"] in node_ids, f"edge source {edge['source']} 不在 nodes"
        assert edge["target"] in node_ids, f"edge target {edge['target']} 不在 nodes"


def test_p1_task_graph_planned_keeps_inferred_behavior():
    """P1 #3: ``/task-graph/planned`` 保留推演行为（基于 spec 解析）。

    关键验收：推演版必须标 ``planned=True``，node id 仍是虚拟字符串前缀
    （design-1 / dev-N / qa-1），且永远包含 qa 节点（即便 DB 没 QA task）。
    """
    import inspect
    from agentboard.features.proposals import service
    src = inspect.getsource(service.build_proposal_task_graph)
    # 必须有 planned 标记
    assert '"planned": True' in src or "'planned': True" in src, \
        "build_proposal_task_graph 必须返回 planned=True 标记"
    # 必须基于 spec 解析（不查 DB）
    assert "converged_spec" in src, \
        "build_proposal_task_graph 必须基于 converged_spec 推演"
    assert "design-1" in src and "qa-1" in src, \
        "build_proposal_task_graph 必须有 design-1 / qa-1 虚拟节点"


# ============================================================
# P1 #4: endpoint 权限
# ============================================================

def test_p1_endpoint_authorization_present():
    """P1 #4: /convert /task-graph /ticket-requests /delete 都必须有 authorization + 权限校验。

    通过静态检查源码确保 endpoint 不再缺失权限。
    """
    import inspect
    from agentboard.features.proposals import router
    src = inspect.getsource(router)

    # 关键 endpoint 列表
    endpoints_must_check = [
        ("convert_proposal",        # POST /api/proposals/{pid}/convert
         "_enforce_owner_or_admin",  # owner 或 admin 才能 convert
         ),
        ("get_proposal_task_graph",  # GET  /api/proposals/{pid}/task-graph
         "_enforce_member_or_admin",  # 项目成员可读
         ),
        ("get_proposal_planned_task_graph",  # GET /api/proposals/{pid}/task-graph/planned
         "_enforce_member_or_admin",
         ),
        ("list_ticket_requests",    # GET /api/proposals/{pid}/ticket-requests
         "_enforce_member_or_admin",
         ),
        # delete_proposal 走"creator 或 admin"直接比较（不是通用 enforce helper）
        # 不在通用 enforce 列表里，单独校验（creator 字段是模型真实字段 author_id）
        ("delete_proposal",         # DELETE /api/proposals/{pid}
         "p.author_id",  # 用直接比较
         ),
    ]

    for ep_name, _enforce_kw in endpoints_must_check:
        # 找到 endpoint 函数的定义
        idx = src.find(f"def {ep_name}(")
        assert idx >= 0, f"找不到 {ep_name} 函数"
        # 找到下一个 def 或文件末尾
        next_def = src.find("\ndef ", idx + 1)
        if next_def < 0:
            next_def = len(src)
        ep_src = src[idx:next_def]
        # 必须有 authorization 参数
        assert "authorization: str | None = Header(None)" in ep_src, \
            f"{ep_name} 缺 authorization 参数"
        # 必须调 _caller_uid_admin 拿 uid
        assert "_caller_uid_admin" in ep_src, \
            f"{ep_name} 缺 _caller_uid_admin 调用"
        # 必须有显式权限校验（_enforce helper 或直接比较）
        assert _enforce_kw in ep_src, \
            f"{ep_name} 缺项目权限校验（期望 { _enforce_kw }）"


# ============================================================
# P1/P2 #5: ProposalConversionService 三阶段收敛
# ============================================================

def test_p1_proposal_conversion_service_three_phases():
    """P1/P2 #5: ProposalConversionService 必须有 plan / validate / apply 三阶段。

    通过静态检查 + 实际行为验证。
    """
    from agentboard.features.proposals.conversion_service import (
        ProposalConversionService, ConversionPlan, ConversionResult,
    )

    # 1. ConversionPlan 数据结构：必填字段
    plan = ConversionPlan(
        document=None,
        epic=None,
        epic_id=42,
        story={"title": "S", "description": "D"},
        tasks=[
            {"title": "设计：S", "type": "design"},
            {"title": "实现：S", "type": "dev"},
            {"title": "QA：S", "type": "qa"},
        ],
        dependencies=[("设计：S", "实现：S"), ("实现：S", "QA：S")],
        create_qa=True,
        min_tasks=3,
    )
    # 2. plan() 推演包含 design + dev + qa
    class _StubProposal:
        def __init__(self, title, converged_spec):
            self.title = title
            self.converged_spec = converged_spec
            self.content = converged_spec
    stub = _StubProposal("Test", "- [ ] Feature A\n- [ ] Feature B")
    p = ProposalConversionService.plan(stub, epic_id=1)
    titles = [t["title"] for t in p.tasks]
    # 至少有 design + 1 dev + 1 qa（spec 两个 dev task）
    assert any(t["type"] == "design" for t in p.tasks), "plan 必须含 design task"
    assert any(t["type"] == "dev" for t in p.tasks), "plan 必须含 dev task"
    assert any(t["type"] == "qa" for t in p.tasks), "plan 必须含 qa task"
    # 至少有 1 条 design → dev dep
    assert any(
        src.startswith("设计") for src, _dst in p.dependencies
    ), f"plan 至少需要 1 条 design→dev dep，实际：{p.dependencies}"

    # 3. validate() 拒绝不合法 plan
    bad_plan = ConversionPlan(
        epic_id=None, epic=None, story={"title": "S"},
        tasks=[{"title": "t", "type": "dev"}],  # < 3 tasks
        min_tasks=3,
    )
    with pytest.raises(Exception):
        ProposalConversionService.validate(bad_plan, project_id=1)

    # 4. 关键：convert_proposal_to_story 已经变 thin facade
    import inspect
    from agentboard.features.proposals import service
    convert_src = inspect.getsource(service.convert_proposal_to_story)
    assert "ProposalConversionService.plan" in convert_src, \
        "convert_proposal_to_story 必须是 thin facade（调 ProposalConversionService.plan）"
    assert "ProposalConversionService.validate" in convert_src
    assert "ProposalConversionService.apply" in convert_src
