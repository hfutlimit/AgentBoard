import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DB_PATH = "_test_ctx_builder_tmp.db"
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///./{DB_PATH}"

import pytest
from agentboard.core.infrastructure.database import SessionLocal, engine, init_db
from agentboard.features.projects.models import Agent, Project, Story, Epic
from agentboard.features.work_items.models import Task, Comment
from agentboard.features.documents.models import Document, DocumentType, DocumentStatus
from agentboard.features.learning.models import Learning
from agentboard.agent_runtime.behavior.context_builder import (
    ExecutionContextBuilder,
    execution_context_builder,
)
from agentboard.agent_runtime.behavior.models import (
    EffectiveBehaviorConfig,
    PreparationBehavior,
    CollaborationBehavior,
    LearningBehavior,
    DocumentSourceConfig,
)
from agentboard.agent_runtime.learning.retriever import learning_retriever
from agentboard.agent_runtime.contract import ExecutionCommand, WorkType


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


# -------------------------------------------------------------
# 向后兼容：纯 ctx 预填（无 db）场景
# -------------------------------------------------------------
def test_context_builder_assembles_full_context():
    cmd = ExecutionCommand(
        execution_id="exec-123",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=42,
        context={
            "project_id": 3,
            "title": "Implement Redis Cache",
            "description": "Add caching layer for user profiles",
            "spec": "Use redis-py client with TTL=300s",
            "comments": [
                {"id": 1, "author_username": "alice", "content": "Make sure fallback works on connection error."}
            ],
            "documents": [
                {"id": 10, "title": "Cache Architecture", "type": "design", "content": "Redis clustering setup..."}
            ],
            "learnings": [
                {"id": 5, "category": "accepted_review_feedback", "summary": "Handle timeout gracefully", "lesson": "Always configure socket_timeout."}
            ],
        },
    )

    # 不传 behavior → 默认全开（向后兼容 e2e / preview 场景）
    ctx = execution_context_builder.build(cmd)

    assert ctx.execution_id == "exec-123"
    assert ctx.work_type == WorkType.IMPLEMENTATION
    assert len(ctx.comments) == 1
    assert ctx.comments[0].author == "alice"
    assert len(ctx.documents) == 1
    assert ctx.documents[0].title == "Cache Architecture"
    assert len(ctx.learnings) == 1
    assert ctx.learnings[0].summary == "Handle timeout gracefully"
    assert "Implement Redis Cache" in ctx.raw_context_summary
    assert "Make sure fallback works" in ctx.raw_context_summary


# -------------------------------------------------------------
# P1 修复：ContextBuilder 真的从 DB 拉 documents / comments / learnings
# -------------------------------------------------------------
@pytest.fixture
def fixture_world(db_session):
    """建立完整业务世界：Project + Epic + Story + Task + Document + Comment + Learning。"""
    p = Project(name=f"Proj_{uuid.uuid4().hex[:6]}")
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)

    e = Epic(project_id=p.id, title="Cache Subsystem", description="Epic for caching work")
    db_session.add(e)
    db_session.commit()
    db_session.refresh(e)

    s = Story(epic_id=e.id, title="Implement Redis Cache Layer", description="Story description")
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)

    t = Task(
        project_id=p.id,
        story_id=s.id,
        title="Implement Redis Cache",
        description="Add caching layer for user profiles",
        spec="Use redis-py client with TTL=300s",
    )
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)

    # Story 关联的 design 文档
    d1 = Document(
        project_id=p.id,
        story_id=s.id,
        title="Cache Architecture",
        content="Redis clustering setup with sentinel failover",
        type=DocumentType.DESIGN.value,
        status=DocumentStatus.APPROVED.value,
    )
    # 项目级 knowledge 文档
    d2 = Document(
        project_id=p.id,
        title="Coding Conventions",
        content="All public APIs must have docstrings",
        type=DocumentType.KNOWLEDGE.value,
        status=DocumentStatus.APPROVED.value,
    )
    db_session.add_all([d1, d2])
    db_session.commit()
    db_session.refresh(d1)
    db_session.refresh(d2)

    # Task 关联的 3 条评论
    c1 = Comment(task_id=t.id, author="alice", content="Make sure fallback works on connection error.")
    c2 = Comment(task_id=t.id, author="bob", content="Use cluster mode?")
    c3 = Comment(task_id=t.id, author="carol", content="Don't forget TTL refresh")
    db_session.add_all([c1, c2, c3])
    db_session.commit()

    # 项目级 learnings
    learn = Learning(
        project_id=p.id,
        category="accepted_review_feedback",
        summary="Handle timeout gracefully",
        lesson="Always configure socket_timeout.",
        tags_json='["timeout", "redis"]',
    )
    db_session.add(learn)
    db_session.commit()
    db_session.refresh(learn)

    return {
        "project": p, "epic": e, "story": s, "task": t,
        "doc_linked": d1, "doc_project": d2,
        "learning": learn,
    }


