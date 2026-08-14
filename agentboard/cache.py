"""[FACADE] 旧 import 路径:``from agentboard.cache import ...``。

实际实现已迁至 ``agentboard.core.infrastructure.cache``,本文件仅为
向后兼容保留的薄壳 re-export。新代码请直接 import core.infrastructure.cache。
"""
from .core.infrastructure.cache import (  # noqa: F401
    CacheEntry,
    SimpleCache,
    STATS_CACHE_TTL,
    API_CACHE_TTL,
    get_cache,
)

__all__ = [
    "CacheEntry",
    "SimpleCache",
    "STATS_CACHE_TTL",
    "API_CACHE_TTL",
    "get_cache",
]
