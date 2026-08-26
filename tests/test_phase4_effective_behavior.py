"""Phase 4 P1 测试（2026-08-26 review）。

覆盖：
1. ``GET /api/agent-behavior/effective`` 端点（server 端，3 参全 optional）
2. Worker 路径 ``prepare_execution(client=...)`` 调该端点拿 EffectiveBehaviorConfig
3. Fallback：client 调失败 / db=None / client=None → 走 system default
4. db 优先于 client（server-side scenario）
"""
from __future__ import annotations

import os
import sys
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest
from fastapi.testclient import TestClient

from agentboard.agent_runtime.contract import ExecutionCommand, WorkType
from agentboard.agent_runtime._prepared import prepare_execution
from agentboard.agent_runtime.behavior.models import (
    EffectiveBehaviorConfig, PreparationBehavior, CollaborationBehavior,
    LearningBehavior,
)


class _FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


class _FakeClient:
    """模拟 httpx.Client：记录请求，返回编程响应。"""
    def __init__(self, response_status: int = 200, response_body: dict | None = None):
        self.calls: list[tuple[str, str, dict]] = []
        self.response_status = response_status
        self.response_body = response_body or {
            "preset": "test",
            "preset_version": 1,
            "preparation": {"sync_code": False, "checkout_branch": False, "inspect_code": True},
            "collaboration": {"read_comments": True, "leave_summary": True, "reply_to_review": True},
            "learning": {"accepted_correction": True, "judgment_reversal": True},
            "document_sources": [],
            "additional_instructions": None,
            "sources": {"system": True},
        }

    def request(self, method: str, path: str, params: dict | None = None):
        self.calls.append((method, path, params or {}))
        return _FakeResponse(self.response_status, self.response_body)


# =============== 1. prepare_execution 走 client 路径 ===============

def test_prepare_execution_with_client_calls_effective_endpoint():
    """Worker 路径：``client`` 不为 None → 调 ``GET /api/agent-behavior/effective``。"""
    client = _FakeClient()
    cmd = ExecutionCommand(
        execution_id="test_1",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=100,
        context={"project_id": 5, "agent_id": 3, "task_id": 100},
    )
    result = prepare_execution(cmd, client=client)
    # 验证：endpoint 被调
    assert len(client.calls) == 1
    method, path, params = client.calls[0]
    assert method == "GET"
    assert path == "/api/agent-behavior/effective"
    assert params == {"project_id": 5, "agent_id": 3, "work_type": "implementation"}


def test_prepare_execution_with_client_uses_effective_config():
    """返回的 PreparedExecution 用的 behavior 来自 client response（不是 system default）。"""
    custom_body = {
        "preset": "custom",
        "preset_version": 99,
        "preparation": {"sync_code": True, "checkout_branch": True, "inspect_code": False},
        "collaboration": {"read_comments": False, "leave_summary": True, "reply_to_review": False},
        "learning": {"accepted_correction": False, "judgment_reversal": False},
        "document_sources": [],
        "additional_instructions": "use this hint",
        "sources": {"project": True, "system": True},
    }
    client = _FakeClient(response_status=200, response_body=custom_body)
    cmd = ExecutionCommand(
        execution_id="test_2",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=1,
        context={"project_id": 1},
    )
    result = prepare_execution(cmd, client=client)
    # effective config 来自 response
    assert result.behavior.preset == "custom"
    assert result.behavior.preset_version == 99
    assert result.behavior.preparation.sync_code is True
    assert result.behavior.preparation.checkout_branch is True
    assert result.behavior.preparation.inspect_code is False
    assert result.behavior.additional_instructions == "use this hint"
    # 验证 response 被 client 拿到并使用
    assert result.behavior.sources.get("project") is True


