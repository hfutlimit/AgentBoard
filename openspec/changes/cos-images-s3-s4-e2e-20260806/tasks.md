# Tasks: Epic 64 S3/S4 评论与描述图片渲染验证

## 任务清单

- [x] MCP 新建 Task 993（Story 63 S3 评论图片，highest）与 Task 994（Story 64 S4 描述图片，highest）
- [x] MCP 状态：Task 993/994 backlog → todo → in_progress
- [x] 编写 `tests/test_epic64_s3_s4_e2e.py`：S3/S4 全链路 DOM 断言（任务/Story/Epic 描述 + 评论区 + quick-view 抽屉 + 行内 UI 添加），31/31 PASS
- [x] `app.spec.ts` 新增 3 用例（评论场景/描述场景/空 alt 边界），单测 21 passed
- [x] 修复遗留 `tests/test_smoke.py`（`list_comments` keyword-only 兼容）
- [x] pytest 聚焦回归 50 passed + 核心回归 9 passed/9 skipped
- [x] OpenSpec change 三件套（proposal/design/tasks）
- [x] MCP 状态：Task 993/994 → in_review；Story 63/64 → in_review
- [x] git commit + push origin main；写 memory；删 autodev.lock

## 验收记录

- E2E：31/31 passed，0 console error / pageerror / js-css 加载失败
- 前端单测：21 passed（含新增 S3/S4 3 用例）
- pytest：50 + 9 passed（test_smoke 遗留已修复）
- 零前端/后端源码变更（仅测试 + 文档），无需构建部署
