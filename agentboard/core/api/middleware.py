"""FastAPI middleware: request_id propagation + structured access log.

Phase 1 实现 RequestContextMiddleware(设 request_id/user_id)
和 RequestLoggingMiddleware(请求结束打 access log + 喂 metrics)。
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ..observability.logging import bind_request_id, bind_user_id, current_request_id
from ..observability.metrics import metrics
from ..observability.tracing import trace_span

log = logging.getLogger("agentboard.api.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求生成/透传 ``X-Request-ID`` 并注入到日志 contextvars。

    客户端可通过 ``X-Request-ID`` 头指定 ID(便于跨服务追踪),否则自动生成。
    """

    HEADER = "X-Request-ID"

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(self.HEADER)
        rid = bind_request_id(incoming or f"req-{uuid.uuid4().hex[:12]}")
        request.state.request_id = rid
        # user_id 在 auth 依赖里 set
        bind_user_id(None)

        start = time.perf_counter()
        with trace_span("http_request", method=request.method, path=request.url.path):
            try:
                response: Response = await call_next(request)
            except Exception as e:
                elapsed = time.perf_counter() - start
                log.exception("request_failed", extra={
                    "method": request.method,
                    "path": request.url.path,
                    "elapsed_ms": int(elapsed * 1000),
                    "error": str(e),
                })
                raise

        elapsed = time.perf_counter() - start
        # 用 route 模板(若已解析)而不是 raw path,避免 cardinality 爆炸
        route = request.scope.get("route").path if request.scope.get("route") else request.url.path
        metrics.api_requests_total.inc(
            method=request.method, route=route, status=str(response.status_code)
        )
        metrics.api_request_duration.observe(elapsed)

        response.headers[self.HEADER] = rid
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """请求结束打一条 access log(JSON 格式,带 request_id/method/path/status/elapsed_ms)。"""

    async def dispatch(self, request: Request, call_next):
        # RequestContextMiddleware 已记了主要字段,本类作为补充:输出渲染后的 body 大小
        response = await call_next(request)
        if response.status_code >= 500:
            log.error("access", extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "request_id": current_request_id(),
            })
        return response