def test_context_builder_loads_documents_from_db(db_session, fixture_world):
    """behavior.preparation.read_documents=True 时必须真从 DB 拉关联 + 项目级文档。"""
    cmd = ExecutionCommand(
        execution_id="exec-doc-1",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=fixture_world["task"].id,
        context={
            "project_id": fixture_world["project"].id,
            "title": fixture_world["task"].title,
            "description": fixture_world["task"].description,
        },
    )
    behavior = EffectiveBehaviorConfig(
        preparation=PreparationBehavior(
            read_documents=True, load_memory=False, inspect_code=True
        ),
        document_sources=[
            DocumentSourceConfig(type="linked_documents"),
            DocumentSourceConfig(type="project_documents"),
        ],
    )
    builder = ExecutionContextBuilder()  # 不需要 retriever
    ctx = builder.build(cmd, behavior=behavior, db=db_session)

    # 必须真从 DB 拉到 2 个 doc（linked + project）
    assert len(ctx.documents) == 2
    titles = {d.title for d in ctx.documents}
    assert "Cache Architecture" in titles
    assert "Coding Conventions" in titles
    assert ctx.sources_resolved["documents_from_db"] is True
    assert ctx.sources_resolved["documents_from_ctx"] is False


def test_context_builder_respects_read_documents_false(db_session, fixture_world):
    """behavior.preparation.read_documents=False 时绝不查 documents。"""
    cmd = ExecutionCommand(
        execution_id="exec-doc-off",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=fixture_world["task"].id,
        context={"title": "x", "description": "y"},
    )
    behavior = EffectiveBehaviorConfig(
        preparation=PreparationBehavior(
            read_documents=False, load_memory=False, inspect_code=True
        ),
    )
    builder = ExecutionContextBuilder()
    ctx = builder.build(cmd, behavior=behavior, db=db_session)

    assert len(ctx.documents) == 0
    assert ctx.sources_resolved["documents_from_db"] is False


def test_context_builder_empty_document_sources_clears(db_session, fixture_world):
    """document_sources=[]（显式清空）时即便 read_documents=True 也不查。"""
    cmd = ExecutionCommand(
        execution_id="exec-doc-clear",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=fixture_world["task"].id,
        context={"title": "x", "description": "y"},
    )
    behavior = EffectiveBehaviorConfig(
        preparation=PreparationBehavior(
            read_documents=True, load_memory=False, inspect_code=True
        ),
        document_sources=[],  # 显式清空
    )
    builder = ExecutionContextBuilder()
    ctx = builder.build(cmd, behavior=behavior, db=db_session)

    assert len(ctx.documents) == 0


def test_context_builder_loads_comments_from_db(db_session, fixture_world):
    """behavior.collaboration.read_comments=True 时从 DB 拉评论。"""
    cmd = ExecutionCommand(
        execution_id="exec-cmt-1",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=fixture_world["task"].id,
        context={"title": "x", "description": "y"},
    )
    behavior = EffectiveBehaviorConfig(
        preparation=PreparationBehavior(
            read_documents=False, load_memory=False, inspect_code=False
        ),
        collaboration=CollaborationBehavior(read_comments=True),
    )
    builder = ExecutionContextBuilder()
    ctx = builder.build(cmd, behavior=behavior, db=db_session)

    assert len(ctx.comments) == 3
    assert ctx.sources_resolved["comments_from_db"] is True


def test_context_builder_respects_read_comments_false(db_session, fixture_world):
    cmd = ExecutionCommand(
        execution_id="exec-cmt-off",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=fixture_world["task"].id,
        context={"title": "x", "description": "y"},
    )
    behavior = EffectiveBehaviorConfig(
        preparation=PreparationBehavior(
            read_documents=False, load_memory=False, inspect_code=False
        ),
        collaboration=CollaborationBehavior(read_comments=False),
    )
    builder = ExecutionContextBuilder()
    ctx = builder.build(cmd, behavior=behavior, db=db_session)

    assert len(ctx.comments) == 0
    assert ctx.sources_resolved["comments_from_db"] is False


