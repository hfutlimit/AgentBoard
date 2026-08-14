"""OpenTelemetry tracing 占位。

本阶段先建 no-op 接口,让 service 层可以 ``with trace_span("op"):`` 而不报 ImportError;
后续接 OTel SDK 时只改本文件。

设计:
- 默认 ``NoOpTracer``:trace_span 是 contextmanager,空操作
- 启用时(``settings.tracing_enabled=True``)接入 opentelemetry-sdk
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from ..config import settings

log = logging.getLogger("agentboard.tracing")


class NoOpSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass

    def set_status(self, status: str, description: str = "") -> None:
        pass


class NoOpTracer:
    @contextmanager
    def start_as_current_span(self, name: str, **kw: Any) -> Iterator[NoOpSpan]:
        log.debug("trace_span(no-op)", extra={"span": name, **kw})
        yield NoOpSpan()


# 单例:Phase 1 默认 no-op,settings.tracing_enabled 打开时升级为 OTel
tracer: Any = NoOpTracer()


def get_tracer(name: str) -> Any:
    return tracer


@contextmanager
def trace_span(name: str, **attrs: Any) -> Iterator[NoOpSpan]:
    """使用示例::

        from agentboard.core.observability import trace_span
        with trace_span("create_task", project_id=pid):
            ...
    """
    with tracer.start_as_current_span(name, attributes=attrs) as span:
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            raise


def init_tracing() -> None:
    """接入 OTel SDK(若 tracing_enabled 且 opentelemetry-api 可用)。"""
    global tracer
    if not settings.tracing_enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("tracing_enabled but opentelemetry-sdk not installed; keeping no-op")
        return
    resource = Resource.create({"service.name": "agentboard"})
    provider = TracerProvider(resource=resource)
    if settings.otel_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint)))
        except ImportError:
            log.warning("OTLPSpanExporter not available; tracing will be in-memory only")
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("agentboard")
    log.info("tracing initialized", extra={"endpoint": settings.otel_endpoint})
