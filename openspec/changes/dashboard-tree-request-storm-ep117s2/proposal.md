# Proposal — 首页整树加载请求风暴治理（Epic 117 S2）

epic: 117
story: 224
task: 996
status: in_review

## 背景

Epic 117 S1（Task 995）已交付 `GET /api/overview` 跨项目聚合端点与首页两阶段渲染：统计卡/图表由 overview 单请求驱动、首屏秒出。但 `loadDashboardFullTree` 仍在后台执行四级整树级联加载：

```
projects → (Promise.all) 所有 epics → (Promise.all) 所有 stories → (Promise.all) 所有 tasks
```

生产环境项目/Epic/Story 数量庞大（实测约 100 项目 / 数百 Story），其中 **Task 级加载对每个 Story 发一个 `/api/stories/{sid}/tasks` 请求**，是全树请求量最大的一级（数百请求瞬时并发）。而首页核心渲染（统计卡/图表）已由 overview 驱动，Task 级全量数据仅用于：

1. overview 失败时的图表/统计回退（低频场景）；
2. 跳转项目页/Story 页前的数据预热（各视图均有独立加载路径，非必需）。

## 方案

纯前端增量改动，零 REST/DB 契约变更，零新增依赖：

1. **overview 成功时跳过 Task 级加载**：`loadDashboardFullTree` 仅加载 Epics + Stories 层级（供计数/跳转预热），请求量从 `P+E+S` 降至 `P+E`；
2. **overview 失败时保留全量回退**：行为与现状完全一致（图表/统计依赖 tasks() 信号）；
3. **并发分片**：各级 `Promise.all` 全量并发改为并发受限的 `parallelMap(items, limit=6, fn)`，避免瞬时并发风暴；失败项跳过不中断整段（成功项保留），比现状「整体 try/catch 丢弃全部」更健壮。

## 影响面

- `frontend/src/app/app.ts`：`loadDashboardFullTree` 改造 + 新增 `parallelMap` 私有工具方法；
- `frontend/src/app/app.spec.ts`：新增单测用例；
- `tests/`：新增 Playwright E2E 验证脚本；
- 不修改 `models.py` / `api.py` / `mcp_server.py`；不触碰端口 18001。

## 验收

见 story 224 / task 996 的验收标准。
