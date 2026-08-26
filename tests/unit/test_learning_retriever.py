import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

DB_PATH = "_test_learning_retriever_tmp.db"
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///./{DB_PATH}"

import pytest
from agentboard.core.infrastructure.database import SessionLocal, engine, init_db
from agentboard.features.projects.models import Project
from agentboard.features.learning.models import Learning
from agentboard.agent_runtime.learning.retriever import LearningRetriever, learning_retriever


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


def test_learning_retriever_ranks_by_relevance(db_session):
    p1 = Project(name=f"P1_{uuid.uuid4().hex[:6]}")
    p2 = Project(name=f"P2_{uuid.uuid4().hex[:6]}")
    db_session.add_all([p1, p2])
    db_session.commit()

    # 插入学习记录 (使用动态生成的 project_id)
    l1 = Learning(
        project_id=p1.id,
        agent_id=None,
        work_type="dev",
        category="accepted_review_feedback",
        summary="Always add composite index for multi-column search",
        lesson="Composite index prevents table scan on sqlite",
        tags_json='["database", "index", "performance"]',
        confidence=1.0,
    )
    l2 = Learning(
        project_id=p1.id,
        agent_id=None,
        work_type="qa",
        category="qa_defect",
        summary="UI button disabled state check",
        lesson="Assert button is disabled during submission",
        tags_json='["ui", "form", "button"]',
        confidence=0.9,
    )
    l3 = Learning(
        project_id=p2.id,  # different project
        agent_id=None,
        work_type="dev",
        category="accepted_review_feedback",
        summary="Project 2 specific rule",
        lesson="Ignore in project 1",
        tags_json='["database"]',
        confidence=1.0,
    )
    db_session.add_all([l1, l2, l3])
    db_session.commit()

    # 检索项目 1 下关于 database index 的 dev 任务经验
    results = learning_retriever.retrieve(
        project_id=p1.id,
        agent_id=None,
        work_type="dev",
        title="Optimize database query performance with index",
        description="Add composite index to tasks table",
        db=db_session,
        limit=5,
    )

    assert len(results) >= 1
    # l1 应该排在最前面因为 project=p1.id, work_type=dev, tags 命中 database/index/performance
    assert results[0]["id"] == l1.id
    assert "Composite index" in results[0]["lesson"]
    # l3 属于 project 2，绝不可出现在 project 1 的结果中
    ids = [r["id"] for r in results]
    assert l3.id not in ids