"""Execution error classification shared by handlers and the MQ bridge."""
from __future__ import annotations

import httpx
from sqlalchemy.exc import OperationalError

from agentboard.core.infrastructure.messaging.rabbitmq import MessageRetry
from .config import AgentInvocationError, PermanentAgentError, TransientAgentError


def is_transient_execution_error(exc: BaseException) -> bool:
    """Return whether retrying the same command can reasonably succeed later.

    分类优先级（2026-08-26 P1 修复）：
    1. MessageRetry / TransientAgentError / 任何显式 transient 子类 → True
    2. PermanentAgentError / 任何显式 permanent 子类 → False
    3. HTTP 超时 / 429 / 5xx / 网络错 / OperationalError → True
    4. **AgentInvocationError 基础类** → True（默认 transient；这是
       "未明确标 permanent = 默认可重试"原则，避免基础类被错认为永久失败）
    5. 其他未识别错误 → False（保守：不重试未知的，避免卡死）
    """
    if isinstance(exc, MessageRetry):
        return True
    if isinstance(exc, TransientAgentError):
        return True
    if isinstance(exc, PermanentAgentError):
        return False
    if isinstance(exc, (httpx.TimeoutException, TimeoutError, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else 0
        return status == 429 or status >= 500
    if isinstance(exc, httpx.HTTPError):
        return True
    if isinstance(exc, OperationalError):
        return True
    # P1（2026-08-26）：AgentInvocationError 基础类默认 transient。
    # 防止旧测试 + 旧 invoker 抛基础类时，被错误分类为 FAILED_PERMANENT →
    # Story → blocked。三次转 blocked 留给真正的 PermanentAgentError 子类。
    if isinstance(exc, AgentInvocationError):
        return True
    return False
