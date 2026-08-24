"""Application configuration via pydantic-settings (12-factor).

所有配置从环境变量读,带类型校验 + 默认值。生产环境用 ``AGENTBOARD_ENV=production``
激活安全检查(``core.infrastructure.auth.validate_runtime_security``)。

Usage::

    from agentboard.core.config import settings
    print(settings.db_url, settings.secret_key)
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings.

    字段按使用频率组织:基础设施(数据库/CORS) → 业务开关(缓存/MQ) → 集成密钥。
    """

    # ---- 运行环境 ----
    env: Literal["development", "staging", "production"] = Field(
        default="development",
        description="运行环境;production 下会触发安全检查",
    )
    debug: bool = Field(default=False, description="FastAPI debug 模式")

    # ---- 服务端 ----
    api_host: str = Field(default="0.0.0.0", alias="AGENTBOARD_API_HOST")
    api_port: int = Field(default=18000, alias="AGENTBOARD_API_PORT")
    web_host: str = Field(default="127.0.0.1", alias="AGENTBOARD_WEB_HOST")
    web_port: int = Field(default=28080, alias="AGENTBOARD_WEB_PORT")

    # ---- 数据库 ----
    db_url: str = Field(
        default="sqlite:///./agentboard.db",
        alias="AGENTBOARD_DB_URL",
        description="SQLAlchemy URL;默认 sqlite,生产用 MariaDB/MySQL",
    )

    # ---- 安全 ----
    secret_key: str = Field(
        default="dev-insecure-secret-change-me",
        alias="AGENTBOARD_SECRET",
        description="HMAC 签名密钥;生产必须 >= 32 字节",
    )
    token_ttl_seconds: int = Field(
        default=172800, alias="AGENTBOARD_TOKEN_TTL_SECONDS",
        description="Token 有效期(秒);默认 2 天",
    )
    require_auth: bool = Field(
        default=False, alias="AGENTBOARD_REQUIRE_AUTH",
        description="生产必须 True;开发可保持 False 兼容 MCP/Web 旧调用",
    )
    cors_origins: str = Field(
        default="*", alias="AGENTBOARD_CORS_ORIGINS",
        description="逗号分隔的 CORS 允许源;生产禁用 *",
    )

    # ---- 缓存 ----
    cache_ttl: int = Field(default=30, alias="AGENTBOARD_CACHE_TTL")
    stats_cache_ttl: int = Field(default=300, alias="AGENTBOARD_STATS_CACHE_TTL")

    # ---- MQ(pika/RabbitMQ) ----
    mq_url: str = Field(default="amqp://guest:guest@127.0.0.1:5672/", alias="AGENTBOARD_MQ_URL")
    mq_workflow_queue: str = Field(default="agentboard.workflow", alias="AGENTBOARD_MQ_WORKFLOW_QUEUE")
    mq_ticket_queue: str = Field(default="agentboard.ticket", alias="AGENTBOARD_MQ_TICKET_QUEUE")

    # ---- Worker / Agent 集成 ----
    worker_agent_cmd: str | None = Field(
        default=None, alias="AGENTBOARD_WORKER_AGENT_CMD",
        description="Worker 调 Agent CLI 命令模板,如 '\"<py>\" \"<invoker>\"'",
    )
    worker_heartbeat_interval: int = Field(default=20, alias="AGENTBOARD_WORKER_HEARTBEAT_INTERVAL")
    worker_claim_poll_interval: float = Field(default=2.0, alias="AGENTBOARD_WORKER_CLAIM_POLL_INTERVAL")

    # ---- COS 对象存储 ----
    cos_secret_id: str = Field(default="", alias="COS_SECRET_ID")
    cos_secret_key: str = Field(default="", alias="COS_SECRET_KEY")
    cos_bucket: str = Field(default="", alias="COS_BUCKET")
    cos_region: str = Field(default="", alias="COS_REGION")

    # ---- 可观测性 ----
    log_level: str = Field(default="INFO", alias="AGENTBOARD_LOG_LEVEL")
    log_json: bool = Field(default=False, alias="AGENTBOARD_LOG_JSON", description="True=JSON 结构化日志")
    metrics_enabled: bool = Field(default=True, alias="AGENTBOARD_METRICS_ENABLED")
    tracing_enabled: bool = Field(default=False, alias="AGENTBOARD_TRACING_ENABLED")
    otel_endpoint: str | None = Field(default=None, alias="AGENTBOARD_OTEL_ENDPOINT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()


# 全局便捷引用
settings = get_settings()
