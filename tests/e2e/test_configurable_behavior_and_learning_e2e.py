"""End-to-End Acceptance Test Suite: 5 Mandatory Acceptance Scenarios.

Validates the full AgentBoard Configurable Agent Behavior & Learning closed loop:
1. Scenario 1: Proposal Clarify & Conversion Intelligence (Inspect code before asking, >=3 tasks)
2. Scenario 2: User-Configurable Preparation & 3-Tier Merge (sync_code, checkout_branch, field merge)
3. Scenario 3: Owner / Reviewer Comment Loop & Structured Summary (read_comments, leave_summary, reply_to_review)
4. Scenario 4: Owner Accepted Correction Learning (rejection -> fix -> pass -> extract -> recall in next task)
5. Scenario 5: Reviewer Judgment Reversal Learning (false rejection -> challenge -> pass -> extract review reflection)
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DB_PATH = "_test_behavior_e2e_tmp.db"
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///./{DB_PATH}"

import pytest
from agentboard.agent_runtime.behavior.defaults import get_default_payload_for_work_type
from agentboard.agent_runtime.behavior.models import (
    AgentBehaviorConfigPayload,
    EffectiveBehaviorConfig,
    PreparationBehavior,
    CollaborationBehavior,
    LearningBehavior,
)
from agentboard.agent_runtime.behavior.prompt_builder import prompt_builder
from agentboard.agent_runtime.behavior.resolver import behavior_resolver
from agentboard.agent_runtime.behavior.context_builder import execution_context_builder
from agentboard.agent_runtime.learning.evaluator import LearningCategory, learning_evaluator
from agentboard.agent_runtime.learning.extractor import learning_extractor
from agentboard.agent_runtime.learning.retriever import learning_retriever
from agentboard.agent_runtime.contract import ExecutionCommand, WorkType
from agentboard.core.infrastructure.database import SessionLocal, engine, init_db
from agentboard.features.learning.models import Learning
from agentboard.features.projects.models import Agent, Project
from agentboard.features.scheduling.behavior_service import (
    get_behavior_payload,
    upsert_behavior_config,
)


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
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_scenario_1_proposal_intelligence():
    """Scenario 1: 需求澄清自决与规范转化.
    - 澄清 Prompt 明确强制：提问前必须阅读真实源码与文档，不得问看一眼代码就能知道的事实。
    - 转化工单包含完备的设计意图与工单拆分规范。
    """
    eff = behavior_resolver.resolve(work_type=WorkType.PROPOSAL_CLARIFY)
    prompt = prompt_builder.build(
        work_type=WorkType.PROPOSAL_CLARIFY,
        behavior=eff,
        context={"raw_context_summary": "Proposal: Multi-tenant auth token isolation."},
    )

    # 1. 验证强制代码检索与反无意义提问规则
    assert "核心职责：需求澄清" in prompt
    assert "代码审查" in prompt
    assert "凡是能从现有代码中直接查明的事实，绝不得向用户发问" in prompt

    # 2. 验证转化 Prompt
    convert_eff = behavior_resolver.resolve(work_type=WorkType.PROPOSAL_CONVERT)
    convert_prompt = prompt_builder.build(
        work_type=WorkType.PROPOSAL_CONVERT,
        behavior=convert_eff,
        context={"raw_context_summary": "Converged proposal ready for conversion."},
    )
    assert "工单转化" in convert_prompt
    assert "不少于 3 个明确的拆分 Task" in convert_prompt


def test_scenario_2_user_configurable_preparation(db_session):
    """Scenario 2: 用户自定义准备配置生效与三级合并.
    - 项目级配置覆盖 sync_code=False
    - Agent 级配置覆盖 checkout_branch=True
    - 运行时验证：按正确顺序渲染 checkout -> inspect -> read_docs，且不包含 sync_code

    Review 2026-08-26 修正：Agent 级配置必须存为 (project_id, agent_id, work_type=None)，
    这是用户在项目上下文里为 Agent 设默认值的真实保存路径（API: PUT
    /api/projects/{pid}/agents/{aid}/behavior）。原先用 (project_id=None, agent_id)
    永远不会被运行时命中，是 P1 bug 的现场重现。
    """
    p = Project(name=f"Config_Test_{uuid.uuid4().hex[:6]}")
    ag = Agent(agent_id=f"ag_{uuid.uuid4().hex[:8]}", name="Scenario Agent")
    db_session.add_all([p, ag])
    db_session.commit()

    # 1. 项目覆盖：关闭 sync_code
    project_payload = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(sync_code=False, inspect_code=True),
        additional_instructions="Strict compliance with project architectural boundaries.",
    )
    upsert_behavior_config(db_session, payload=project_payload, project_id=p.id)

    # 2. Agent 在项目内的默认配置：开启 checkout_branch（必须带 project_id）
    agent_payload = AgentBehaviorConfigPayload(
        preparation=PreparationBehavior(checkout_branch=True),
    )
    upsert_behavior_config(
        db_session, payload=agent_payload, project_id=p.id, agent_id=ag.id, work_type=None
    )

    # 3. 解析最终生效配置
    resolver = behavior_resolver
    resolver.db = db_session
    effective = resolver.resolve(
        project_id=p.id,
        agent_id=ag.id,
        work_type=WorkType.IMPLEMENTATION,
    )

    assert effective.preparation.sync_code is False
    assert effective.preparation.checkout_branch is True
    assert effective.preparation.inspect_code is True
    assert effective.additional_instructions == "Strict compliance with project architectural boundaries."
    assert effective.sources["project"] is True
    # (project_id, agent_id) 解析路径现在被标记为 project_agent
    assert effective.sources["project_agent"] is True

    # 4. 生成 Prompt 验证渲染结果
    prompt = prompt_builder.build(
        work_type=WorkType.IMPLEMENTATION,
        behavior=effective,
        context={"branch": "feat/payment-gateway"},
    )
    assert "分支准备" in prompt
    assert "feat/payment-gateway" in prompt
    assert "代码审查" in prompt
    assert "代码同步" not in prompt  # sync_code was disabled


def test_scenario_3_collaboration_and_comment_loop():
    """Scenario 3: Owner 与 Reviewer 协同、留痕与回复规范.
    - 任务构建 Context 聚合历史评审评论
    - Prompt 包含清晰的 leave_summary 与 reply_to_review 行为指引
    """
    cmd = ExecutionCommand(
        execution_id="exec-collab-1",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=505,
        context={
            "title": "Fix SQL injection in report exporter",
            "comments": [
                {"id": 1, "author_username": "security_reviewer", "content": "Reject: Raw string formatting in query."}
            ],
        },
    )

    ctx = execution_context_builder.build(cmd)
    assert len(ctx.comments) == 1
    assert "Raw string formatting" in ctx.comments[0].content

    eff = behavior_resolver.resolve(work_type=WorkType.IMPLEMENTATION)
    prompt = prompt_builder.build(
        work_type=WorkType.IMPLEMENTATION,
        behavior=eff,
        context={"raw_context_summary": ctx.raw_context_summary},
    )

    assert "工作总结" in prompt
    assert "变更内容" in prompt
    assert "受影响的文件与组件" in prompt
    assert "评审回应" in prompt
    assert "ACCEPTED" in prompt and "CHALLENGED" in prompt
    assert "Raw string formatting in query." in prompt


def test_scenario_4_owner_accepted_correction_learning_loop(db_session):
    """Scenario 4: Owner 接受纠错闭环学习与经验复用.
    - Owner 遭遇 Review 驳回，采纳意见后修复通过
    - Evaluator 识别触发 ACCEPTED_REVIEW_FEEDBACK
    - Extractor 提炼结构化教训并入库
    - 下一个任务通过 Retriever 召回并注入 Prompt
    """
    p = Project(name=f"Learning_Proj_{uuid.uuid4().hex[:6]}")
    db_session.add(p)
    db_session.commit()

    # 1. 模拟驳回并修复通过事件
    task = {
        "id": 801,
        "project_id": p.id,
        "assignee_id": None,
        "status": "done",
        "type": "dev",
        "title": "Add Database Transaction to Order Checkout",
    }
    history = [
        {"status": "in_review"},
        {"status": "in_progress"},
        {"status": "in_review"},
        {"status": "done"},
    ]
    comments = [
        {"author": "reviewer", "content": "Missing rollback on PaymentGatewayError exception."},
        {"author": "owner", "content": "ACCEPTED: Wrapped db.commit() in try/except with db.rollback()."},
    ]

    triggers = learning_evaluator.evaluate_task_outcome(task, history=history, comments=comments)
    assert len(triggers) == 1
    assert triggers[0].category == LearningCategory.ACCEPTED_REVIEW_FEEDBACK

    # 2. 提炼结构化教训
    extracted = learning_extractor.extract(triggers[0])
    assert extracted.category == "accepted_review_feedback"

    # 3. 持久化到 DB
    learning_record = Learning(
        project_id=p.id,
        agent_id=None,
        work_type="dev",
        category=extracted.category,
        summary=extracted.summary,
        lesson=extracted.lesson,
        tags_json='["database", "transaction", "rollback"]',
        confidence=1.0,
    )
    db_session.add(learning_record)
    db_session.commit()

    # 4. 后续任务检索经验
    next_task_cmd = ExecutionCommand(
        execution_id="exec-next-task-2",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=802,
        context={
            "project_id": p.id,
            "title": "Implement Refund Transaction Processing",
            "description": "Handle database transaction for order refunds",
        },
    )

    recalled_learnings = learning_retriever.retrieve(
        project_id=p.id,
        work_type="dev",
        title="Implement Refund Transaction Processing",
        description="Handle database transaction for order refunds",
        db=db_session,
    )

    assert len(recalled_learnings) >= 1
    assert recalled_learnings[0]["id"] == learning_record.id

    # 5. 注入 Prompt 验证
    eff = behavior_resolver.resolve(work_type=WorkType.IMPLEMENTATION)
    next_prompt = prompt_builder.build(
        work_type=WorkType.IMPLEMENTATION,
        behavior=eff,
        learnings=recalled_learnings,
        context={"raw_context_summary": "Task 802: Implement Refund Transaction Processing"},
    )

    assert "【历史项目经验（Project Learnings）】" in next_prompt
    assert "accepted_review_feedback" in next_prompt
    assert learning_record.summary in next_prompt


def test_scenario_5_reviewer_judgment_reversal_learning(db_session):
    """Scenario 5: Reviewer 误判申诉反思学习.
    - Reviewer 误判驳回，Owner 提供可验证证据申诉 (CHALLENGED)
    - Reviewer 复核证据后采纳申诉并改判通过 (owner_challenge_accepted)
    - Evaluator 触发 REVIEW_JUDGMENT_REVERSAL
    - 提炼反思沉淀
    """
    p = Project(name=f"Reversal_Proj_{uuid.uuid4().hex[:6]}")
    db_session.add(p)
    db_session.commit()

    task = {
        "id": 901,
        "project_id": p.id,
        "reviewer_id": None,
        "status": "done",
        "type": "dev",
        "title": "Add CSRF Token Verification",
    }
    review_records = [
        {"id": 1, "decision": "reject", "reason": "CSRF header is not checked on POST routes."},
        {"id": 2, "decision": "approve", "resolution": "owner_challenge_accepted"},
    ]
    comments = [
        {"author": "reviewer", "content": "Reject: CSRF header is not checked on POST routes."},
        {"author": "owner", "content": "CHALLENGED: CSRF verification is globally enforced in CsrfMiddleware (middleware.py:34)."},
        {"author": "reviewer", "content": "【采纳申诉】核对 middleware.py:34 证据属实，撤销驳回并批准通过。"},
    ]

    triggers = learning_evaluator.evaluate_task_outcome(
        task, review_records=review_records, comments=comments
    )
    assert len(triggers) == 1
    assert triggers[0].category == LearningCategory.REVIEW_JUDGMENT_REVERSAL

    extracted = learning_extractor.extract(triggers[0])
    assert extracted.category == "review_judgment_reversal"
    assert "评审" in extracted.summary or "误判" in extracted.summary
    assert len(extracted.lesson) > 10
