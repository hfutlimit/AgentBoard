# Epic 78 · Story 106 — AgentSchedule 绑定松绑（项目/Agent 级 + 筛选）

**status**: in_review
**date**: 2026-08-04

## 问题

现状 `AgentSchedule` 只绑一个 `project_id` + cron 表达式，触发时 `scheduler._trigger_one`
创建 `AgentRun` 一律 `task_id=None`，而 `executor.build_run_context` 的 agent 名来自
环境变量 `AGENTBOARD_DEFAULT_AGENT`（默认 codex）——**schedule 无法表达「给谁、推什么活」**：

1. **没有 agent 维度**：一个 schedule 无法指定由哪个 Agent（codex / claude / workbuddy / qoder）执行，
   全部 fallback 到 env 默认值，多 Agent 场景无法区分。
2. **没有任务维度**：触发后 run 不绑定任何 task，执行器没有可执行的 payload——
   「从 backlog 给 Agent 推活」的闭环在数据层是断的。
3. **没有筛选维度**：无法表达「只推该项目 high 以上优先级的 bug」这类规则。

Story 106 把 schedule 从「固定任务的定时器」升级为「项目/Agent 级 + 可选筛选的任务推送规则」。

## 目标

1. `AgentSchedule` 新增字段：`agent`（指定执行 Agent）、`task_id`（兼容旧单任务语义）、
   `task_priority` / `task_type` / `epic_id`（可选筛选）。
2. 触发时：固定 `task_id` → 直接绑定；否则自动挑**下一个 eligible task**（
   `status ∈ (backlog, todo)`、按 `epic_id`/`task_type` 过滤、`task_priority` 为最低门槛，
   优先级降序 + id 升序）写入 `run.task_id`；无 eligible 则跳过本次触发。
3. `executor.build_run_context` 的 agent 改为**读 `schedule.agent`**（fallback env）。
4. 全程增量：新增列/参数，不破坏既有 REST/DB 契约；旧单任务 schedule 行为不变。

## 验收

- 新建「项目/Agent 级」schedule（agent + 筛选）到期触发时，run 自动绑定 backlog/todo 中
  最高优先级 eligible task；
- 旧单任务 schedule（`task_id` 固定）行为不变；
- 无 eligible task 时不创建空 run（幂等推进 next_run_at）；
- 全量回归通过，零既有契约破坏。
