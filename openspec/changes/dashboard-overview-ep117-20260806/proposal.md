# Proposal — Dashboard 首页加载性能优化（跨项目聚合统计端点）

status: in_review
epic: 117 (Dashboard 首页加载性能优化)
story: 223
task: 995

## 问题

首页 Dashboard 的 `loadDashboard()` 采用**四级整树预加载**：

```
projects() → 每项目 listEpics → 每 Epic listStories → 每 Story listTasks
```

三级 `Promise.all` 但逐级串行级联：必须等全部 Epic 返回才能发 Story 请求，等全部 Story
返回才能发 Task 请求。项目 / Epic / Story 数量增长后，请求总量 = N + M + K（生产远程环境
实测数百个请求），首页骨架屏等待时间显著拉长（E2E 曾需 `wait_for_function(..., timeout=60000)`
才能等到首页骨架屏消失），是长期记忆里明确记录的**已知性能债**。

## 目标

1. 后端新增跨项目聚合端点 `GET /api/overview`：一次返回首页全部统计
   （Epic/Story/Task 总数、任务状态分布、近 7 日活动、各项目交付进度）。
2. 前端首页改为**两阶段渲染**：
   - 阶段一：overview 单请求驱动统计卡与图表秒出，骨架屏提前收尾；
   - 阶段二：后台异步整树加载，填充 `epics/stories/tasks` 全局信号
     （搜索 / 看板 / 任务列表等既有功能依赖这些信号，**契约不变**）。
3. 权限：admin 可见全部项目；普通用户仅成员项目；未登录空统计。

## 约束

- 增量式：不新增数据库表 / 不修改现有 REST 契约 / 零新增依赖 / 双后端（SQLite/MariaDB）兼容。
- overview 失败不阻断：前端静默回退到既有整树逻辑。
- 不动 MCP 18001 端口、不动 docker 编排。

## 验收

- `GET /api/overview` 返回结构稳定：`counts / projects / status_distribution / activity_7d`。
- 首页首屏不再等待整树加载完成；统计卡数据与整树口径一致。
- 回归：搜索 / 看板 / 任务列表 / Story 视图不受影响；前端单测与 pytest 全绿。
