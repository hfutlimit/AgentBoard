"""FastAPI app factory.

Phase 1 末尾启用:本阶段只装配 middleware / exception handlers / CORS / 健康检查
端点。具体 router 在 Phase 5 从 ``agentboard.api`` 迁入 ``app.include_router(...)``。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import settings
from ..infrastructure.auth import validate_runtime_security
from ..infrastructure.database import init_db
from ..observability.logging import configure_logging
from ..observability.metrics import metrics
from ..observability.tracing import init_tracing
from .errors import register_exception_handlers
from .middleware import RequestContextMiddleware, RequestLoggingMiddleware

log = logging.getLogger("agentboard.api")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """启动钩子:日志、配置校验、DB 迁移、可观测性初始化。"""
    configure_logging()
    init_tracing()
    log.info("startup", extra={"env": settings.env, "db_url_kind": settings.db_url.split("://", 1)[0]})
    try:
        validate_runtime_security()
    except RuntimeError as e:
        # 开发环境允许,生产启动失败
        if settings.is_production:
            raise
        log.warning("runtime_security_warning", extra={"reason": str(e)})
    if app_state.pop("skip_db_init", False):
        log.info("db_init_skipped")
    else:
        init_db()
    yield
    log.info("shutdown")


# app.state 上的小工具,tests/conftest.py 可用
app_state: dict[str, bool] = {}


def create_app(*, skip_db_init: bool = False) -> FastAPI:
    """Build a configured FastAPI app.

    Args:
        skip_db_init: 跳过 Alembic 迁移(测试场景用)。
    """
    app_state["skip_db_init"] = skip_db_init
    app = FastAPI(
        title="AgentBoard API",
        version="2.0.0",
        description="AgentBoard 后端 API(2026-08 重构,vertical-slice 架构)",
        debug=settings.debug,
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    # 注意中间件顺序:外层后 add 的先执行
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    # ---- 健康检查 / 指标暴露 ----
    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["meta"])
    async def readyz() -> dict[str, str]:
        # 简化版:DB 探活
        from sqlalchemy import text
        from ..infrastructure.database import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.get("/metrics", tags=["meta"], include_in_schema=False)
    async def prometheus_metrics() -> "Response":  # type: ignore[name-defined]
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(metrics.render_prometheus(), media_type="text/plain; version=0.0.4")

    # ---- Phase 5 会在这里 include_router(各 feature.router) ----
    # from agentboard.features.projects.router import router as projects_router
    # app.include_router(projects_router, prefix="/api")

    return app
