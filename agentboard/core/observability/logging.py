"""Structured JSON logging + request_id propagation.

特性:
- JSON 格式(生产) / 人类可读(开发) 可切换(``settings.log_json``)
- ``bind_request_id()`` 在 middleware 里设值,所有 log 自动带 ``request_id``
- ``contextvars`` 跨 async 任务安全

Usage::

    from agentboard.core.observability import configure_logging, get_logger, bind_request_id
    configure_logging()
    log = get_logger(__name__)
    bind_request_id("req-abc-123")
    log.info("user_login", extra={"user_id": 42})
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from ..config import settings

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_user_id_var: ContextVar[int | None] = ContextVar("user_id", default=None)


# ---- public helpers --------------------------------------------------------

def bind_request_id(request_id: str | None = None) -> str:
    """Set request_id for the current async context; auto-generate if None."""
    rid = request_id or f"req-{uuid.uuid4().hex[:12]}"
    _request_id_var.set(rid)
    return rid


def current_request_id() -> str | None:
    return _request_id_var.get()


def bind_user_id(user_id: int | None) -> None:
    _user_id_var.set(user_id)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ---- formatter --------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record, with request_id/user_id auto-injected."""

    # 标准库 LogRecord 字段,避免在 JSON 里出现
    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = current_request_id()
        if rid:
            payload["request_id"] = rid
        uid = _user_id_var.get()
        if uid is not None:
            payload["user_id"] = uid
        # 用户附加字段(用 log.info("event", extra={"k": v}))
        for k, v in record.__dict__.items():
            if k not in self._RESERVED and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _PlainFormatter(logging.Formatter):
    """Development-friendly:  ``2026-08-14 08:30:00 INFO  [req-abc] agentboard.api: user_login user_id=42``"""

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        rid = current_request_id() or "-"
        extras = " ".join(
            f"{k}={v}" for k, v in record.__dict__.items()
            if k not in _JsonFormatter._RESERVED and not k.startswith("_")
        )
        msg = record.getMessage()
        line = f"{ts} {record.levelname:<5} [{rid}] {record.name}: {msg}"
        if extras:
            line += f"  {extras}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


# ---- bootstrap --------------------------------------------------------------

_configured = False


def configure_logging() -> None:
    """Initialize root logger once. Idempotent."""
    global _configured
    if _configured:
        return
    _configured = True

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _JsonFormatter() if settings.log_json else _PlainFormatter()
    )
    root = logging.getLogger()
    # 避免重复 add(uvicorn reload 等场景)
    if not any(isinstance(h, logging.StreamHandler) and getattr(h, "_ab_handler", False) for h in root.handlers):
        handler._ab_handler = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(os.getenv("AGENTBOARD_LOG_LEVEL", settings.log_level).upper())

    # 调低第三方噪音
    for noisy in ("pika", "sqlalchemy.engine.Engine", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