def test_context_builder_loads_learnings_via_retriever(db_session, fixture_world):
    """behavior.preparation.load_memory=True 时通过 retriever 从 DB 拉 learnings。"""
    cmd = ExecutionCommand(
        execution_id="exec-lrn-1",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=fixture_world["task"].id,
        context={
            "project_id": fixture_world["project"].id,
            "title": "Implement Redis Cache",
            "description": "Add caching layer for user profiles",
        },
    )
    behavior = EffectiveBehaviorConfig(
        preparation=PreparationBehavior(
            read_documents=False, load_memory=True, inspect_code=False
        ),
    )
    builder = ExecutionContextBuilder(retriever=learning_retriever)
    ctx = builder.build(cmd, behavior=behavior, db=db_session)

    assert len(ctx.learnings) == 1
    assert ctx.learnings[0].summary == "Handle timeout gracefully"
    assert ctx.sources_resolved["learnings_from_db"] is True


def test_context_builder_respects_load_memory_false(db_session, fixture_world):
    """P1 关键修复：load_memory=False 时绝对不查 learnings。"""
    cmd = ExecutionCommand(
        execution_id="exec-lrn-off",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=fixture_world["task"].id,
        context={
            "project_id": fixture_world["project"].id,
            "title": "Implement Redis Cache",
            "description": "Add caching layer for user profiles",
        },
    )
    behavior = EffectiveBehaviorConfig(
        preparation=PreparationBehavior(
            read_documents=False, load_memory=False, inspect_code=False
        ),
    )
    builder = ExecutionContextBuilder(retriever=learning_retriever)
    ctx = builder.build(cmd, behavior=behavior, db=db_session)

    # load_memory 关 → 不查 retriever
    assert len(ctx.learnings) == 0
    assert ctx.sources_resolved["learnings_from_db"] is False
    # 同时 summary 里不应出现 "相关项目经验" 段
    assert "相关项目经验" not in ctx.raw_context_summary


def test_context_builder_db_preferred_over_ctx(db_session, fixture_world):
    """DB 路径优先于 ctx 预填（DB 是 server 端 source of truth）。"""
    cmd = ExecutionCommand(
        execution_id="exec-priority",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=fixture_world["task"].id,
        context={
            "title": "x",
            "description": "y",
            "comments": [
                {"id": 999, "author_username": "ghost", "content": "Should be ignored"}
            ],
        },
    )
    behavior = EffectiveBehaviorConfig(
        preparation=PreparationBehavior(
            read_documents=False, load_memory=False, inspect_code=False
        ),
        collaboration=CollaborationBehavior(read_comments=True),
    )
    builder = ExecutionContextBuilder()
    ctx = builder.build(cmd, behavior=behavior, db=db_session)

    # 必须拿到 DB 里的 3 条评论，不是 ctx 里的 ghost
    assert len(ctx.comments) == 3
    assert "ghost" not in {c.author for c in ctx.comments}
    assert ctx.sources_resolved["comments_from_db"] is True
    assert ctx.sources_resolved["comments_from_ctx"] is False


def test_context_builder_falls_back_to_ctx_when_no_db():
    """没传 db 时使用 ctx 预填数据（向后兼容旧调用 / preview 场景）。"""
    cmd = ExecutionCommand(
        execution_id="exec-fallback",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=99,
        context={
            "title": "Old API",
            "description": "x",
            "comments": [
                {"id": 1, "author_username": "fallback", "content": "from ctx"}
            ],
            "documents": [
                {"id": 2, "title": "From Ctx", "type": "design", "content": "x"}
            ],
        },
    )
    behavior = EffectiveBehaviorConfig(
        preparation=PreparationBehavior(
            read_documents=True, load_memory=True, inspect_code=True
        ),
        collaboration=CollaborationBehavior(read_comments=True),
    )
    builder = ExecutionContextBuilder()
    ctx = builder.build(cmd, behavior=behavior, db=None)

    assert len(ctx.comments) == 1
    assert ctx.comments[0].author == "fallback"
    assert len(ctx.documents) == 1
    assert ctx.documents[0].title == "From Ctx"
    assert ctx.sources_resolved["comments_from_ctx"] is True
    assert ctx.sources_resolved["documents_from_ctx"] is True
