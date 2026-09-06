## ADDED Requirements

### Requirement: Worker-owned 本机配置台提供隔离的任务记录

在 Worker-owned 模式下，系统 SHALL 在每个当前配置 Agent 详情提供“基本信息、任务类型、提示词、任务记录”四个中文标签。任务记录 SHALL 只展示当前 Worker/Server scope 和当前 Agent 的本机持久化 attempt；不得用服务端 AgentRun、生产 API、旧 `ExecutionStore` 或 `/api/executions` 填充。切换标签或 Agent SHALL 保留未保存编辑；历史不得因停用、移除或重启删除，重建同 ID Agent 后可在同 scope 查看。列表 SHALL 以 `(started_at, record_id)` 倒序 keyset cursor 分页，默认 20 且服务端限制页大小；详情 SHALL 走可回退 hash 路由并显示状态演进、交付状态和受控详情。默认列表不得展示审计标识、凭据、提示词、完整 context、绝对路径或 token。

#### Scenario: 当前 Agent 的安全分页与详情返回

- **GIVEN** 同一 SQLite 有不同 scope 或 Agent 的记录，当前配置含 `design-a`
- **WHEN** 操作者切换 `design-a` 的任务记录、翻页、打开详情并返回
- **THEN** 仅显示当前 scope/Agent 的稳定倒序记录，前后页不重复或跳项
- **AND** draft、详情路由、返回和浏览器前进后退均保持可用
- **AND** 页面不渲染 token、凭据、提示词、context 或绝对路径

### Requirement: 本机历史具有可恢复状态事件与严格安全边界

系统 SHALL 在 `HistoryDatabasePath` 维护独立于 `WorkJournal`/`ExecutionStore` 的 history records 与只追加状态 events，并以 canonical Server origin + Worker ID 绑定 scope。一次 attempt SHALL 由 `work_id` 和 fenced token 指纹唯一标识。每次状态转换 SHALL 在同一事务更新记录快照并追加有序事件；详情 SHALL 返回完整事件序列。Provider 前 SHALL 已持久化 `running`；journal 保存原始结果后 SHALL 为 `result_pending_delivery`；completion 确认后 SHALL 为 `succeeded`；Provider、校验或明确 fail 路径 SHALL 为 `failed`；启动恢复遗留 `running` SHALL 为 `interrupted`。

系统 SHALL 保持当前 journal cleanup：成功 completion 不删除 journal，仅既有明确 fail 成功和换 token 冲突可删除。journal 成功而 pending 失败、或 completion 成功而 succeeded 写入失败时，Worker SHALL 保留 journal 并在持锁恢复/对账中幂等补写，且不得重调 Provider。历史 GET/恢复扫描不得调用 Provider；GET 不得请求生产 API、读取 journal raw result、暴露旧 `/api/executions`。

结果投影 SHALL 限于七种 work kind 的显式 allowlist；未知字段和 raw JSON SHALL 不持久化或返回。错误详情 SHALL 使用固定安全代码而非原始异常。写入和 DTO SHALL 有集中脱敏与长度限制。scope 初始化或必要历史写入失败时 SHALL fail-closed，不得静默调用未记录 Provider，并留下无 secret 的本机诊断。

两个 GET SHALL 处于 `/api/local` 的 loopback、Host、同源、`X-AgentBoard-Local-Portal: 1` 与 no-store 边界：`/agents/{agentId}/work-records` 提供受签名 cursor/状态/页大小限制的摘要，`/work-records/{recordId}` 提供 scope/当前 Agent 限定的详情。非法参数 SHALL 为 400，未知 Agent、跨 scope、已移除 Agent 或不存在记录 SHALL 为 404，本机来源/portal 标记失败 SHALL 为 403，响应不得含敏感内部信息。

#### Scenario: pending 与 completion 写入故障不会重跑 Provider

- **GIVEN** Provider 结果已经成功写入 WorkJournal
- **WHEN** pending 历史写入失败，或 `/complete` 返回成功但 succeeded 历史写入失败，Worker 重启/重放
- **THEN** Worker 保留 journal 并只执行幂等历史对账、必要发布和 fenced completion，不再次调用 Provider
- **AND** 状态恢复后事件序列可显示 pending、confirmed 或安全失败事实
- **AND** 成功 completion 不会因为本功能删除 journal
