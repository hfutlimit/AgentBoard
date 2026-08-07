# Proposal：多数决评审（S3 M3）

> ID: agent-collab-s3-m3-20260807 · Epic 122 / Story 232 / Task 1015
> 上游：文档 #50「多 Agent 自动协作闭环：需求与方案」§7 决策 #7、§8 切片 3

## 1. 问题

Epic 122 已交付的评审闭环（S1 Story 评审 / S2 Task 评审）均为**单评审人**：
`review_story` / `review_task` 仅被指派 reviewer（`reviewer_id` 匹配）可操作，
approve 即通过。文档 #50 §7 决策 #7 明确将「评审强度升级：**N 人多数决**」留待
切片 3 —— 单评审人存在误判/合谋/离线阻塞风险，评审质量依赖单一 Agent 判断。

## 2. 目标

把评审强度从「1 名 reviewer approve 即通过」升级为「**N 人投票，达法定票数按多数决结算**」：

1. **投票表**：一实体（Story/Task）多评审人各一票，一人一票（upsert 改票）；
2. **多数决结算**：approve > reject → 通过（Story→ready / Task→done）；
   reject >= approve（含平局保守驳回）→ 驳回（round+1，回原评审流/开发流）；
3. **超时兜底**：票数未达法定票数但已超时 → 按现有票结算（防死锁）；
4. **模式开关**：环境变量 `AGENTBOARD_REVIEW_MODE`（`single` 默认 / `majority`），
   默认 single 保持既有行为零回归；`AGENTBOARD_REVIEW_QUORUM`（默认 3）设法定票数；
5. **事件语义**：投票未达法定票数 → 广播 `review.vote_cast`（新事件，进白名单），
   结算成功沿用既有 `story.ready` / `task.reviewed` / `review.rejected` / `task.rejected`。

## 3. 约束

- 默认 `single` 模式行为**逐字节不变**（既有 S1/S2 测试零回归是硬验收）；
- 零新增第三方依赖；迁移 `q6r7s8t9u0v1`（review_votes 表）双后端兼容；
- 不触碰 18001；MCP 工具 review_story / review_task 语义升级，签名不变。

## 4. 验收

- 单测：多数决通过/驳回/未达 quorum 不结算/一人一票改票/平局保守驳回/
  超时兜底结算（防死锁）/single 模式兼容/权限（非 reviewer 候选拒绝）；
- api 事件断言：投票未结算 → vote_cast；结算 approve → ready/reviewed；
  结算 reject → rejected；
- 既有 epic122 全系测试零回归；Epic 97 AST 护栏零违规。
