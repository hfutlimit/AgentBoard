"""Tests for performance optimization (Epic 18)

Epic 18: API 性能优化 - 数据库索引与缓存
"""
import pytest
import time
from agentboard.cache import SimpleCache, get_cache


class TestSimpleCache:
    """缓存模块单元测试"""

    def test_set_and_get(self):
        cache = SimpleCache(default_ttl=60)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_nonexistent(self):
        cache = SimpleCache(default_ttl=60)
        assert cache.get("nonexistent") is None

    def test_expired_key(self):
        cache = SimpleCache(default_ttl=1)  # 1 second TTL
        cache.set("expire_key", "expire_value")
        time.sleep(1.1)
        assert cache.get("expire_key") is None

    def test_delete(self):
        cache = SimpleCache(default_ttl=60)
        cache.set("delete_key", "delete_value")
        assert cache.delete("delete_key") is True
        assert cache.get("delete_key") is None

    def test_clear(self):
        cache = SimpleCache(default_ttl=60)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()
        assert cache.size == 0

    def test_invalidate_prefix(self):
        cache = SimpleCache(default_ttl=60)
        cache.set("project:1:stats", "stats1")
        cache.set("project:2:stats", "stats2")
        cache.set("user:1:name", "name1")
        count = cache.invalidate_prefix("project:")
        assert count == 2
        assert cache.get("project:1:stats") is None
        assert cache.get("user:1:name") == "name1"

    def test_get_or_set(self):
        cache = SimpleCache(default_ttl=60)
        factory_called = [False]

        def factory():
            factory_called[0] = True
            return "computed_value"

        # First call: factory is invoked
        result1 = cache.get_or_set("factory_key", factory)
        assert result1 == "computed_value"
        assert factory_called[0] is True

        # Second call: cached value is returned
        factory_called[0] = False
        result2 = cache.get_or_set("factory_key", factory)
        assert result2 == "computed_value"
        assert factory_called[0] is False

    def test_cleanup(self):
        cache = SimpleCache(default_ttl=1)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        time.sleep(1.1)
        count = cache.cleanup()
        assert count == 2
        assert cache.size == 0

    def test_size(self):
        cache = SimpleCache(default_ttl=60)
        assert cache.size == 0
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        assert cache.size == 2
        cache.delete("key1")
        assert cache.size == 1


class TestGlobalCache:
    """全局缓存实例测试"""

    def test_get_cache(self):
        cache = get_cache()
        assert cache is not None
        assert isinstance(cache, SimpleCache)

    def test_global_cache_persistence(self):
        cache = get_cache()
        test_key = "__test_global_cache_key__"
        try:
            cache.set(test_key, "test_value")
            assert cache.get(test_key) == "test_value"
        finally:
            cache.delete(test_key)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
