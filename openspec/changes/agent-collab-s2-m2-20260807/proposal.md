# Change Proposal：多 Agent 协作闭环 S2 M2（Task 评审闭环）

> ID: agent-collab-s2-m2-20260807 · Epic 122 / Story 231 / Task 1012

## 问题

S2 M1（Task 1010）交付了开发任务竞争认领（claim）与提交评审入口（submit-review），
任务进入 `in_review` 后即断链：没有 Task reviewer 的指派机制、没有 approve/reject
评审动作、worker 对 `task.ready_for_review` 只打日志（M2 预留）。开发任务的「认领 →
开发 → 提交评审 → 评审 → 完成/退回」闭环缺少最后一环。

## 目标

1. **Task 评审字段**：`tasks` 表加 `reviewer_id`（被指派评审人，FK users 可空）+ `review_round`
   （评审轮次护栏，默认 0），与 S1 Story 的评审列对齐。
2. **随机指派（CAS 幂等）**：`assign_task_reviewer` 仅对 `in_review` 任务生效；候选 =
   在线 reviewer Agent ∩ 项目成员 ∩ **≠ assignee**（评审人与作者隔离，文档 #51 要求）；
   条件 UPDATE `status=in_review AND reviewer_id IS NULL` → rowcount=1 恰一赢家；已指派幂等复用。
3. **评审投票（CAS）**：`review_task` 仅被指派 reviewer 可操作 `in_review` 任务；
   approve → `done`；reject → `review_round+1` 退回 `in_progress`（开发者修复后重新
   submit-review，评审人保留）；达 5 轮上限 → `blocked` 护栏；评审意见落 Task 评论（唯一载体）。
4. **事件闭环**：指派成功 → 定向 `review.requested`（entity_type=task）；approve →
   广播 `task.reviewed`；reject → 广播 `task.rejected`（ref_id=轮次）。
5. **worker 自动分配**：`task.ready_for_review` → 自动指派；轮询模式兜底扫描
   `in_review` 未指派 Task。
6. **MCP 工具**：`assign_task_reviewer` / `review_task` / `list_task_review_tasks`。

## 非目标

Task 评审统计报表、Webhook 事件透传、reject 后自动换 reviewer（保留同一评审人）、
Story 级 ready 前强制 Task 全 done 的依赖校验 —— 属切片 3 或后续迭代。

## 关键设计

- **与 S1 review_story 同构**：状态迁移/评论载体/round 护栏完全对齐，复用
  `_online_reviewer_candidates` 与 `MAX_REVIEW_ROUNDS`，行为可预期；
- **CAS 原子判定**：指派与评审都走条件 UPDATE + rowcount 检查，并发恰一赢家
  （与 Epic 118 claim 护栏同语义）；
- **reject 语义**：退回 `in_progress` 而非 `pending_review`——Task 状态机只有
  `in_review→in_progress/done`（无 pending_review 态），退回后开发者可继续修改并
  重新 `submit-review`，reviewer_id 保留 → 同一评审人继续评审（不换人）；
- **消息只带定位信息**：事件发布 best-effort，状态一律回查 DB（与 M2 总线设计一致）；
- **迁移纯增量**：tasks 加列不重建表、不动 CHECK（Task 状态机零污染），SQLite/MariaDB 双后端兼容。

## 验收

1. pytest `test_epic122_s2m2.py` 22 用例：指派幂等/非 in_review 拒绝/无在线 reviewer
   拒绝/排除 assignee/CAS 恰一赢家；评审 approve→done+评论、reject→in_progress+round+1、
   round 上限 blocked、非 reviewer/非 in_review/无 comment/非法 verdict 拒绝；
   worker 事件→指派 + 轮询兜底；MCP AST 注册；
2. 既有测试零回归（S2 M1 旧断言随 M2 实现更新：task.ready_for_review 现在触发 HTTP）；
3. 零新增第三方依赖；不触碰 18001。
