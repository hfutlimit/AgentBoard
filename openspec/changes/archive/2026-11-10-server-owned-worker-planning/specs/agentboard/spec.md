## ADDED Requirements

### Requirement: Server 拥有工作规划状态机

Server SHALL 从 Task/Proposal 业务状态现算每个已映射项目的下一单位工作
（类型/状态 → work kind、readiness 与人审门、讨论轮次奇偶归属、Proposal 轮次），
经 `work_key` 幂等物化 `worker_work` 行，并收尾全部 Task 已 done 的 Story。
规划 SHALL 在 outbox relay 之前运行，默认 5s 节流（`AGENTBOARD_WORKER_PLAN_SECONDS`
可调），失败只记日志不阻塞 relay。规划 SHALL NOT 选择具体 Agent——工作由共享队列
竞争消费。Worker SHALL 只做 presence 心跳、项目范围声明与消费执行。

#### Scenario: 重复规划不重复派活

- **GIVEN** 一个已映射项目存在可工作的 Task，planner 已物化一行 `worker_work`
- **WHEN** 业务状态未变，再跑一轮规划
- **THEN** 新建行计数为 0，队列中仍只有一行同 `work_key` 的工作

### Requirement: Worker 项目范围声明按成员资格收敛

`POST /api/worker-work/projects` SHALL 对列出的每个项目校验调用者的项目成员
资格后写入 `worker_project_mappings`（重复声明幂等，重新列出会重新启用）。
不再列出的映射 SHALL 被置 `enabled=False`，但禁用每个受影响项目前 SHALL 校验
调用者在该项目的成员资格——Worker 跨用户共享、无 owner，任何成员不得借自报
`worker_id` 缩减别的项目的工作范围；非成员项目的映射留给项目管理员经
`/api/projects/{pid}/workers` 管理。`project_ids` SHALL 允许空集，用于退掉
调用者有权管理的全部范围。

#### Scenario: 成员不能缩减别的项目的 Worker 范围

- **GIVEN** Worker W 同时映射项目 A（用户甲成员）与项目 B（用户乙成员）
- **WHEN** 乙调用该端点声明 W 只负责 B（或声明空集）
- **THEN** B 的映射按声明更新，A 的映射保持 `enabled=True`

### Requirement: 能力覆盖未知即 fail-open

规划 SHALL 用已映射 Worker 的在线 AgentInstance 能力与七种 work kind 的交集
决定是否建行：覆盖未知（无映射或能力数据不可读，含 malformed JSON）SHALL 不过滤；
有映射但无在线实例 SHALL 不建行。`AgentInstance.agent_id` SHALL 按逻辑串
（`Agent.agent_id`）关联，不得混用整数主键。

#### Scenario: 脏能力数据不停摆规划

- **GIVEN** 某在线 Agent 的 capabilities 是非法 JSON
- **WHEN** planner 计算该项目覆盖
- **THEN** 覆盖视为未知，规划照常建行，不抛异常

### Requirement: 被丢弃消息的可用工作 SHALL 被重发

`available` 且已发布（`published_at` 非空）超过 3 分钟 grace 的行，只要工作仍可
offer 且覆盖恢复，planner SHALL 重置其 `published_at` 让 relay 重发同一行
（同 `work_key`，不建新行）。`leased` 行 SHALL NOT 被 planner 触碰（归 relay
的租约过期路径）。该不变量覆盖 ack-and-drop、ack 后崩溃等消息消失场景。

#### Scenario: ack-drop 的工作在 Agent 恢复后继续

- **GIVEN** 一行已发布的可用工作，其消息被无合格 Agent 的 Worker ack-drop
- **WHEN** 能 serve 该 kind 的 Agent 重新在线，grace 已过，planner 再跑一轮
- **THEN** 该行 `published_at` 被重置，relay 重新发布，不产生重复行

### Requirement: serverplan 协议门禁与滚动升级

Server SHALL 在 `GET /api/worker-work/snapshot` 返回协议串
`worker-work.serverplan.v1`；新 Worker 读不到该值 SHALL 拒绝启动。
`POST /offers` 与 `POST /stories/{id}/complete` SHALL 保留，并与 planner 共享
同一套 `materialize_offer` 幂等物化，混合集群不重复派活。

#### Scenario: 旧 Worker 与新 Server 混合不重复派活

- **GIVEN** 新 Server 的 planner 已物化某工作行
- **WHEN** 旧 Worker 扫描同一业务对象并 POST `/offers`
- **THEN** 命中同一 `work_key`，返回既有行，不新建

### Requirement: Server 补齐默认工作计划

Proposal converge 且允许自动建单但未给 `ticket_plan` 时，Server SHALL 补齐
规范 design → dev → QA 三任务 DAG。QA 缺陷 confirm 未给 `qa_followup` 时，
Server SHALL 从不可变 QA 证据构造 bug + 独立复测计划。Worker 显式传入的计划
SHALL 继续接受，但必须与不可变证据逐字匹配，否则 422。

#### Scenario: confirm 不带 followup 由 Server 建 bug 与复测

- **GIVEN** 一轮失败 QA 的证据含已确认缺陷，讨论进入 confirm
- **WHEN** Worker 提交 confirm 但不带 `qa_followup`
- **THEN** Server 创建缺陷 bug Task 与依赖其的复测 QA Task，原 QA 证据不变