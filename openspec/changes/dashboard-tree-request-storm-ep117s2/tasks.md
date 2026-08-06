# Tasks — 首页整树加载请求风暴治理（Epic 117 S2）

epic: 117
story: 224
task: 996

## 任务清单

- [x] OpenSpec 变更文档（proposal / design / tasks）
- [x] `app.ts` 新增 `parallelMap` 并发受限工具（limit=6，单项失败跳过）
- [x] `app.ts` `loadDashboardFullTree` 改造：overview 成功跳过 Task 级；各级改分片并发
- [x] `app.spec.ts` 单测：overview 成功 listTasks 零调用 / overview 失败回退 / 并发上限
- [x] 前端构建 + 全量单测通过
- [x] Playwright E2E：首页秒出 + 统计卡正确 + tasks 请求数 0 + 项目/Story 页回归 + 0 报错
- [x] 回归：后端聚焦 pytest + 前端既有单测
- [x] 部署（cp dist → static + docker restart web）+ git commit/push + MCP 状态流转 in_review
