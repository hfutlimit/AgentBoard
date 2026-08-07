# Tasks: 评审运营视图多数决投票进度提示（Epic 122 S4 M2）

## 状态

- [x] 后端 `service.get_review_stats` 增补 review_mode/review_quorum/votes（majority 遍历 pending 实体，复用 `_review_vote_counts`）
- [x] 前端 models.ts ReviewStats 扩展 review_mode/review_quorum/votes（可选向后兼容）
- [x] 前端 app.ts 纯函数 reviewModeLabel / reviewVotePct / reviewVoteReached
- [x] 前端 app.html 评审模式徽标 + 多数决投票进度区块
- [x] 前端 app.css 徽标/投票进度样式（含暗色）
- [x] 单测 tests/test_epic122_s4m2.py（8 passed）
- [x] 前端 vitest 3 用例
- [x] E2E tests/test_epic122_s4m2_e2e.py（全绿 0 报错）
- [x] 部署 docker restart api/web（未触碰 18001）
- [x] Task 1017 → in_review；Story 233 / Epic 122 状态同步

## 验收记录

- pytest `test_epic122_s4m2.py`：8 passed；
- 回归：s3m2+s3m3 组合 41 passed（三文件组合 s3m2+s3m3+s2m1 的 2 failed 为预存在批量 flaky —— HEAD stash 对照同失败，与本次无关）；
- vitest：全量通过（含新增 3 用例）；
- E2E：majority 徽标 + 投票进度 + 0 console/pageerror/js-css 404；
- docker api review-stats 实测：`review_mode: single | quorum: 3 | votes: []`（结构就位）。

## 硬约束

- 零既有 REST 契约破坏（纯新增字段）；零新增依赖；未触碰 18001/docker 端口。
