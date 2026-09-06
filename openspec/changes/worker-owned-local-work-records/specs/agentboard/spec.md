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

系统 SHALL 保持当前 journal cleanup：成功 completion 不删除 journal；普通、未登记恢复保留的 attempt 仍仅可在既有明确 fail 成功或换 token 冲突时删除。journal 成功而 pending 失败、或 completion 成功而 succeeded 写入失败时，Worker SHALL 保留 journal 并在持锁恢复/对账中幂等补写，且不得重调 Provider。历史 GET/恢复扫描不得调用 Provider；GET 不得请求生产 API、读取 journal raw result、暴露旧 `/api/executions`。

结果投影 SHALL 限于七种 work kind 的显式 allowlist；未知字段和 raw JSON SHALL 不持久化或返回。错误详情 SHALL 使用固定安全代码而非原始异常。写入和 DTO SHALL 有集中脱敏与长度限制。scope 初始化或必要历史写入失败时 SHALL fail-closed，不得静默调用未记录 Provider，并留下无 secret 的本机诊断。

两个 GET SHALL 处于 `/api/local` 的 loopback、Host、同源、`X-AgentBoard-Local-Portal: 1` 与 no-store 边界：`/agents/{agentId}/work-records` 提供受签名 cursor/状态/页大小限制的摘要，`/work-records/{recordId}` 提供 scope/当前 Agent 限定的详情。非法参数 SHALL 为 400，未知 Agent、跨 scope、已移除 Agent 或不存在记录 SHALL 为 404，本机来源/portal 标记失败 SHALL 为 403，响应不得含敏感内部信息。

#### Scenario: pending 与 completion 写入故障不会重跑 Provider

- **GIVEN** Provider 结果已经成功写入 WorkJournal
- **WHEN** pending 历史写入失败，或 `/complete` 返回成功但 succeeded 历史写入失败，Worker 重启/重放
- **THEN** Worker 保留 journal 并只执行幂等历史对账、必要发布和 fenced completion，不再次调用 Provider
- **AND** 状态恢复后事件序列可显示 pending、confirmed 或安全失败事实
- **AND** 成功 completion 不会因为本功能删除 journal

### Requirement: 已开始 fenced attempt 的结果恢复保留防止过期重跑

系统 SHALL 在 Provider 调用前，以当前 live fence 创建或幂等取得一条服务端 result-recovery reservation。该保留 SHALL 绑定 work、attempt、project/kind、原 worker、Agent、token 指纹和 input hash，具有服务端固定且有限的保留期；它不得保存或通过读取接口暴露 token、完整 context 或原始 Provider result。Worker 仅在 reserve 与本地 `running` 记录均成功后调用 Provider。

保留有效时，lease 过期后的 claim SHALL 返回 `result_recovery_required`，不得签发/接受新 token、增加 attempt、重置业务执行或删除该 attempt 的 journal。持原 worker/Agent/token 的 Worker SHALL 可通过受认证的 `recover-result` 交付 journal 中的原结果；该接口 SHALL 校验 reservation、原身份、input hash、业务前置条件与 canonical result digest，并复用 `/complete` 的结果校验、业务 mutation 和幂等性。结果不同、身份不符、业务输入变化、保留期已过或明确终结 SHALL 拒绝且不写入业务结果。保留期到达而未恢复 SHALL 进入可诊断的人工对账终态，不能自动创建新 offer 或重跑 Provider。

#### Scenario: completion 超时后 lease 过期仍只交付原结果

- **GIVEN** Worker 已取得 reservation、Provider result 已写入本地 WorkJournal，且普通 `/complete` 的传输结果未知
- **WHEN** lease 到期后同一 Worker 重启或收到重放消息
- **THEN** claim 指示 `result_recovery_required`，Worker 保留原 journal 并以同一 worker、Agent、token 调用 `recover-result`
- **AND** 服务端使用与 `/complete` 相同的校验和幂等结果写入，成功时记录为 completed
- **AND** Provider 不会再次调用，也不会签发新 token

#### Scenario: reserve 后结果永久丢失不触发重复执行

- **GIVEN** reservation 已成功，但 Worker 在 Provider 调用边界崩溃且没有可恢复的 journal result
- **WHEN** 保留期届满
- **THEN** 服务端和本机历史均记录安全、可诊断的人工对账事实
- **AND** 系统不自动重试 Provider、不发放新 token，也不创建新的 offer
