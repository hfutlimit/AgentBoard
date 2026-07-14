"""Simple in-memory cache for API responses.

Epic 18: API 性能优化 - 缓存机制
Epic 23 Story 23.1: 统计端点缓存强化
提供基于 TTL 的简单缓存，减少重复数据库查询
"""
import os
import threading
import time
from typing import Any, Callable, Hashable

# TTL 配置（秒），可通过环境变量覆盖
STATS_CACHE_TTL = int(os.getenv("AGENTBOARD_STATS_CACHE_TTL", "300"))  # 默认 5 分钟


class CacheEntry:
    """缓存条目，包含值和过期时间"""

    __slots__ = ('value', 'expires_at')

    def __init__(self, value: Any, ttl: int):
        self.value = value
        self.expires_at = time.time() + ttl


class SimpleCache:
    """简单的线程安全内存缓存，支持 TTL"""

    def __init__(self, default_ttl: int = 60):
        """
        Args:
            default_ttl: 默认过期时间（秒）
        """
        self._cache: dict[Hashable, CacheEntry] = {}
        self._lock = threading.RLock()
        self.default_ttl = default_ttl

    def get(self, key: Hashable) -> Any | None:
        """获取缓存值，如果过期或不存在返回 None"""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if time.time() > entry.expires_at:
                del self._cache[key]
                return None
            return entry.value

    def set(self, key: Hashable, value: Any, ttl: int | None = None) -> None:
        """设置缓存值"""
        with self._lock:
            self._cache[key] = CacheEntry(value, ttl or self.default_ttl)

    def delete(self, key: Hashable) -> bool:
        """删除缓存值"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()

    def invalidate_prefix(self, prefix: str) -> int:
        """删除所有以 prefix 开头的缓存键（用于批量失效）"""
        with self._lock:
            keys_to_delete = [k for k in self._cache if isinstance(k, str) and k.startswith(prefix)]
            for key in keys_to_delete:
                del self._cache[key]
            return len(keys_to_delete)

    def cleanup(self) -> int:
        """清理所有过期的缓存条目"""
        with self._lock:
            now = time.time()
            keys_to_delete = [k for k, v in self._cache.items() if now > v.expires_at]
            for key in keys_to_delete:
                del self._cache[key]
            return len(keys_to_delete)

    def get_or_set(self, key: Hashable, factory: Callable[[], Any], ttl: int | None = None) -> Any:
        """获取缓存值，如果不存在则调用 factory 并缓存结果"""
        value = self.get(key)
        if value is not None:
            return value
        value = factory()
        self.set(key, value, ttl)
        return value

    @property
    def size(self) -> int:
        """返回当前缓存条目数量"""
        with self._lock:
            return len(self._cache)


# 全局缓存实例
# 可以在应用启动时配置 TTL
_api_cache = SimpleCache(default_ttl=30)  # 默认 30 秒


def get_cache() -> SimpleCache:
    """获取全局 API 缓存实例"""
    return _api_cache
