"""可观测性:logging / metrics / tracing 三个支柱的统一切面。

Phase 1 落地:
- ``logging.py``: 结构化 JSON 日志 + request_id 贯穿
- ``metrics.py``: Prometheus 指标(请求计数/延迟/缓存命中率/MQ 深度)
- ``tracing.py``: OpenTelemetry span(本阶段先占位,默认 no-op,Phase 1 末尾激活)
"""
from __future__ import annotations

from .logging import configure_logging, get_logger, bind_request_id, current_request_id  # noqa: F401
from .metrics import metrics, MetricsRecorder  # noqa: F401
from .tracing import tracer, get_tracer, trace_span  # noqa: F401

__all__ = [
    "configure_logging",
    "get_logger",
    "bind_request_id",
    "current_request_id",
    "metrics",
    "MetricsRecorder",
    "tracer",
    "get_tracer",
    "trace_span",
]
