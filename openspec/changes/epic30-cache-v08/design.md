# 设计：API 缓存强化

## 数据/配置
- `cache.py` 模块级常量 `API_CACHE_TTL = int(os.getenv("AGENTBOARD_CACHE_TTL", "30"))`。
- `_api_cache = SimpleCache(default_ttl=API_CACHE_TTL)` 在 import 时读取，因此**改环境变量需重启进程**才生效（与既有 `STATS_CACHE_TTL` 行为一致）。
- `api.py` 中 `_CACHE_TTL_STATS` / `_CACHE_TTL_LIST` 的默认值改为 `str(API_CACHE_TTL)`，实现"全局默认 + 端点可单独覆盖"的回退语义。

## 命中率统计（线程安全）
- `SimpleCache.__init__` 增加 `_hits` / `_misses`（整型）。
- 所有读写在既有 `self._lock`（RLock）内完成，`get()` 命中 `+1 hits`、未命中（含过期删除） `+1 misses`。
- `get_or_set()` 复用 `get()`，因此一次 miss + 一次 factory 调用只计 1 miss；命中只计 1 hit，不会重复计数。
- `stats()` 返回 `{"size","hits","misses","hit_rate","default_ttl"}`，`hit_rate = round(hits/(hits+misses),4)`（无请求时为 `0.0`）。
- `reset_stats()` 仅清零计数，不动已有缓存条目。

## 观测端点
- `GET /api/cache/stats`：直接 `return get_cache().stats()`。
- 鉴权交给 `require_business_auth` 中间件：
  - `AGENTBOARD_REQUIRE_AUTH=1` 时，GET 需具备 `api:read` 权限的 Bearer / `abk_` API Key；
  - 本地开放模式（默认）下公开可读。
- 端内不调用 `_current_user`，避免开放模式下无 token 时误报 401。

## 兼容性
- 既有 `invalidate_prefix` / `cleanup` / `clear` 行为不变。
- 不影响任何既有端点契约；`/api/projects/{pid}/stats` 仍按 `_CACHE_TTL_STATS`（现回退到全局默认）缓存。
