# Tasks: Worker-owned 本机任务记录页面

## 1. 历史存储与脱敏

- [ ] 新增 WorkerOwned 专属 `LocalWorkRecordStore`、identity 表和 `worker_owned_work_records` schema，复用 `HistoryDatabasePath` 但不复用或修改 `WorkJournal`/`ExecutionStore` 表。
- [ ] 实现 canonical Server origin + Worker ID scope 绑定、首次原子初始化、跨 scope fail-closed、attempt 的 `(scope, work_id, token 指纹)` 幂等和新 token 新记录。
- [ ] 实现 `WorkRecordRedactor`、安全结果字段投影和摘要/详情/错误限长；禁止持久化或 DTO 输出 token、凭据、prompt、完整 context、绝对路径和 raw result。
- [ ] 实现状态、delivery state、状态演进和 keyset 分页/不透明游标。

## 2. Worker 生命周期接入

- [ ] 在 `WorkerOwnedService` claim 成功且 Provider 前创建 durable `running` record；写入失败时不调用 Provider，按现有失败/重试边界处理并写安全本地日志。
- [ ] 在 journal 保存结果后写 `result_pending_delivery`，在 fenced completion 成功后写 `succeeded`，在 Provider/解析/校验/明确 fail 路径写 `failed`。
- [ ] 在持有本机进程锁、消费前恢复遗留 running 为 `interrupted`；已有 journal result 仅补交付，确保显示历史/恢复扫描不会调用 Provider。
- [ ] 保持 journal cleanup、fenced claim/complete/fail、DesignDocumentPublisher、队列 ACK 和 drain 的既有行为；不改变旧执行存储和 API。

## 3. 本机只读接口

- [ ] 将两条 history GET 路由接入既有 `/api/local` group/filter：`/agents/{agentId}/work-records` 和 `/work-records/{recordId}`。
- [ ] 实现当前配置 Agent 校验、scope 限定、默认 20/最大 100、状态过滤、不透明 cursor 和安全 400/404/500 错误映射。
- [ ] 确保接口不使用生产 HTTP client、journal raw-result 读取、Provider 或 `/api/executions`；响应 `no-store` 且不暴露内部异常。

## 4. 四标签 portal 与路由

- [ ] 将 Agent 编辑区拆为“基本信息、任务类型、提示词、任务记录”四个中文标签，复用 draft 同步以保持未保存编辑。
- [ ] 新增任务记录列表 loading/error/empty/refresh/前后翻页和中文状态/时间/摘要展示。
- [ ] 新增受校验、可直接访问、支持前进后退的 hash 列表/详情路由，以及详情返回入口和仅按需折叠的审计信息。
- [ ] 保持 `LocalWorkerRuntime.StatusAsync` 状态展示、保存后启动、重复 start 防重和排空停止交互不回归。

## 5. 自动化验证

- [ ] 增加 .NET 存储测试：schema/reopen、scope/Agent 隔离、attempt 幂等、全部状态迁移、恢复、分页和 cursor 篡改。
- [ ] 增加脱敏/限长及接口安全测试：token/secret/path/context 不泄露，非本机/跨源拒绝，未知 Agent/跨 scope 详情和非法参数安全失败。
- [ ] 增加 Worker 测试：记录写入失败不调用 adapter，保存 result 后回执错误重启只交付不重跑，running crash 恢复为 interrupted，drain/start 语义不变。
- [ ] 更新 `scripts/test_worker_owned_portal.cjs`：四标签 draft 保留、列表/分页/刷新/空错态、详情 hash 路由及敏感字段不渲染。
- [ ] 在隔离临时配置和数据库运行 focused .NET/DOM tests；QA 单独执行实际本机 portal/Worker 启停、记录与分页回归，并如实区分 fake/隔离验证与未执行的生产/真实 provider 验证。
