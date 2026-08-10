"""Worker 业务 Handler 注册表（Epic 123 Step 2）。

Worker 主循环通过 ``HANDLERS`` 工厂构建全部域 Handler，按 ``name`` 路由。
新增业务域只需：新建 ``handlers/xxx.py`` 实现 Handler 协议 + 在此注册，
**不改 worker.py 主循环**。
"""
from __future__ import annotations

from typing import Any

import httpx

from .clarify import ClarifyHandler
from .story import StoryHandler
from .ticket import TicketHandler

__all__ = ["ClarifyHandler", "StoryHandler", "TicketHandler", "build_handlers"]


def build_handlers(client: httpx.Client, config: Any) -> dict[str, Any]:
    """构建全部域 Handler，返回 ``{name: handler}`` 路由表。"""
    return {
        h.name: h
        for h in (
            ClarifyHandler(client, config),
            TicketHandler(client, config),
            StoryHandler(client, config),
        )
    }
