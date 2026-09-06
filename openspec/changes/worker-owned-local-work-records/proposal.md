# Change: Worker-owned 本机任务记录页面

## 背景

Worker-owned 配置台已经能维护本机 Agent、保存配置并控制本机 Worker 的启动和排空停止，但不能向操作者说明某个本机 Agent 实际执行过什么、结果是否已成功交付、或 Worker 在重启前中断在何处。旧 `ExecutionStore` 与 `/api/executions` 服务于另一套执行模型；`WorkJournal` 则是完成后删除的 fenced-claim 重放账本。二者都不能作为本功能的用户可见历史来源。

## 目标

- 在 `ConfigurationPortal.html` 的每个 Agent 详情中提供“基本信息、任务类型、提示词、任务记录”四标签，切换不丢失未保存配置编辑。
- 在当前 `HistoryDatabasePath` 内增加独立、scope 绑定、长期保留的 Worker-owned 本机执行历史，按一次实际 attempt（`work_id` + fenced claim token）记录。
- 为当前 portal 提供受既有本机安全边界保护的摘要分页和详情只读接口；接口不调用生产 API，不代理旧 `/api/executions`。
- 如实显示运行、结果待交付、成功、失败与启动恢复中断；已有 journal 结果恢复时只补交付，不能因为记录展示而再次执行 Provider。

## 非目标

- 不改造旧 Processor Portal、`ExecutionStore`、`/api/executions` 或服务端 AgentRun。
- 不把完整业务上下文、提示词、凭据、token 或绝对工作目录持久化/返回给浏览器。
- 不改变 Server fenced claim/complete/fail 契约、队列选择、单实例锁、配置 revision-CAS 或“停止执行=排空当前任务”的语义。
- 本 change 不部署、不迁移生产数据库，也不让本机历史接口访问生产 API。

## 影响范围

- `src/nodes/AgentBoard.Node/WorkerOwned/`：新增历史存储、脱敏与 DTO，接入 `WorkerOwnedService`、`ConfigurationPortal`。
- `src/nodes/AgentBoard.Node/WorkerOwned/ConfigurationPortal.html`：四标签、任务记录列表、详情 hash 路由与交互状态。
- `src/nodes/AgentBoard.Node.Tests/`、`scripts/test_worker_owned_portal.cjs`：存储/恢复/API/页面行为回归。

## 风险与缓解

- 历史写入失败若继续调用 Provider，会造成不可审计执行；将其视为执行前置条件失败，走既有 fail/retry 边界并写本地诊断日志。
- 共享 SQLite 文件可能混读不同 Server/Worker；历史库使用与 journal 相同的稳定 scope，首次绑定后拒绝不匹配 scope，并且所有查询强制 scope 谓词。
- 原始 Provider 输出与错误可能带敏感内容；写入前统一限长、移除已知 secret 键和值模式、归一化本机路径，接口只投影脱敏字段。
- 崩溃窗口可能留下 running attempt；启动时一次性标记为“执行中断/待恢复”，若已有 journal 结果则只进入补交付路径；无结果 attempt 不会由查看页面触发 Provider。
