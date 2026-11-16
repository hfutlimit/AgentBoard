## MODIFIED Requirements

### Requirement: Worker 项目范围声明按成员资格收敛

`POST /api/worker-work/projects` SHALL 在读取声明内容之前先校验调用者凭据
（缺失或无效 → 401）与 worker-owned 执行开关（关闭 → 409），因此空集声明
不构成未鉴权探针。凭据 SHALL 每请求只解析一次，并复用于后续全部成员资格
校验，不得随项目数量重复解析。

该端点 SHALL 对列出的每个项目校验调用者的项目成员资格后写入
`worker_project_mappings`（重复声明幂等，重新列出会重新启用）。不再列出的
映射 SHALL 被置 `enabled=False`，但禁用每个受影响项目前 SHALL 校验调用者在
该项目的成员资格——Worker 跨用户共享、无 owner，任何成员不得借自报
`worker_id` 缩减别的项目的工作范围；非成员项目的映射留给项目管理员经
`/api/projects/{pid}/workers` 管理。`project_ids` SHALL 允许空集，用于退掉
调用者有权管理的全部范围。

#### Scenario: 成员不能缩减别的项目的 Worker 范围

- **GIVEN** Worker W 同时映射项目 A（用户甲成员）与项目 B（用户乙成员）
- **WHEN** 乙调用该端点声明 W 只负责 B（或声明空集）
- **THEN** B 的映射按声明更新，A 的映射保持 `enabled=True`

#### Scenario: 空集声明仍需鉴权

- **GIVEN** worker-owned 执行已启用，调用者未携带有效凭据
- **WHEN** 调用该端点声明 `project_ids: []`
- **THEN** 返回 401，且不改动任何映射

#### Scenario: 执行平面关闭时拒绝声明

- **GIVEN** `AGENTBOARD_WORKER_OWNED_ENABLED=0`，调用者携带有效凭据
- **WHEN** 调用该端点声明任意项目范围
- **THEN** 返回 409，且不改动任何映射

## ADDED Requirements

### Requirement: Stale run 回收上报与实际回收一致

`scan_stale_agent_runs` 返回的 `taken_over` SHALL 只包含本次真正被回收的 run。
两条回收路径——有 task 的 `soft_takeover_run` 与无 task 的 schedule-level run
——SHALL 共用同一 CAS 不变量：仅当 run 仍为 `status='running'` 且
`is_stale=true` 时才置 `failed`，并以 `rowcount` 为唯一事实来源。心跳在扫描
SELECT 与回收写入之间清掉 `is_stale`（或别的写者已推进该 run）时 CAS SHALL
落空，该 run 保持原状且 SHALL NOT 出现在 `taken_over` 中。

#### Scenario: 心跳恢复的 run 不被上报为已回收

- **GIVEN** 一个 stale 且进度早于回收阈值的 running run
- **WHEN** 心跳在扫描读取之后、回收写入之前清掉 `is_stale`，随后扫描执行回收
- **THEN** 该 run 仍为 running，且 `taken_over` 不包含它

#### Scenario: 无 task 的 schedule-level run 同受 CAS 保护

- **GIVEN** 一个 `task_id` 为空、stale 且进度早于回收阈值的 running run
- **WHEN** 心跳在扫描读取之后清掉 `is_stale`，随后扫描执行回收
- **THEN** 该 run 仍为 running，且 `taken_over` 不包含它

#### Scenario: 真正停滞的无 task run 仍被回收并上报

- **GIVEN** 一个 `task_id` 为空、stale 且进度早于回收阈值的 running run，期间无心跳恢复
- **WHEN** 扫描执行回收
- **THEN** 该 run 置 `failed`、`error_message` 为 `stale_run_no_task`、`finished_at` 非空，并计入 `taken_over`