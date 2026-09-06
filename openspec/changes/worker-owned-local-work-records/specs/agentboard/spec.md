## ADDED Requirements

### Requirement: Worker-owned 本机配置台提供隔离的任务记录

在 Worker-owned 模式下，系统 SHALL 在本机配置台的每个当前配置 Agent 详情中提供“基本信息、任务类型、提示词、任务记录”四个中文标签。任务记录 SHALL 只展示当前 Worker/Server scope 与当前 Agent 的本机持久化执行尝试；系统不得用服务端 AgentRun、生产 API、旧 `ExecutionStore` 或 `/api/executions` 填充该页面。切换标签或 Agent SHALL 保留尚未保存的配置编辑，历史记录 SHALL 不因 Agent 停用、移除或 Worker 重启而删除；重新创建同 ID 的 Agent 后 SHALL 可查看同一 scope 的既有记录。

列表 SHALL 按开始时间和稳定 record ID 倒序以 keyset cursor 分页，默认 20 条且服务端限制页大小；页面 SHALL 提供刷新、上一页、下一页、加载、空和错误状态。详情 SHALL 可经可回退 hash 路由访问，并显示本机 Agent、Provider/模型、工作类型、关联业务项、起止时间、状态演进、结果或错误详情，以及交付确认状态。默认列表不得显示凭据、提示词、完整上下文、绝对工作目录或原始 token；审计标识只可在详情按需显示。

#### Scenario: 当前 Agent 查看本机隔离记录并返回详情

- **GIVEN** 同一数据库中存在不同 scope 或不同 Agent 的本机执行历史，且当前本机配置包含 Agent `design-a`
- **WHEN** 操作者切换到 `design-a` 的“任务记录”标签、翻到下一页并打开一条记录详情后返回
- **THEN** 列表只显示当前 scope 和 `design-a` 的倒序记录，且前后页不重复或跳过记录
- **AND** 详情和返回路由保持可用，未保存的基本信息、任务类型和提示词编辑仍然保留
- **AND** 页面未显示 token、凭据、提示词、完整上下文或绝对路径

### Requirement: 本机任务记录安全持久化、恢复与只读访问

系统 SHALL 在当前 Worker 的 `HistoryDatabasePath` 中维护独立于 `WorkJournal` 和 `ExecutionStore` 的 Worker-owned 历史表，并以 canonical Server origin 与 Worker ID 绑定 scope。一次物理执行尝试 SHALL 由 `work_id` 和 fenced claim token 指纹唯一标识；token 本身不得持久化到可见历史或返回给浏览器。Provider 实际执行前 SHALL 已经持久化 `running` 记录；journal 已保存结构化结果而服务端 completion 未确认时 SHALL 为 `result_pending_delivery`；completion 确认后 SHALL 为 `succeeded`；Provider、校验或明确 fail 路径 SHALL 为 `failed`。启动恢复 SHALL 将遗留 `running` 标为 `interrupted`，保留已有 journal 结果用于只补交付，且展示历史或恢复扫描不得调用 Provider。

所有结果、错误和状态备注 SHALL 在持久化与返回前进行集中脱敏和长度限制。系统 SHALL 拒绝跨 scope 数据；历史写入或 scope 初始化失败时 SHALL fail-closed，不得静默调用未记录的 Provider，并留下不含 secret 的本机诊断日志。

系统 SHALL 在既有 `/api/local` 的 loopback、Host、同源与 no-store 安全边界下提供：`GET /api/local/agents/{agentId}/work-records`（限制 cursor、状态过滤和页大小的摘要分页）及 `GET /api/local/work-records/{recordId}`（已脱敏详情）。这两个接口 SHALL 不请求生产 API、不读取/暴露 journal 原始结果、不暴露旧 `/api/executions`，并对非法参数返回 400、未知 Agent/跨 scope/不存在记录返回 404、本机来源校验失败返回 403，响应均不得含敏感内部信息。

#### Scenario: 回执超时后重启只补交付而不重跑 Provider

- **GIVEN** Provider 已成功产生结构化结果且该结果已写入 WorkJournal 和本机历史的 `result_pending_delivery`，但 fenced completion 的网络响应超时
- **WHEN** Worker 重启、重新取得同一工作并进行正常恢复/回执
- **THEN** Worker 使用 journal 中的已保存结果补交付，不再次调用 Provider
- **AND** 服务端 completion 确认后本机历史变为 `succeeded` 和已确认交付
- **AND** 两条本机 GET 接口仅返回脱敏投影，跨 scope 或非本机来源的请求不能读取该记录
