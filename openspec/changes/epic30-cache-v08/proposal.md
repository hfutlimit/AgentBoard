# 变更提案：API 缓存强化（Epic 30 v0.8 等价实现）

## 背景
Epic 30（前端体验升级 v0.8）规划了两件 API 缓存增强工作：
- Task 801：扩展 API 缓存 TTL 配置（如 `AGENTBOARD_CACHE_TTL`）。
- Task 802：添加缓存命中率统计。

`agentboard/cache.py` 当前的 `SimpleCache` 已支持 TTL 与按前缀失效，但：
1. 全局默认 TTL 硬编码为 30s，无法通过环境变量统一调整；
2. 没有任何命中/未命中统计，缓存效果不可观测，无法调优。

## 目标
在不改动 `models.py` / 对外 REST 契约的前提下，对 `cache.py` 做增量增强，并提供一个观测端点。

## 非目标
- 不引入新框架 / 构建链。
- 不改动既有端点的请求/响应结构。
- 不为列表端点默认开启缓存（避免陈旧数据影响既有测试与交互）。

## 范围
- **Task 801（TTL 可配置）**：`cache.py` 新增 `API_CACHE_TTL = int(getenv("AGENTBOARD_CACHE_TTL","30"))`，作为 `SimpleCache` 默认 TTL；`api.py` 中 `_CACHE_TTL_STATS` / `_CACHE_TTL_LIST` 在未单独配置时回退到全局默认。
- **Task 802（命中率统计）**：`SimpleCache` 增加线程安全的 hit/miss 计数、`stats()` 与 `reset_stats()`；`api.py` 新增 `GET /api/cache/stats`（鉴权由 `require_business_auth` 中间件统一处理）。

## 影响
- 仅 `agentboard/cache.py` 与 `agentboard/api.py`。
- 新增端点 `/api/cache/stats`（GET，需 `api:read` 权限；本地开放模式下公开可读）。
- 新增可配置环境变量 `AGENTBOARD_CACHE_TTL`。

## 退出标准
- `AGENTBOARD_CACHE_TTL` 可改变全局默认 TTL。
- `/api/cache/stats` 返回 `size / hits / misses / hit_rate / default_ttl`。
- 单测覆盖 `SimpleCache` 计数与 `stats()`；端到端验证端点可访问且无控制台/资源错误。
