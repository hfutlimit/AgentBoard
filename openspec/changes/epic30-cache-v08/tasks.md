# 任务清单：API 缓存强化（Epic 30 v0.8）

## Task 801 — 扩展 API 缓存 TTL 配置
- [x] `cache.py`：`API_CACHE_TTL = int(getenv("AGENTBOARD_CACHE_TTL","30"))`，作为 `SimpleCache` 默认 TTL。
- [x] `api.py`：`_CACHE_TTL_STATS` / `_CACHE_TTL_LIST` 默认值改为 `str(API_CACHE_TTL)`，回退到全局默认。
- [x] 单元验证：monkeypatch 环境变量 → reload → `API_CACHE_TTL` / `get_cache().default_ttl` 跟随变化。

## Task 802 — 添加缓存命中率统计
- [x] `cache.py`：`SimpleCache` 增加 `_hits` / `_misses` 计数（`get()` 内、锁内递增）、`stats()`、`reset_stats()`。
- [x] `api.py`：新增 `GET /api/cache/stats`，返回 `size/hits/misses/hit_rate/default_ttl`。
- [x] 单元验证：`SimpleCache` 命中/未命中计数、过期计为未命中、`get_or_set` 只计一次、`hit_rate` 计算。
- [x] 集成/端到端：运行中的本地 API（58125）`/api/cache/stats` 返回结构正确；Playwright 登录后跨域 fetch 该端点，无 page/console/`.js`/`.css` 错误。

## 验证结论
- `pytest tests/test_epic30_cache.py`：8 passed（含运行 API 探针）。
- `tests/test_epic30_cache_e2e.py`：登录 → 进入 project → 浏览器 fetch `/api/cache/stats` → 断言结构 + 零错误。
- 核心端点 `/api/meta`、`/api/projects`、`/api/projects/{pid}/stats`、`/api/cache/stats` 均返回 200。
