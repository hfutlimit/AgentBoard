"""Execution error classification shared by handlers and the MQ bridge.

Phase 5 P1 修复（2026-08-26 review）：
- 原 ``is_transient_execution_error(exc) -> bool`` 只能返回 True/False，
  调用方无法区分"已识别 transient" / "已识别 permanent" / "未知"，
  默认行为是"未识别 = permanent"（保守），但 invoker 把所有非零退出码
  都包成 ``TransientAgentError``（"非零 = 一律 retry"）违反了这个原则。
- 新增 ``ErrorCategory`` 三态 + ``classify_error(exc) -> ErrorCategory`` 统一入口。
- ``is_transient_execution_error`` / ``is_permanent_execution_error`` 保留作
  薄包装（向后兼容 + 调用方便）。
- 默认未识别错误 → PERMANENT（"未明确标 transient = 不重试"原则）。
"""
from __future__ import annotations

import enum
import re
from typing import Final

import httpx
from sqlalchemy.exc import OperationalError

from agentboard.core.infrastructure.messaging.rabbitmq import MessageRetry
from .config import AgentInvocationError, PermanentAgentError, TransientAgentError


class ErrorCategory(enum.Enum):
    """统一错误分类。"""
    TRANSIENT = "transient"   # 可安全重试（网络瞬时、5xx、429、超时）
    PERMANENT = "permanent"   # 不会因重试而变好（auth、config、命令缺失、invalid args）
    UNKNOWN = "unknown"       # 未识别 — 默认走 PERMANENT 路径（保守不重试）


# stderr 关键字 → 分类映射（invoker 退出非零时用）
# 正则大小写不敏感；优先匹配 PERMANENT 关键词（更保守）
_PERMANENT_STDERR_PATTERNS: Final[tuple[re.Pattern, ...]] = (
    re.compile(r"\b(unauthorized|forbidden|permission denied|api[_\s]?key|auth)\b", re.I),
    re.compile(r"\b(invalid|malformed|bad[_\s]?arg|usage)\b", re.I),
    re.compile(r"\b(config|configuration|missing[_\s]?required)\b", re.I),
    re.compile(r"\b(model[_\s]?not[_\s]?found|unknown[_\s]?model|unsupported[_\s]?model)\b", re.I),
    re.compile(r"\b(quota[_\s]?exceeded|rate[_\s]?limit[_\s]?exceeded|billing)\b", re.I),
)

_TRANSIENT_STDERR_PATTERNS: Final[tuple[re.Pattern, ...]] = (
    re.compile(r"\b(timeout|timed[_\s]?out|connection[_\s]?(refused|reset))\b", re.I),
    re.compile(r"\b(temporar(y|ily)|try[_\s]?again[_\s]?later|retry)\b", re.I),
    re.compile(r"\b(rate[_\s]?limit|too[_\s]?many[_\s]?requests|429)\b", re.I),
    re.compile(r"\b(5\d\d|server[_\s]?error|upstream)\b", re.I),
)


def classify_stderr(stderr: str) -> ErrorCategory:
    """从 subprocess stderr 文本推断错误分类（Phase 5 P1 引入）。

    - 优先 PERMANENT 关键词（auth / config / invalid / quota 之类）—— 保守
    - 次选 TRANSIENT 关键词（timeout / 5xx / 429）
    - 没匹配到任何 → UNKNOWN（让调用方走默认 PERMANENT 路径）
    """
    if not stderr:
        return ErrorCategory.UNKNOWN
    for pat in _PERMANENT_STDERR_PATTERNS:
        if pat.search(stderr):
            return ErrorCategory.PERMANENT
    for pat in _TRANSIENT_STDERR_PATTERNS:
        if pat.search(stderr):
            return ErrorCategory.TRANSIENT
    return ErrorCategory.UNKNOWN


def classify_error(exc: BaseException) -> ErrorCategory:
    """统一错误分类入口（Phase 5 P1）。

    分类优先级：
    1. MessageRetry / TransientAgentError 显式 → TRANSIENT
    2. PermanentAgentError 显式 → PERMANENT
    3. HTTP 5xx / 429 / 超时 / 网络错 / OperationalError → TRANSIENT
    4. AgentInvocationError 基础类 → TRANSIENT（旧 invoker 抛基础类视为可重试）
    5. 其他 → UNKNOWN（调用方决定如何处理；默认建议 PERMANENT 不重试）
    """
    if isinstance(exc, MessageRetry):
        return ErrorCategory.TRANSIENT
    if isinstance(exc, TransientAgentError):
        return ErrorCategory.TRANSIENT
    if isinstance(exc, PermanentAgentError):
        return ErrorCategory.PERMANENT
    if isinstance(exc, (httpx.TimeoutException, TimeoutError, httpx.NetworkError)):
        return ErrorCategory.TRANSIENT
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else 0
        if status == 429 or status >= 500:
            return ErrorCategory.TRANSIENT
        return ErrorCategory.PERMANENT  # 4xx（非 429）= 客户端错，不重试
    if isinstance(exc, httpx.HTTPError):
        return ErrorCategory.TRANSIENT
    if isinstance(exc, OperationalError):
        return ErrorCategory.TRANSIENT
    # AgentInvocationError 基础类默认 transient（旧 invoker 兼容）
    if isinstance(exc, AgentInvocationError):
        return ErrorCategory.TRANSIENT
    return ErrorCategory.UNKNOWN


def is_transient_execution_error(exc: BaseException) -> bool:
    """向后兼容的薄包装。Phase 5 P1 后新代码请用 ``classify_error``。"""
    return classify_error(exc) is ErrorCategory.TRANSIENT


def is_permanent_execution_error(exc: BaseException) -> bool:
    """判断错误是否应明确归类为 PERMANENT（UNKNOW 默认不视为 permanent）。"""
    return classify_error(exc) is ErrorCategory.PERMANENT
