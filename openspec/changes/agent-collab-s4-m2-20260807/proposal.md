# Proposal: 评审运营视图多数决投票进度提示（Epic 122 S4 M2）

## 背景

Epic 122 S4 M1（Task 1016）已交付评审运营前端视图：统计卡（Story/Task approved/rejected/pending/blocked、平均轮次、驳回率）、超时未决数、超时重派按钮、评审人工作量条。但 Story 233 交付范围明确包含「**多数决模式下投票进度提示**」，S4 M1 未覆盖：

- 后端 `GET /api/review-stats` 返回结构不包含任何多数决信息（review_mode / review_quorum / 已投票数）；
- majority 模式下，评审运营面板无法感知「某个 pending Story/Task 还差几票才能结算」。

## 目标

1. 后端 `get_review_stats` 增补三个字段：`review_mode`（single|majority）、`review_quorum`（法定票数）、`votes`（pending 实体的投票进度列表）；
2. 前端评审运营面板：评审模式徽标 + 多数决投票进度区块（标题/通过票/驳回票/已投·法定票数/进度条/达法定票高亮）；
3. 零契约破坏：single 模式行为逐字节不变（votes 恒为空数组，新字段均为纯增量）。

## 方案要点

- 复用既有 `get_review_mode()` / `get_review_quorum()`（env 驱动）与 `_review_vote_counts()`；
- majority 模式下遍历全部 pending 实体（pending_review Story / in_review Task），统计 approve/reject 票数；
- 前端 `reviewModeLabel()` / `reviewVotePct()` / `reviewVoteReached()` 纯函数，模板复用既有 `.badge` / 进度条样式体系。

## 验收

- 单测 8 passed（结构/计数/single 兼容/API 透传）；
- E2E 全绿（majority 徽标 + 投票进度 + 0 报错）；
- 既有回归零失败；零新增依赖；不触碰 18001。
