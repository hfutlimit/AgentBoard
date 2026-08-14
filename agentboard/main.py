"""Uvicorn 入口。

启动::

    uvicorn agentboard.main:app --host 0.0.0.0 --port 18000

或::

    python -m agentboard.main
"""
from __future__ import annotations

import uvicorn

from .config import settings


# 延迟 import,确保 settings 加载完再初始化 DB
def _build_app():
    from .core.api.app import create_app
    return create_app()


app = _build_app()


def main() -> None:
    uvicorn.run(
        "agentboard.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.debug,
        log_config=None,  # 走我们自己的结构化日志
    )


if __name__ == "__main__":
    main()
