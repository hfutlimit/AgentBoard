"""Phase 2 P1 测试（2026-08-26 review 三件套）。

覆盖：
1. Coordinator._message_consumed 把 attempt 写到 DB（message_attempts 表）
2. 新 Coordinator 实例（模拟 worker restart）从 DB 读回同一 attempt
3. attempt 达到上限（6）后 _message_consumed 返回 False（dead-letter）
4. in-memory fallback 仍工作（session_factory=None → dict 行为不变）
5. 非 transient / dead-lettered → _delete_attempt 清掉
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 重要：time.sleep 是 module-level import，coordinator.py 调的是
# `time.sleep(delay)`（不是 `coordinator.time.sleep`）。
# 用 pytest fixture monkeypatch 局部屏蔽，全局屏蔽会破坏其他测试的 sleep 行为。
from agentboard.processors.config import ProcessorConfig
from agentboard.processors.coordinator import (
    ProcessorCoordinator, WORKFLOW_RETRY_BACKOFF_SECONDS, _execution_id_from_retry_key,
)
from agentboard.processors.contract import (
    ExecutionResult, ExecutionStatus, ExecutionAction,
)
from agentboard.processors.config import TransientAgentError


@pytest.fixture
def fast_sleep(monkeypatch):
    """把 time.sleep 屏蔽掉（仅本测试范围）—— coordinator 的 backoff sleep 不真等。"""
    import time
    monkeypatch.setattr(time, "sleep", lambda *_a, **_kw: None)
    yield

from agentboard.core.infrastructure import messaging as mq


def _make_session_factory():
    """每个测试一个临时 SQLite（in-memory 之外的独立文件，避免连接竞争）。"""
    db = f"_phase2_test_{uuid.uuid4().hex[:8]}.db"
    eng = create_engine(
        f"sqlite:///{db}", connect_args={"check_same_thread": False},
    )
    # agentboard/__init__.py 是空 docstring，必须显式触发 model 注册。
    # MessageAttempt 本身无 FK，但 features.scheduling.models 包含
    # AgentSchedule / AgentRun / RunEvent / TaskAssignment 等带 FK 的模型，
    # 需要把被引用的表（tasks/projects/agents/users/...）全部 import 才能
    # 让 Base.metadata.create_all 成功。
    from agentboard.features.scheduling.models import (  # noqa: F401
        MessageAttempt, AgentSchedule, AgentRun, RunEvent,
        TaskAssignment, TaskApplication, AgentBehaviorConfig,
    )
    from agentboard.features.work_items.models import (  # noqa: F401
        Task, Comment, Attachment,
    )
    from agentboard.features.projects.models import (  # noqa: F401
        Project, ProjectMember, ReviewVote, Story, Sprint, Epic,
    )
    from agentboard.features.identity.models import User, ApiKey  # noqa: F401
    from agentboard.core.common.models import Base
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, autoflush=False, autocommit=False, future=True)
    return eng, Sess, db


def _cleanup(eng, db):
    eng.dispose()
    if os.path.exists(db):
        os.remove(db)


def _build_coordinator(*, session_factory=None, invoker=None, client=None):
    """构造 coordinator，跳过 ProposalProcessor（只测 coordinator 自身逻辑）。"""
    cfg = ProcessorConfig(
        api_url="http://127.0.0.1:9", token="t",
        agent_cmd='"echo" "noop"',
        use_coordinator=True, async_story_executor=False,
    )
    return ProcessorCoordinator(
        config=cfg, invoker=invoker, client=client,
        session_factory=session_factory,
    )


# =============== 1. DB 持久化基本流程 ===============

def test_message_consumed_records_attempt_to_db(fast_sleep):
    """第一次 transient failure：attempt 写入 DB，_message_consumed 抛 MessageRetry。"""
    eng, Sess, db = _make_session_factory()
    try:
        coord = _build_coordinator(session_factory=Sess)
        retry_key = ("task.review_requested", "task", 100, 0)
        result = ExecutionResult.from_exception(
            execution_id="test", error=TransientAgentError("5xx upstream"),
            action="fail",
        )
        with pytest.raises(mq.MessageRetry):
            coord._message_consumed(result, retry_key)
        # DB 验证
        s = Sess()
        try:
            from agentboard.features.scheduling.models import MessageAttempt
            row = s.query(MessageAttempt).filter(
                MessageAttempt.execution_id == _execution_id_from_retry_key(retry_key),
            ).first()
            assert row is not None, "message_attempts 应该有记录"
            assert row.attempt == 1, f"第一次失败 attempt 应=1，实际 {row.attempt}"
            assert row.status == "pending"
            assert row.last_event == "task.review_requested"
        finally:
            s.close()
    finally:
        _cleanup(eng, db)


# =============== 2. 跨进程 attempt 读取（核心 P1 修复验证）===============

def test_attempt_persists_across_coordinator_instances(fast_sleep):
    """模拟 worker A → 处理失败 → worker B（重启后）→ 读回同一 attempt。

    这是 review 关键场景：旧 in-memory dict 在 worker restart 时归零，
    导致同一条消息可被无限重试。DB 持久化保证跨进程 attempt 一致。
    """
    eng, Sess, db = _make_session_factory()
    try:
        retry_key = ("task.review_requested", "task", 200, 0)
        execution_id = _execution_id_from_retry_key(retry_key)

        # Worker A：2 次 transient failure
        coord_a = _build_coordinator(session_factory=Sess)
        for i in range(2):
            result = ExecutionResult.from_exception(
                execution_id=execution_id, error=TransientAgentError("5xx"),
                action="fail",
            )
            with pytest.raises(mq.MessageRetry):
                coord_a._message_consumed(result, retry_key)
        coord_a.close()

        # Worker B：新实例，模拟 worker A 进程崩溃后重启
        coord_b = _build_coordinator(session_factory=Sess)
        try:
            # 第一次处理（attempt 2 → 3）
            result = ExecutionResult.from_exception(
                execution_id=execution_id, error=TransientAgentError("5xx"),
                action="fail",
            )
            with pytest.raises(mq.MessageRetry):
                coord_b._message_consumed(result, retry_key)
            # 验证 DB：attempt 应该是 3（不是 1，证明 in-memory dict 没用）
            s = Sess()
            try:
                from agentboard.features.scheduling.models import MessageAttempt
                row = s.query(MessageAttempt).filter(
                    MessageAttempt.execution_id == execution_id,
                ).first()
                assert row.attempt == 3, (
                    f"跨进程后 attempt 应=3（持久化），实际 {row.attempt} "
                    f"（如果=1 说明走了 in-memory fallback，BUG）"
                )
            finally:
                s.close()
        finally:
            coord_b.close()
    finally:
        _cleanup(eng, db)


# =============== 3. 达到上限 dead-letter ===============

def test_attempt_limit_triggers_dead_letter(fast_sleep):
    """第 7 次 transient failure（超过 WORKFLOW_RETRY_BACKOFF_SECONDS 长度 6）→ dead-letter。"""
    eng, Sess, db = _make_session_factory()
    try:
        coord = _build_coordinator(session_factory=Sess)
        retry_key = ("comment.replied", "task", 300, 1)
        execution_id = _execution_id_from_retry_key(retry_key)

        # 触发 6 次（应该全部 raise MessageRetry）
        for i in range(len(WORKFLOW_RETRY_BACKOFF_SECONDS)):
            result = ExecutionResult.from_exception(
                execution_id=execution_id, error=TransientAgentError("5xx"),
                action="fail",
            )
            with pytest.raises(mq.MessageRetry):
                coord._message_consumed(result, retry_key)

        # 第 7 次：超过上限 → 返回 False（dead-letter），不抛
        result = ExecutionResult.from_exception(
            execution_id=execution_id, error=TransientAgentError("5xx"),
            action="fail",
        )
        result_status = coord._message_consumed(result, retry_key)
        assert result_status is False, (
            f"超过 6 次后 _message_consumed 应返回 False（dead-letter），"
            f"实际 {result_status!r}"
        )
        # 验证 DB：status 应该是 dead_lettered
        s = Sess()
        try:
            from agentboard.features.scheduling.models import MessageAttempt
            row = s.query(MessageAttempt).filter(
                MessageAttempt.execution_id == execution_id,
            ).first()
            assert row.status == "dead_lettered"
            assert row.attempt == 6  # 6 次 + 第 7 次 dead-letter，attempt 停在 6
        finally:
            s.close()
    finally:
        _cleanup(eng, db)


# =============== 4. in-memory fallback（session_factory=None）==============

def test_in_memory_fallback_when_no_session_factory(fast_sleep):
    """session_factory=None → 走旧 in-memory dict 行为（dev 模式 / 单 Worker）。"""
    coord = _build_coordinator(session_factory=None)
    try:
        retry_key = ("task.review_requested", "task", 400, 0)
        execution_id = _execution_id_from_retry_key(retry_key)
        # 第一次
        result = ExecutionResult.from_exception(
            execution_id=execution_id, error=TransientAgentError("5xx"), action="fail",
        )
        with pytest.raises(mq.MessageRetry):
            coord._message_consumed(result, retry_key)
        # in-memory dict 应该有 attempt
        assert coord._msg_retries[retry_key] == 1
    finally:
        coord.close()


def test_in_memory_fallback_dead_letter(fast_sleep):
    """in-memory fallback 达到上限也返回 False。"""
    coord = _build_coordinator(session_factory=None)
    try:
        retry_key = ("task.review_requested", "task", 500, 0)
        execution_id = _execution_id_from_retry_key(retry_key)
        # 灌满
        for i in range(len(WORKFLOW_RETRY_BACKOFF_SECONDS)):
            result = ExecutionResult.from_exception(
                execution_id=execution_id, error=TransientAgentError("5xx"), action="fail",
            )
            with pytest.raises(mq.MessageRetry):
                coord._message_consumed(result, retry_key)
        # 第 7 次
        result = ExecutionResult.from_exception(
            execution_id=execution_id, error=TransientAgentError("5xx"), action="fail",
        )
        assert coord._message_consumed(result, retry_key) is False
    finally:
        coord.close()


# =============== 5. 非 transient / dead-lettered 清 attempt ===============

def test_non_transient_clears_attempt_in_db():
    """非 transient 失败（permanent）→ _delete_attempt 清掉 DB 记录。"""
    eng, Sess, db = _make_session_factory()
    try:
        coord = _build_coordinator(session_factory=Sess)
        retry_key = ("task.review_requested", "task", 600, 0)
        execution_id = _execution_id_from_retry_key(retry_key)
        # 先写一个 attempt
        coord._set_attempt(execution_id, 2, last_error="x", status="pending", retry_key=retry_key)
        s = Sess()
        try:
            from agentboard.features.scheduling.models import MessageAttempt
            row = s.query(MessageAttempt).filter(
                MessageAttempt.execution_id == execution_id,
            ).first()
            assert row is not None
            assert row.attempt == 2
        finally:
            s.close()
        # 非 transient → 调 _message_consumed 应该清掉
        result = ExecutionResult.failure(
            execution_id=execution_id, error="permanent", action="fail",
        )
        # result.status 默认是 FAILED（不是 FAILED_TRANSIENT），应该 return True
        result_status = coord._message_consumed(result, retry_key)
        assert result_status is True
        # DB 验证：行已删
        s = Sess()
        try:
            from agentboard.features.scheduling.models import MessageAttempt
            row = s.query(MessageAttempt).filter(
                MessageAttempt.execution_id == execution_id,
            ).first()
            assert row is None, (
                f"非 transient 应清掉 attempt，但 row={row}"
            )
        finally:
            s.close()
    finally:
        _cleanup(eng, db)


# =============== 6. execution_id 派生 ===============

def test_execution_id_from_retry_key_format():
    """execution_id 字符串格式稳定可逆。"""
    k = ("task.review_requested", "task", 100, 5)
    eid = _execution_id_from_retry_key(k)
    assert eid == "task.review_requested:task:100:5"
    # 反解
    recovered = ProcessorCoordinator._retry_key_from_execution_id(eid)
    assert recovered == k
