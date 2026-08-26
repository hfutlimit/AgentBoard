"""Execution error classification shared by handlers and the MQ bridge."""
from __future__ import annotations

import httpx
from sqlalchemy.exc import OperationalError

from agentboard.core.infrastructure.messaging.rabbitmq import MessageRetry
from .config import PermanentAgentError, TransientAgentError


def is_transient_execution_error(exc: BaseException) -> bool:
    """Return whether retrying the same command can reasonably succeed later."""
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
    return False
