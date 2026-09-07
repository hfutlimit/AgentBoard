## ADDED Requirements

### Requirement: Worker-owned 本机配置台提供隔离的任务记录

在 Worker-owned 模式下，系统 SHALL 在每个当前配置 Agent 详情提供“基本信息、任务类型、提示词、任务记录”四个中文标签。任务记录 SHALL 只展示当前 Worker/Server scope 和当前 Agent 的本机持久化 attempt；不得用服务端 AgentRun、生产 API、旧 `ExecutionStore` 或 `/api/executions` 填充。切换标签或 Agent SHALL 保留未保存编辑；历史不得因停用、移除或重启删除，重建同 ID Agent 后可在同 scope 查看。列表 SHALL 以 `(started_at, record_id)` 倒序 keyset cursor 分页，默认 20 且服务端限制页大小；详情 SHALL 走可回退 hash 路由并显示状态演进、交付状态和受控详情。默认列表不得展示审计标识、凭据、提示词、完整 context、绝对路径或 token。

#### Scenario: 当前 Agent 的安全分页与详情返回

- **GIVEN** 同一 SQLite 有不同 scope 或 Agent 的记录，当前配置含 `design-a`
- **WHEN** 操作者切换 `design-a` 的任务记录、翻页、打开详情并返回
- **THEN** 仅显示当前 scope/Agent 的稳定倒序记录，前后页不重复或跳项
- **AND** draft、详情路由、返回和浏览器前进后退均保持可用
- **AND** 页面不渲染 token、凭据、提示词、context 或绝对路径

### Requirement: 本机历史具有只追加状态事件、严格投影和诚实状态

系统 SHALL 在 `HistoryDatabasePath` 维护独立于 `WorkJournal`/`ExecutionStore` 的 history records 与只追加状态 events，并以 canonical Server origin + Worker ID 绑定 scope。一次 attempt SHALL 由 `work_id` 和 fenced token 指纹唯一标识。每次状态转换 SHALL 在同一事务更新记录快照并追加有序事件；详情 SHALL 返回完整事件序列。Provider 前 SHALL 已持久化 `running`；journal 保存原始结果且 pending 落库成功后 SHALL 为 `result_pending_delivery`；completion 确认且 success 落库成功后 SHALL 为 `succeeded`；Provider、校验或明确 fail 路径 SHALL 为 `failed`；启动恢复遗留 `running` SHALL 为 `interrupted`。当本机无法持久化确认事实时，系统 SHALL 显示已知的 pending/interrupted/失败事实，不得标记为成功。

系统 SHALL 保持当前 journal cleanup：成功 completion 不删除 journal；明确 fail 成功和 `new_token_required` 的现有语义不因本功能改变。Provider 前必要历史写入失败时 SHALL 不调用 Provider。journal 成功而 pending 历史写入失败、或 completion 成功而 succeeded 写入失败时，Worker SHALL 仅作有界本地写入重试、保留现有 journal 和无 secret 诊断，不得重调 Provider、申请新 token 或声称跨租约恢复。只在当前 live fence 与既有身份仍有效时，Worker MAY 使用现有路径继续该次交付；否则 SHALL 保留 pending/interrupted 的真实状态并等待 Story #432 或人工处理。

结果投影 SHALL 限于七种 work kind、普通 review 和 discussion author/reviewer turn 的显式 allowlist；未知字段和 raw JSON SHALL 不持久化或返回。错误详情 SHALL 使用固定安全代码而非原始异常。写入和 DTO SHALL 有集中脱敏与长度限制。scope 初始化或必要历史写入失败时 SHALL fail-closed，并留下无 secret 的本机诊断。

两个 GET SHALL 处于 `/api/local` 的 loopback、Host、同源、`X-AgentBoard-Local-Portal: 1` 与 no-store 边界：`/agents/{agentId}/work-records` 提供受签名 cursor/状态/页大小限制的摘要，`/work-records/{recordId}` 提供 scope/当前 Agent 限定的详情。非法参数 SHALL 为 400，未知 Agent、跨 scope、已移除 Agent 或不存在记录 SHALL 为 404，本机来源/portal 标记失败 SHALL 为 403，响应不得含敏感内部信息。history GET SHALL 不请求 Server/生产 API、调用 Provider、读取 journal raw result、发布结果或暴露旧 `/api/executions`。

#### Scenario: 本机历史写入故障不会伪称交付或重跑 Provider

- **GIVEN** Provider 结果已成功写入 `WorkJournal`
- **WHEN** pending 历史写入失败，或 `/complete` 已返回成功但 succeeded 历史写入失败
- **THEN** Worker 只在当前进程有界重试本地状态写入，保留 journal 且不再次调用 Provider
- **AND** 未持久化的完成事实不得显示为成功；重启遗留 running SHALL 显示为执行中断
- **AND** 若 live fence 已过期、身份变更或 Journal 被覆盖，系统不得声称能自动恢复，而是如实保留已知状态并依赖 Story #432 或人工处理

### Requirement: #431 不承诺跨租约恢复或 Journal 防覆盖

本 change SHALL 不新增或依赖服务端 reservation/recover-result、跨租约/跨 Agent 结果恢复、Journal 防覆盖或自动重试。lease 到期且 completion 结果未知、原 Agent 删除/停用/工作类型变更、同 ID 重建、新 token/claim 以及候选选择前 Journal 覆盖属于 Story #432 的依赖和已知风险，不得被任务记录页面、history GET 或状态投影伪装为已解决。后续 #432 SHALL 另行定义并验证结果保护、原身份补交付、Journal 不覆盖和可审计人工对账。

#### Scenario: 未覆盖恢复窗口如实呈现并转交 #432

- **GIVEN** 一条本机记录处于 `result_pending_delivery` 或 `interrupted`，且 lease 已过期、原 Agent 不可用或 `WorkJournal` 原始结果已不可安全确认
- **WHEN** 操作者刷新任务记录或打开详情
- **THEN** 页面只展示已知的状态、交付状态和固定诊断，不显示“已确认交付”，不调用 Provider 或 Server 恢复接口
- **AND** 实施和验收须将跨租约补交付、Journal 防覆盖、原身份恢复及人工对账列为 Story #432 的后续工作，不得在 #431 中标记为已实现
