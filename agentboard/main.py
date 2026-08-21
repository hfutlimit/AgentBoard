"""Uvicorn 入口。

启动::

    uvicorn agentboard.main:app --host 0.0.0.0 --port 18000

或::

    python -m agentboard.main
"""
from __future__ import annotations

import uvicorn

from .core.config import settings
from . import api  # 拿到 agentboard.api:app（9 阶段重构后的真入口）


# 兼容 "uvicorn agentboard.main:app" 启动方式
app = api.app


def main() -> None:
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
        log_config=None,  # 走我们自己的结构化日志
    )


if __name__ == "__main__":
    main()
