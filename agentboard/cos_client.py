"""[FACADE] 旧 import 路径:``from agentboard.cos_client import ...``。

实际实现已迁至 ``agentboard.core.infrastructure.cos_client``,本文件仅为
向后兼容保留的薄壳 re-export。新代码请直接 import core.infrastructure.cos_client。
"""
from .core.infrastructure.cos_client import (  # noqa: F401
    CosClient,
    CosError,
    client,
    ENV_KEYS,
)

__all__ = [
    "CosClient",
    "CosError",
    "client",
    "ENV_KEYS",
]
