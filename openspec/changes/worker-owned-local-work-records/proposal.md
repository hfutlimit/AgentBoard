# Change: Worker-owned 本机任务记录页面

## 背景

Worker-owned 配置台已能维护本机 Agent、保存配置并控制本机 Worker 的启动与排空停止，但操作者无法查看某个本机 Agent 的实际执行、交付确认或重启中断。旧 `ExecutionStore`/`/api/executions` 属于另一套执行模型；`WorkJournal` 是含 fenced claim token 与原始结果的运行重放账本，不能成为用户可见历史。

当前 `WorkerOwnedService` 的成功 `/complete` 路径**不会**调用 `WorkJournal.Remove`；只有服务端明确 fail 成功和 `new_token_required` 冲突会清理 journal。本变更保持该既有 cleanup 语义，不以历史功能为由改变它。

## 目标

- 在 `ConfigurationPortal.html` 的 Agent 详情提供“基本信息、任务类型、提示词、任务记录”四标签，切换不丢失未保存编辑。
- 在 `HistoryDatabasePath` 内创建独立、scope 绑定、长期保留的 attempt 历史及追加状态事件；一次 attempt 由 `work_id` 与 fenced token 指纹唯一确定。
- 提供受现有 loopback/Host/同源/no-store 边界及 portal 标记保护的本机只读分页、详情接口，不访问生产 API，也不代理旧执行接口。
- 可靠反映执行、待交付、已交付、失败和恢复中断；journal 已有结果时只补交付，绝不因历史恢复或查询重调 Provider。

## 非目标

- 不改造旧 Processor Portal、`ExecutionStore`、`/api/executions`、服务端 AgentRun、fenced claim/complete/fail 契约、队列、单实例锁或配置 revision-CAS。
- 不变更 `WorkJournal` 的现有成功保留、明确失败/换 token 清理行为。
- 不向历史持久化或浏览器返回完整上下文、提示词、凭据、token、绝对工作路径、原始 Provider 输出或原始异常文本。
- 不部署、不迁移生产数据库；两条本机历史 GET 不请求生产 API。

## 影响范围

- `src/nodes/AgentBoard.Node/WorkerOwned/`：历史存储、追加事件、严格结果投影、恢复/对账和本机路由。
- `ConfigurationPortal.html`：四标签、任务记录列表、详情 hash 路由与安全显示。
- `src/nodes/AgentBoard.Node.Tests/`、`scripts/test_worker_owned_portal.cjs`：存储、故障窗口、接口安全和 DOM 回归。

## 风险与缓解

- Provider 前的历史写入失败会造成不可审计执行：视为前置失败，不调用 Provider，不确认完成，记录无 secret 的本机诊断并走已有 fail/retry 边界。
- journal 成功而 pending 历史落库失败、或 completion 已确认而成功状态落库失败：保留 journal，停止该次交付并在 Worker 专属恢复/对账中幂等补写；绝不以查看历史重调 Provider。
- 原始 JSON 与异常文本是不可信输入：按七种 work kind 建立允许字段投影；未知字段一律不用，失败详情使用固定错误代码而非原始文本，再进行集中脱敏与限长。
- 同一 SQLite 被其他 Worker/Server 使用：历史 identity 原子绑定稳定 scope，所有读写含 scope 谓词，冲突 fail-closed 且不删除原库。
