# Change Proposal：多 Agent 协作闭环 S1 M1（Agent 注册表 + Story 评审）

> ID: agent-collab-s1-m1-20260807 · Epic 122 / Story 230 / Task 1006

## 问题

AgentBoard 的协作仍是「单 Agent 对系统」：Story 创建后没有其他 Agent 评审，没有事件驱动的接力通知。Epic 122 将系统升级为多 Agent 工作流编排中枢，本 Change 为 S1 里程碑 M1（MVP 切片的数据与 API 基础）。

## 目标

1. **Agent 注册表**：外部 Agent 经 `agents` 表自报身份（agent_id 唯一键、roles/capabilities、绑定服务账号 user_id、在线态），支撑后续评审/开发分配。
2. **Story 评审状态机**：新增 `pending_review`（待评审）/ `ready`（评审通过可开发）两态 + `reviewer_id`/`review_round` 列，状态机受控迁移。
3. **REST API**：Agent 注册/心跳/注销/列表；Story 指派评审人（随机 + CAS 幂等）；评审投票（approve/reject + 评论，仅被指派 reviewer）。
4. **前端映射**：状态 chips 补齐 `pending_review`/`ready` 展示（纯增量，不污染 Task 状态机）。

## 非目标（后续切片）

MQ 事件总线泛化（WorkflowMessage/定向+广播拓扑）、MCP 工具（agent_register/review_story 等）、story.created 自动指派消费者 —— 属 S1 M2/M3。

## 关键设计

- **不动共用 `Status` 枚举**：`pending_review`/`ready` 独立为 `STORY_REVIEW_STATUSES`，Task/Epic 状态机零污染（`update_task` 走 `ALL_STATUSES` 校验天然拒绝）。
- **CAS 原子判定**：`assign_reviewer` 条件 UPDATE `status=backlog AND reviewer_id IS NULL` → rowcount=1 才成功；`review_story` 条件 UPDATE 匹配 `reviewer_id + pending_review`。并发恰一赢家。
- **评审意见唯一载体**：approve/reject 必须伴随评论（审计轨迹）；`review_round` 达 5 轮置 `blocked`（护栏，与 Proposal max_rounds 对齐）。
- **兼容**：Story 创建默认 `backlog`（评审流显式触发），Epic 96 转化链路零影响；`blocked` 一并纳入 Story CHECK 约束。

## 验收

1. pytest：注册幂等 / 候选过滤 / assign CAS / 非 reviewer 拒绝 / round 护栏 / Task 状态不污染（14 用例）；
2. 迁移 SQLite/MariaDB 双后端幂等（SQLite batch_alter_table 更新 CHECK）；
3. API 直调：register→heartbeat→assign-reviewer→review approve 全链路 + 权限 401/422；
4. 前端构建 + E2E 回归无报错；既有测试零回归。
