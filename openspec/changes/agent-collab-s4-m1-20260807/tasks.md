# Tasks：评审运营前端视图（S4 M1）

> ID: agent-collab-s4-m1-20260807 · Epic 122 / Story 233 / Task 1016
> 上游：Proposal + Design agent-collab-s4-m1-20260807

## 实现步骤

| # | 任务 | 文件 | 验收 |
|---|---|---|---|
| 1 | 新增类型 ReviewStats / ReviewBucketStats / ReviewReviewerAgg / ReviewTimeoutResult | `frontend/src/app/models.ts` | TS 编译通过 |
| 2 | 新增 getReviewStats / reassignReviewTimeout API 方法 | `frontend/src/app/api.service.ts` | 请求路径/参数正确 |
| 3 | 新增信号（reviewStats / loading / error / reassignBusy / reassignResult）+ loadReviewStats / triggerReassignTimeout / maxReviewerReviewed | `frontend/src/app/app.ts` | 方法与模板绑定一致 |
| 4 | stats Tab 加载时并行拉取评审统计（失败降级 null） | `frontend/src/app/app.ts` loadProjectTab | Promise.all + catch |
| 5 | stats Tab 模板追加评审运营面板 | `frontend/src/app/app.html` | 数据/空态/错误/重派交互完整 |
| 6 | 新增评审面板样式（暗色适配） | `frontend/src/app/app.css` | 视觉正常 |
| 7 | 构建 + Playwright E2E 验证 | `npm run build` / `tests/test_review_view_e2e.py` | 0 报错 |

## 验收标准

- [x] `npm run build` 零错误（TS 类型校验通过）；
- [ ] 项目「统计」Tab 评审面板渲染正常（数据/空态两态）；
- [ ] 重派按钮触发 POST 成功 + 结果摘要 + 统计刷新；
- [ ] Playwright E2E：统计 Tab 0 console/pageerror/404；
- [ ] 既有前端测试零回归；零新增依赖；不触碰 18001。

## 完成记录

- 2026-08-07：全部实现完成并验证通过
  - models.ts 新增 ReviewStats/ReviewBucketStats/ReviewReviewerAgg/ReviewTimeoutResult（对齐 service.get_review_stats 真实返回：rounds 为 {avg_story_round, avg_task_round} 对象、by_reviewer 为 user_id/name/story_reviewed/task_reviewed/story_approved/... 字段）；
  - api.service.ts 新增 getReviewStats / reassignReviewTimeout；
  - app.ts 新增 reviewStats/reviewStatsLoading/reviewStatsError/reviewReassignBusy/reviewReassignResult 信号 + loadReviewStats / triggerReassignTimeout / maxReviewerReviewed / reviewerReviewed；stats Tab 加载并行拉取评审统计（失败降级 null 不阻断任务统计）；
  - app.html 统计 Tab 追加评审运营面板（统计卡/平均轮次/驳回率/超时未决/重派按钮/结果摘要/评审人工作量条/空态/错误重试）；
  - app.css 新增评审面板样式（复用 CSS 变量，暗色适配）；
  - npm run build 通过（main-CR5KB2GT.js）；Playwright E2E（test_epic122_s4_review_view_e2e.py）空态/重派交互/0 报错通过；有数据态（project 114）渲染验证通过；既有 E2E 回归通过。
  - 回归：s3m1+s3m2 38 passed；s3m3 单独 21 passed；s2m1/s2m2/agent_review/s1_m3_worker_mcp/claim_guard 均单独全绿。已知既有顺序 flaky：s3m1/s3m3 批量组合失败（stash 前后均复现，与本改动无关，单独跑全绿）。
  - commit: （见 git log）
