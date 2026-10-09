# Design — Server-Owned Worker Planning

## 决策

**Server 拥有状态机，Worker 只执行。** 具体边界：

| 关注点 | 归属 | 位置 |
|---|---|---|
| 「哪条业务对象该干活、什么 kind、第几轮」 | Server | `worker_planner.offer_for` |
| 「讨论轮到谁、target_agent 是谁」 | Server | `worker_planner.offer_for`（偶数轮=作者，奇数轮=评审） |
| 「Story 何时收尾」 | Server | `worker_planner.complete_finished_stories` |
| 「收敛但没给 DAG 时的默认计划」 | Server | `worker_work.default_ticket_plan` |
| 「确认 QA 缺陷后的 bug + 复测任务」 | Server | `worker_work.build_qa_followup` |
| 租约 / 认领 / 完成 / 失败的事实 | Server | `worker_work.py`（未变） |
| 跑哪个 CLI、怎么起、工作目录 | Worker | `WorkerOwnedService` + adapters（未变） |
| 「谁抢到这条消息」 | 共享队列竞争消费 | `agentboard.work.v2.project.*`（未变） |

## 不变量（刻意保持）

1. **不做 Agent 选择。** 规划只决定「有什么活」，不决定「谁干」。与 `.NET DatabaseAgentSelector`
   那条「选一个具体 Agent」的 durable 路径保持正交。
2. **业务状态是唯一事实。** planner 每轮从 Task/Proposal 现算，offer 仍经 `check_offer`
   复核，因此过期的规划不可能改动已移动的对象。
3. **幂等靠 `work_key`。** `materialize_offer` 把业务指纹折进 `work_key`；planner 与
   仍在 POST `/offers` 的旧 Worker 会收敛到同一行，混合集群不重复派活。
   `created` 返回值让 `plan_once` 的返回计数也只统计真正新建的行（否则第二轮会虚报非 0）。

## 触发与节流

`plan_once()` 由 `outbox_publisher._drain_once` 在 relay 之前调用。outbox tick 是亚秒级，
而规划是一次全量业务扫描（含每 task 的 readiness 计算），因此 planner 自带节流，
默认 5s —— 与原来 Worker 的 `ReconcileSeconds` 对齐，延迟不回退。失败只记日志、不阻塞
relay 与 legacy outbox。`force=True` 供测试绕过节流。

## 项目范围从哪来

Server 无法凭空知道一台 Worker 想干哪些项目。所以新增
`POST /api/worker-work/projects`，用与 `/offers` 相同的成员资格校验，把项目写进既有
`worker_project_mappings`（T6.3 表，无需迁移）。Worker 在 `PresenceLoop` 每个 presence
周期幂等重传一次，可自愈 Server 重启期间的失败投递。

## 能力覆盖（fail-open）

`covered_kinds(project)` = 该项目下已映射、且 `worker_project_mappings` 关联的
**在线** AgentInstance 所声明的、与七种 work kind 相交的能力集合。

- 返回 `None`（无映射 / 能力数据不可读）→ **不过滤**，宁可多建行也不让工作停摆；
- 返回 `set()`（有映射但当前没有可达实例）→ 不建行，避免为离线 Worker 堆队列；
- malformed capability JSON → `None`，规划永不因脏数据中断。

> 注意 `AgentInstance.agent_id` 是逻辑串（`Agent.agent_id`），不是 `Agent.id` 整数主键 ——
> 混用会静默查不到任何行，从而把覆盖集合算成空集（该 bug 已被 planner 单测捕获）。

## 滚动升级

- 协议串 `worker-work.serverplan.v1`：新 Worker 拒绝连接旧 Server；新 Server 的
  `/offers` 仍在，因此旧 Worker 不会因为「无法 offer」而空转（它只是重复了 Server 的工作，
  而 `work_key` 幂等把它消掉）。
- `qa_followup` / `ticket_plan` 的**显式传入仍然接受**，只是不再必需 —— 这样旧 Worker
  的输出不会被 422 拒绝。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| planner 每轮全量扫 Task，项目大时变慢 | 5s 节流；只扫已声明映射的项目；后续可加增量游标 |
| 覆盖误判导致工作停摆 | 覆盖未知即 fail-open；跳过只记 INFO 日志 |
| 新旧 Worker 同时规划 | `work_key` 幂等 + 协议串门禁 |
| 规划写入与 relay 抢同一 session | planner 用独立 `SessionLocal()`，先 commit 再 relay |
