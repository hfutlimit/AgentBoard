"""Epic 30 (前端体验升级 v0.8) — API 缓存强化 单元测试 + 集成探针。

Task 801: 全局默认 TTL 可通过 AGENTBOARD_CACHE_TTL 配置（各端点可单独覆盖）
Task 802: SimpleCache 线程安全命中率统计 + GET /api/cache/stats 端点

- 单元测试：SimpleCache 命中/未命中计数、hit_rate、reset、可配置 default_ttl。
- 集成探针：对运行中的本地 API (127.0.0.1:58125) 调用 /api/cache/stats，断言返回结构。
"""
import importlib
import os
import sys
import urllib.request
import json

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentboard import cache as cache_mod


# ---------------- Task 802: SimpleCache 命中率统计（纯单元） ----------------

def test_simple_cache_hit_and_miss_counting():
    c = cache_mod.SimpleCache(default_ttl=60)
    # 未命中
    assert c.get("missing") is None
    # 写入后命中
    c.set("k", "v")
    assert c.get("k") == "v"
    # 再未命中（不存在的 key）
    assert c.get("nope") is None

    stats = c.stats()
    assert stats["hits"] == 1, stats
    assert stats["misses"] == 2, stats
    assert stats["size"] == 1, stats
    # hit_rate = 1 / (1+2)
    assert abs(stats["hit_rate"] - round(1 / 3, 4)) < 1e-9, stats


def test_simple_cache_hit_rate_zero_when_no_requests():
    c = cache_mod.SimpleCache(default_ttl=60)
    stats = c.stats()
    assert stats["hits"] == 0
    assert stats["misses"] == 0
    assert stats["hit_rate"] == 0.0


def test_simple_cache_reset_stats():
    c = cache_mod.SimpleCache(default_ttl=60)
    c.set("k", "v")
    c.get("k")        # hit
    c.get("x")        # miss
    assert c.stats()["hits"] == 1
    c.reset_stats()
    s = c.stats()
    assert s["hits"] == 0 and s["misses"] == 0 and s["size"] == 1, s


def test_simple_cache_expired_entry_counts_as_miss():
    c = cache_mod.SimpleCache(default_ttl=1)  # TTL=1s
    c.set("e", "v", ttl=1)
    import time
    time.sleep(1.1)
    assert c.get("e") is None  # 过期 -> 未命中
    assert c.stats()["misses"] == 1
    assert c.stats()["hits"] == 0


def test_get_or_set_counts_once_per_call():
    c = cache_mod.SimpleCache(default_ttl=60)
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return "value"

    # 第一次：未命中 + 计算 + 缓存
    assert c.get_or_set("g", factory) == "value"
    # 第二次：命中（直接取缓存，不再调用 factory）
    assert c.get_or_set("g", factory) == "value"
    assert calls["n"] == 1, "factory should be called only once"
    stats = c.stats()
    assert stats["misses"] == 1 and stats["hits"] == 1, stats


# ---------------- Task 801: 全局默认 TTL 可配置 ----------------

def test_global_ttl_reads_env(monkeypatch):
    monkeypatch.setenv("AGENTBOARD_CACHE_TTL", "45")
    reloaded = importlib.reload(cache_mod)
    try:
        assert reloaded.API_CACHE_TTL == 45, reloaded.API_CACHE_TTL
        assert reloaded.get_cache().default_ttl == 45, reloaded.get_cache().default_ttl
    finally:
        # 还原，避免影响后续用例
        monkeypatch.delenv("AGENTBOARD_CACHE_TTL", raising=False)
        importlib.reload(cache_mod)


def test_default_ttl_defaults_to_30_when_unset(monkeypatch):
    monkeypatch.delenv("AGENTBOARD_CACHE_TTL", raising=False)
    reloaded = importlib.reload(cache_mod)
    try:
        assert reloaded.API_CACHE_TTL == 30
    finally:
        importlib.reload(cache_mod)


# ---------------- 集成探针：运行中的本地 API ----------------

LIVE_URL = "http://127.0.0.1:58125/api/cache/stats"
EXPECTED_KEYS = {"size", "hits", "misses", "hit_rate", "default_ttl"}


def _live_stats():
    req = urllib.request.Request(LIVE_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_live_cache_stats_endpoint_shape():
    try:
        data = _live_stats()
    except Exception as exc:  # 服务未启动则跳过，不视为失败
        pytest.skip(f"local API not reachable ({exc}); start it on :58125 to run this probe")

    assert isinstance(data, dict), data
    assert EXPECTED_KEYS.issubset(data.keys()), f"missing keys: {EXPECTED_KEYS - set(data.keys())}"
    assert isinstance(data["default_ttl"], int) and data["default_ttl"] > 0
    assert 0.0 <= data["hit_rate"] <= 1.0
    assert data["size"] >= 0
    print("[OK] /api/cache/stats ->", data)
