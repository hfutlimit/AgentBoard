# Change: Worker-owned 本机任务记录页面

## 背景与范围裁定

Worker-owned 配置台已能维护本机 Agent、保存配置并控制本机 Worker 的启动与排空停止，但操作者无法查看某个本机 Agent 的实际执行、交付确认或重启中断。旧 `ExecutionStore`/`/api/executions` 属于另一套执行模型；`WorkJournal` 是含 fenced claim token 与原始结果的运行账本，不能成为用户可见历史。

本提案执行 Task #1716、Story #431 的人工裁定（评论 #1136）。它只设计本机配置页、独立执行历史、追加状态事件和真实状态展示。独立托管、跨租约/跨身份结果恢复、服务端 reservation/recover-result、Journal 防覆盖及可审计重试已经拆入 [Story #432](http://124.220.44.12/project/3/stories/432)，不在本 change 的实现范围，也不是当前已具备的能力。

当前 `WorkerOwnedService` 的成功 `/complete` 路径不会调用 `WorkJournal.Remove`；明确 `/fail` 成功和 `new_token_required` 冲突会清理 journal。本变更不改变这些既有语义，不把历史表作为结果恢复账本，也不把历史 GET 变成恢复控制面。

## 目标

- 在 `ConfigurationPortal.html` 的 Agent 详情提供“基本信息、任务类型、提示词、任务记录”四个中文标签；切换标签或 Agent 不丢失未保存编辑。
- 在 `HistoryDatabasePath` 中维护独立于 `WorkJournal`/`ExecutionStore`、与当前 Worker/Server scope 绑定的本机 attempt 快照和只追加状态事件；重启、停用或移除 Agent 不删除历史，同 ID 重建后可查看。
- 在现有 loopback、Host、同源、portal 标记和 `no-store` 边界内提供受限分页及详情只读接口；不查询服务端 AgentRun、生产 API 或旧 `/api/executions`。
- 如实显示执行中、执行失败、已确认交付、结果待交付和执行中断；不能将未知交付或中断标为成功，不能为显示历史重调 Provider。
- 以逐工作类型 allowlist、固定错误投影、长度限制和脱敏确保 token、凭据、提示词、完整上下文、绝对路径和原始输出不进入历史或页面。

## 非目标和 #432 依赖

- 不改造旧 Processor Portal、`ExecutionStore`、`/api/executions`、服务端 AgentRun、队列、单实例锁、配置 revision-CAS 或现有 claim/complete/fail 契约。
- 不新增服务端 migration、reservation/recover-result 接口、跨租约结果恢复、Journal 防覆盖或自动重试；这些均由 Story #432 设计、实现及验收。
- 不承诺 lease 过期、completion 结果未知、原 Agent 删除/停用/工作类型变更、同 ID 重建或新 token/claim 后，已有 raw result 一定能自动补交付。当前实现仍存在 `WorkJournal` 按 `work_id` 覆盖和 `new_token_required` 清理的已知风险，#431 只如实记录其可见事实。
- 不部署、不迁移生产数据。历史 GET 不请求生产 API，历史浏览不调用 Provider、journal replay、结果发布或运行控制。

## 影响范围

- `src/nodes/AgentBoard.Node/WorkerOwned/`：本机历史存储、状态事件、安全投影、现有运行路径的历史写入及本机只读路由。
- `ConfigurationPortal.html`：四标签、任务记录列表、详情 hash 路由和安全显示。
- `src/nodes/AgentBoard.Node.Tests/` 与 `scripts/test_worker_owned_portal.cjs`：存储、状态、投影、接口安全、DOM 与现有启动/停止回归。
- 不修改 `src/backend-fastapi/`；#432 将单独处理其恢复契约和 Journal 保护。

## 风险、现有边界与缓解

- Provider 前历史 `running` 写入失败：是前置失败；不得调用 Provider，留下无 secret 本机诊断并走既有安全失败边界。
- Provider 已产出、journal 已保存而 pending 历史写入失败，或 `/complete` 已确认而 succeeded 写入失败：当前进程只可有界重试本地历史写入；不能安全确认时保留现有 journal、停止进一步交付，并让记录在下次启动如实成为“执行中断”。不得由 #431 申请新 token、重调 Provider 或声称跨租约补交付。若当前 live fence 和现有 journal 身份仍有效，Worker 可使用既有安全路径继续同一次交付；否则转为 #432/人工处理边界。
- lease 到期、原 Agent 不可选或新候选写入前的 Journal 覆盖风险没有因本 change 修复。页面只能显示本机已知的 `interrupted`/`result_pending_delivery` 事实与固定诊断码；完整结果保护和自动恢复依赖 #432。
- 原始 JSON 和异常文本不可信：仅持久化受控标量与固定错误代码，集中脱敏和限长只是纵深防御，不替代 allowlist。
- 同一 SQLite 被其他 Worker/Server 误用：identity 首次原子绑定 scope，所有读写带 scope；冲突、损坏和必要写入失败 fail-closed，保留原数据库。

## 与上一版的差异

上一版将服务端 result-recovery reservation、`recover-result`、保留期人工终态及 reservation-aware Journal 清理作为 #431 承诺。本版全部移除，改为 Story #432 依赖和未覆盖风险；同时保留并细化 #431 的本机历史、状态事件、安全投影、接口、页面和真实展示验收。这一拆分不表示 Journal 覆盖风险已经修复。