def test_prepare_execution_with_client_only_work_type():
    """仅 work_type（无 project/agent）→ params 不带空键。"""
    client = _FakeClient()
    cmd = ExecutionCommand(
        execution_id="test_3",
        work_type=WorkType.QA,
        entity_type="task",
        entity_id=1,
        context={},
    )
    prepare_execution(cmd, client=client)
    params = client.calls[0][2]
    # 不带空值参数
    assert "project_id" not in params
    assert "agent_id" not in params
    assert params.get("work_type") == "qa"


# =============== 2. Fallback 行为 ===============

def test_prepare_execution_no_db_no_client_falls_back_to_system_default():
    """client=None + db=None → 走 BehaviorResolver.db=None 路径（system default）。"""
    cmd = ExecutionCommand(
        execution_id="test_4",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=1,
        context={},
    )
    result = prepare_execution(cmd)  # 全 None
    # system default preset_version 应是 PRESET_VERSION
    from agentboard.agent_runtime.behavior.defaults import PRESET_VERSION
    assert result.behavior.preset_version == PRESET_VERSION


def test_prepare_execution_client_failure_falls_back_to_system_default():
    """client 返回 500 → 退到 system default，不抛异常。"""
    client = _FakeClient(response_status=500)
    cmd = ExecutionCommand(
        execution_id="test_5",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=1,
        context={"project_id": 1},
    )
    # 不应抛
    result = prepare_execution(cmd, client=client)
    # 退到 system default
    from agentboard.agent_runtime.behavior.defaults import PRESET_VERSION
    assert result.behavior.preset_version == PRESET_VERSION


# =============== 3. db 优先于 client ===============

def test_db_takes_priority_over_client():
    """db != None → 走 DB 路径，不调 client。"""
    client = _FakeClient()
    fake_db = mock.MagicMock()
    # 我们这里只验证 client 没被调
    cmd = ExecutionCommand(
        execution_id="test_6",
        work_type=WorkType.IMPLEMENTATION,
        entity_type="task",
        entity_id=1,
        context={"project_id": 1},
    )
    result = prepare_execution(cmd, db=fake_db, client=client)
    assert len(client.calls) == 0, (
        f"db 优先时 client 不应被调，但 client.calls={client.calls}"
    )


# =============== 4. 服务端端点 — 单元测（直接调 endpoint 函数）==============

def test_effective_endpoint_resolves_with_db_session():
    """``get_effective_behavior`` endpoint 函数：db session 路径返回 system default。
    直接调函数测逻辑（end-to-end FastAPI 集成由 test_admin_api_key_scope 等覆盖）。"""
    from agentboard.features.scheduling.behavior_router import get_effective_behavior
    from unittest.mock import MagicMock

    fake_db = MagicMock()
    result = get_effective_behavior(
        project_id=None, agent_id=None, work_type=None,
        authorization=None, s=fake_db,
    )
    # 应返回 EffectiveBehaviorConfig 实例
    assert result.preset == "agentboard-default"
    # 验证：来源标记 system=True
    assert result.sources.get("system") is True


def test_effective_endpoint_unknown_work_type_raises_400():
    """``work_type=garbage`` → endpoint 抛 HTTPException(400)。"""
    from fastapi import HTTPException
    from agentboard.features.scheduling.behavior_router import get_effective_behavior
    from unittest.mock import MagicMock

    fake_db = MagicMock()
    with pytest.raises(HTTPException) as exc_info:
        get_effective_behavior(
            project_id=None, agent_id=None, work_type="garbage_xyz",
            authorization=None, s=fake_db,
        )
    assert exc_info.value.status_code == 400
    assert "work_type" in exc_info.value.detail


def test_effective_endpoint_canonicalizes_valid_work_type():
    """``work_type=task_implement`` (legacy alias) 也能解析。"""
    from agentboard.features.scheduling.behavior_router import get_effective_behavior
    from unittest.mock import MagicMock

    fake_db = MagicMock()
    result = get_effective_behavior(
        project_id=None, agent_id=None, work_type="task_implement",
        authorization=None, s=fake_db,
    )
    assert result.preset == "agentboard-default"
