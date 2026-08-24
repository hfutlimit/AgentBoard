"""FastAPI exception handlers: domain exception → HTTP response。

service 层只 ``raise DomainError`` 子类,本模块统一映射到 JSON 响应。
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..exceptions import DomainError
from ..observability.logging import current_request_id

log = logging.getLogger("agentboard.api.errors")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        body = exc.to_dict()
        body["request_id"] = current_request_id()
        if exc.http_status >= 500:
            log.exception("domain_error", extra={"code": exc.code, "message": exc.message})
        else:
            log.warning("domain_error", extra={"code": exc.code, "message": exc.message, "status": exc.http_status})
        return JSONResponse(status_code=exc.http_status, content=body)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": "http_error",
                "message": exc.detail if isinstance(exc.detail, str) else "http error",
                "details": {},
                "request_id": current_request_id(),
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", extra={"error": str(exc)})
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "internal server error",
                "details": {},
                "request_id": current_request_id(),
            },
        )
