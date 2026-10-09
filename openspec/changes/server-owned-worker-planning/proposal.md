# Server-Owned Worker Planning

**status**: in_progress
**date**: 2026-10-09

## 为什么做

Worker-owned 执行里「下一个工作是什么」原本由 **Worker** 决定：
`WorkerOwnedService.ReconcileLoop` 每 5 秒拉 `/api/worker-work/snapshot`，在本地跑
`WorkPlanner.Next()` 算出 offer，再 POST 回 Server 校验。结果是业务状态机（哪些
Task/Proposal 处于「该干活」状态、讨论轮到谁、Story 何时收尾）散落在每台 Worker 的
C# 里，Server 只当租约登记处：

- 规则（`WorkPlanner.Next` / `TicketPlan` / `AddQaFollowup`）在 .NET 侧，改一次要发 Worker；
- Server 无法回答「这个项目现在有多少待办」，也无法按事件推进；
- Worker 越权做规划，与「Server 是唯一事实表」的定位冲突。

## 改什么

把状态机收归 Server，Worker 退化为纯执行器。

1. **新增 `features/scheduling/worker_planner.py`**：按项目枚举 next work（移植
   `WorkPlanner.Next` 的类型/状态/就绪/人审门语义 + 讨论轮次归属 + Story 收尾），
   经既有 `materialize_offer` 幂等物化 `worker_work` 行。
2. **新增 `POST /api/worker-work/projects`**：Worker 启动时声明负责的项目，写入
   `worker_project_mappings`；Server 只对这些项目规划，避免为无人认领的项目堆队列行。
   不再列出的项目会被置 `enabled=False`，本地配置变更真的能停掉 Server 侧规划。
3. **规划接进 outbox tick**（`outbox_publisher._drain_once` 先 plan 后 relay），
   内部 5s 节流，可用 `AGENTBOARD_WORKER_PLAN_SECONDS` 调整。
4. **`default_ticket_plan` / `build_qa_followup` 移到 Server**：完成时补齐，Worker
   不再构造待办工作。Worker 传的 plan 仍接受，但必须与不可变的证据逐字匹配。
5. **协议串 bump 到 `worker-work.serverplan.v1`**：老 Worker 启动即拒绝，避免新旧
   双方同时规划。
6. **Worker 侧**：删除 `ReconcileLoop` 的扫描 / offer / Story 收尾，只保留 presence
   心跳 + 项目映射 + 消费执行；`WorkPlanner` 仅剩 prompt 组装与结果形状校验。

## 影响范围

`src/backend-fastapi/agentboard/features/scheduling/*`、
`src/backend-fastapi/agentboard/core/infrastructure/outbox_publisher.py`、
`src/nodes/AgentBoard.Node/WorkerOwned/*`，以及对应的 pytest / .NET 用例。

**保留** `POST /offers` 与 `POST /stories/{id}/complete`：滚动升级期旧 Worker 仍可用，
两者与 planner 共享同一套幂等物化，混合集群不会重复派活。

## 不做

- 不改 `worker_work` 表结构（复用 `workers` / `worker_project_mappings` / `agents` /
  `agent_instances`）。
- 不引入 Server 端 Agent **选择**：规划决定「有什么活」，不决定「谁干」，仍由共享队列竞争消费。
- 不清理生产库中历史 `executor_type='minimax'` 行（已确认「已完成的不用清理」）。
