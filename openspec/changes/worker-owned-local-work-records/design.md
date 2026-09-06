# Design: Worker-owned 本机任务记录页面

## 1. 范围和不变量

本设计只扩展 `src/nodes/AgentBoard.Node/WorkerOwned/ConfigurationPortal.html` 及其本机 .NET host。记录代表**当前 Worker-owned 实例**的一次实际执行尝试，不是服务端 AgentRun，也不是旧 `ExecutionStore` 的执行记录。它不读取或混入其他 Worker、其他 Server scope 或生产 API 数据。

下列不变量必须保持：

1. 历史表独立于 `worker_owned_journal`；journal 仍是“claim 身份/原始结果优先落盘、完成后可删除”的重放账本，`ExecutionStore` 和 `/api/executions` 不改变语义。
2. 历史只存可向本机操作者展示的脱敏审计投影；原始 structured result 仅继续由 journal 保存以完成 fenced 回执，不能被历史接口读出。
3. 每一条历史记录都强制绑定 `scope = canonical-server-origin + "|" + worker_id`。即使共享同一 `HistoryDatabasePath`，查询、写入、详情和启动恢复均带该 scope。
4. Provider 调用前，`running` 记录必须同步提交成功；若失败，Provider 不得启动，应记录本地诊断并沿既有 claim 的 fail/retry 边界处理。
5. 历史 GET 绝不调用 Provider、Server API、journal replay 或运行控制。恢复/补交付只能由已经在运行的 `WorkerOwnedService` 执行。
6. 停止仍只设置 drain、停止领取新工作、等待当前调用结束；状态仍唯一来自 `LocalWorkerRuntime.StatusAsync`。

## 2. 组件和数据流

```text
Rabbit delivery
  -> fenced claim (WorkJournal: work id + token + optional raw result)
  -> LocalWorkRecordStore.CreateRunning (durable local audit projection)
  -> Provider
  -> WorkJournal.Save(raw structured result)
  -> LocalWorkRecordStore.MarkResultPendingDelivery(redacted summary/detail)
  -> optional DesignDocumentPublisher
  -> fenced /complete
  -> LocalWorkRecordStore.MarkSucceeded
     \-> on provider/validation/explicit failure: MarkFailed

ConfigurationPortal GET
  -> LocalWorkRecordStore.List(scope, agent, cursor, state)
  -> LocalWorkRecordStore.Get(scope, record id)
  -> sanitized DTO only
```

新增 `LocalWorkRecordStore`（或等价的 WorkerOwned 专属存储）使用 `NodeOptions.HistoryDatabasePath` 的同一 SQLite 文件，但创建自己的 `worker_owned_work_records` 与 `worker_owned_work_record_identity` 表；不得把 schema 塞进或复用 `worker_owned_journal`。初始化沿用 WAL/FULL durability 设置，并通过 identity 表在首次使用时原子写入 scope。已有 journal 所在数据库若被其他 scope 绑定，历史存储同样 fail-closed；保留数据库并要求新路径，而非清空或接管。

`WorkerOwnedService` 将一个 in-memory attempt handle（record id、work id、claim token 指纹）贯穿 claim、执行和回执。token 仅参与唯一性/关联，绝不写入响应或页面；持久化时保存不可逆 SHA-256 指纹，日志也不记录 token。应以 `(scope, work_id, claim_token_fingerprint)` 做唯一约束；同一 fenced token 的重新投递更新同一条 attempt，新 token 创建新的 attempt。

## 3. 本地历史数据模型

表 `worker_owned_work_records` 至少包括：

| 字段 | 说明 |
| --- | --- |
| `record_id` TEXT PK | 随机不可猜测 UUID/ULID；不是 work id 或 token。 |
| `scope` TEXT NOT NULL | canonical server origin + `|` + Worker ID；所有索引和查询的一部分。 |
| `work_id` INTEGER NOT NULL | 服务端业务工作 ID，仅在本机详情中作为审计标识按需展示。 |
| `claim_token_fingerprint` TEXT NOT NULL | SHA-256 token 指纹，用于 attempt 去重，不返回。 |
| `agent_id`, `provider`, `model`, `work_kind` | 运行时已选择的本机 Agent、Provider/模型和七种工作类型。 |
| `business_item_id`, `business_item_label` | 从已 claim 的 work 安全投影得到的关联业务项；label 限长，不保存完整 context。 |
| `state` | `running`、`result_pending_delivery`、`succeeded`、`failed`、`interrupted`。 |
| `started_at`, `ended_at`, `updated_at` | UTC 时间；`ended_at` 仅终态/中断写入。 |
| `result_summary`, `result_detail` | 已脱敏、限长的结果投影；detail 只在详情接口返回。 |
| `failure_summary`, `failure_detail` | 已脱敏、限长的失败投影；detail 只在详情接口返回。 |
| `delivery_state`, `delivered_at` | `not_applicable` / `pending` / `confirmed`；用于明确“结果待交付”。 |
| `transition_note` | 已脱敏、限长的状态迁移说明（例如启动恢复中断），不含异常堆栈。 |

索引为 `(scope, agent_id, started_at DESC, record_id DESC)` 和 `(scope, state, started_at DESC, record_id DESC)`。`business_item_label` 取安全的类型/ID/标题摘要；若来源字段不能安全取得，只保存类型和 ID，不回退保存整段业务 context。

所有可展示文本先经一个中心 `WorkRecordRedactor` 处理，再写库、再作为 DTO 输出。它需要：删除/遮盖常见 JSON secret 名（token、password、secret、authorization、api key 等，大小写不敏感）；遮盖 bearer/token 值、长高熵 token 类字符串；把 Windows、Unix 和 UNC 绝对路径替换为“本机路径已隐藏”；将控制字符标准化；分别限制摘要（例如 500 字符）、详情（例如 8 KiB）和错误详情（例如 4 KiB）。限制常量由实现集中定义并用边界测试锁定。不能可靠识别的敏感业务全文不得被采集；历史 detail 应从已验证的结果字段白名单形成，而非直接复制 `OutputJson`、prompt 或 `context`。

## 4. 状态迁移和恢复

```text
Create before Provider: running
Provider returns and journal raw result fsync succeeds: result_pending_delivery / pending
fenced complete confirmed: succeeded / confirmed
provider, parsing, validation or explicit fail path: failed / not_applicable
process startup finds previous running without terminal transition: interrupted / not_applicable
```

- 创建 `running` 必须发生在 Provider 实际调用前。claim 失败、未被接受或被其他 Agent 拒绝时不创建“执行中”记录。
- Provider 输出经过现有结构校验，且 `WorkJournal.Save` 成功后，历史更新为 `result_pending_delivery`。即使随后 HTTP completion 超时，这个状态仍是准确的本机事实。
- 服务端 `/complete` 仅在成功响应后调用 `MarkSucceeded`；失败回执、Provider 失败或业务校验失败调用 `MarkFailed`，并记录安全摘要。历史更新失败不得把业务成功改写成失败；必须写本地日志并让执行路径保留可重试/可诊断事实。
- `WorkerOwnedService` 在取得数据库与进程锁后、开始消费前执行 `RecoverInterrupted(scope)`：把前次遗留的 `running` 标为 `interrupted`，写入“执行在 Worker 重启前中断；等待正常领取/恢复决策”。它不推测 Provider 是否完成，不伪造成功。
- 若 `WorkJournal` 已有 raw result，后续正常 claim/replay 直接执行 `DesignDocumentPublisher`（如适用）和 fenced completion；不调用 Provider。对应历史先/保持为 `result_pending_delivery`，成功回执后转 `succeeded`。
- 没有 journal result 的 `interrupted` attempt 永久保留作为一次中断审计；以后获准重试时，现有 fenced-claim 规则决定是否执行，使用新 token 的物理尝试另建记录。页面浏览、刷新、详情和恢复标记本身永不重新调用 Provider。

## 5. 受保护的本机接口

接口挂在已有 `/api/local` group，复用 `ConfigurationPortal.IsLocalRequest` endpoint filter：请求必须来自 loopback peer、`localhost` 或 loopback Host，Origin 缺失或严格同源；所有响应 `Cache-Control: no-store`。这两个 GET 是只读，不额外要求 `X-AgentBoard-Local-Portal: 1`，但 portal fetch 仍附带该 header 以保持统一调用习惯。缺少/错误本地来源统一 403 且不泄露存储信息。

### `GET /api/local/agents/{agentId}/work-records`

仅返回当前 scope 和 `agentId` 的本机摘要。`agentId` 必须是当前本机配置中的已知 Agent；未知 Agent 返回 404（不枚举其他历史 Agent）。参数：

| 参数 | 规则 |
| --- | --- |
| `cursor` | 可选、不透明 base64url 游标，编码 `started_at` + `record_id`，最大长度 512；非法、解码失败、scope/agent 不匹配为 400。 |
| `state` | 可选枚举：`running`、`result_pending_delivery`、`succeeded`、`failed`、`interrupted`；其他值为 400。 |
| `pageSize` | 可选整数；默认 20，服务端 clamp 到 1–100。 |

响应为 `{ items, nextCursor }`。每项包含 `recordId`、Agent、工作类型、关联业务项安全摘要、显示状态、开始/结束时间、耗时、结果摘要或失败摘要、`deliveryState`；没有 token、prompt、context、路径或原始结果。按 `(started_at DESC, record_id DESC)` keyset 分页，避免 offset 在新增记录下跳项或重复。

### `GET /api/local/work-records/{recordId}`

`recordId` 只接受 UUID/ULID 格式和合理长度。查询必须限定当前 scope；不存在、跨 scope 或不属于当前本机已配置 Agent 均返回 404。响应是单条已脱敏详情，除摘要外包含 Provider/模型、业务项、起止时间、完整状态演进（时间、状态、已脱敏备注）、结果或错误详情以及 delivery state/时间。它不接受 work id、token 或 agent override，且不会访问 Server/journal/provider。

接口实现将 storage/format 例外映射为不含路径、连接串或 secret 的 400/500 problem detail，同时记录本地结构化日志；不得返回原始 SQLite 异常。

## 6. 页面和路由

保持顶部 Worker 状态和“保存并启动/停止执行”实现：页面继续调用 `GET /api/local/runtime` 的 `LocalWorkerRuntime.StatusAsync` 结果；开始操作先完成 revision-CAS 保存再请求 start；运行中重复点击由 runtime 的 gate 去重；停止只发 drain，只有当前 work 完成、Worker 退出后才显示“未启动”。

每个 Agent 编辑区改为下列四个中文标签：

1. **基本信息**：现有 Agent ID、启用、工具、模型、CLI、超时等字段。
2. **任务类型**：现有七种 work kind 与职责说明。
3. **提示词**：现有通用/专属 pre/post 和执行顺序说明。
4. **任务记录**：当前 selected Agent 的本机历史，独立于编辑表单的 draft。

切换标签时先调用既有 `readEditor()` 将 DOM 值同步入 `snap`，再切换可见 pane；不保存、不重载、不修改 revision，故未保存编辑不丢失。切换 Agent 也先同步当前 draft。任务记录标签首次进入及“刷新”时请求列表；展示加载、错误、空态、上一页/下一页。默认每页 20，前端不信任页面大小，显示服务端给出的分页结果。

详情使用 hash 路由 `#agents/<encoded-agent-id>/work-records/<record-id>`，列表标签使用 `#agents/<encoded-agent-id>/work-records`。进入详情调用单条 GET，提供“返回任务记录”按钮和 `hashchange` 处理；直接打开、浏览器前进/后退均可恢复列表或详情。路由参数必须 `encodeURIComponent`/解码后校验，渲染一律 HTML escape。页面只使用中文业务文字，默认不显示审计 ID；详情可在“审计信息”折叠区显示 record/work ID，仍不显示 token、prompt、context 或路径。

显示映射：`running=执行中`，`result_pending_delivery=结果待交付`，`succeeded=已成功交付`，`failed=执行失败`，`interrupted=执行中断/待恢复`。列表以开始时间倒序，结束时间为空时显示“进行中/未完成”，耗时按当前时间或结束时间计算。任何 API 错误在页面以无敏感中文提示展示，不把 response body 原样插入 DOM。

## 7. 兼容性、错误处理和可观测性

- 不迁移、不读取旧 `ExecutionStore` 表，不提供旧 `/api/executions` 到新路由的 alias；旧门户继续原样工作。
- Agent 从本地配置移除后，历史不删除；同 ID Agent 重新创建后按相同 scope/agent id 可见既有记录。移除期间详情接口按“当前已配置 Agent”约束返回 404，防止历史成为已删除身份枚举端点。
- scope 或数据库 identity 不匹配、存储损坏、写入失败、恢复扫描失败均 fail-closed：不消费/不调用 Provider，记录不含凭据的本机诊断。只读页面返回安全错误；不自动修复、删除或重建数据库。
- 记录状态和 delivery state 是本机审计事实，不能以 Server 或 UI 推测覆盖。操作日志使用 record id、work id、agent、状态类别和异常类型，不写 raw context/token/输出。

## 8. 验收与测试策略

### 单元/存储测试

- 新建、同 token 幂等、新 token 新 attempt、scope/agent 隔离、重开 SQLite 后持久化和不匹配 scope fail-closed。
- 状态迁移：写前 running、journal-result 后 pending、completion 成功、失败、启动恢复 interrupted；恢复只处理 running。
- 脱敏/限长覆盖 JSON secret、Bearer/token、路径、控制字符、超长文本及详情/摘要白名单；确认 DTO 永不含 token、prompt、context、绝对路径或 raw result。
- keyset 分页（倒序、并列时间、无重复/漏项）、cursor 篡改/跨 agent/scope、状态筛选和大小边界。

### HTTP/运行测试

- loopback/host/origin 防护与 no-store 仍生效；非 loopback、DNS-rebind Host、跨源或缺失本机写标记（现有写 API）仍拒绝。历史 GET 不调用生产 HTTP client、provider 或旧 ExecutionStore。
- 未知 Agent、跨 scope record、非法 record/cursor/status 返回明确的 400/404/403 且没有路径/凭据。
- 用 fake adapter 验证：历史写入失败时 adapter 未调用；成功结果存 journal 后模拟 completion 网络错误再启动，adapter 不会二次调用且最终转成功；crash 前 running 变 interrupted。
- `LocalWorkerRuntime` 重复 start 不创建第二消费者，stop 维持 draining 至 active work 完成；现有 runtime 测试保持通过。

### 页面 DOM/交互测试

- 四标签存在且为中文；基本信息/任务类型/提示词已有配置可编辑；多 Agent 切换和四标签切换不丢失 draft。
- mock 本机接口验证任务记录的 loading/error/empty/list/pagination/refresh、状态文案、详情与 hash 返回/前进后退。
- 断言默认列表与详情不渲染凭据、提示词、完整上下文、绝对路径或 token；审计标识仅在详情折叠区。

QA 应在隔离的本地配置和 `HistoryDatabasePath` 上启动实际 portal/Worker，使用可控 fake provider 或隔离服务验证记录、分页、详情和 drain 回归；不得连接或变更生产环境。生产部署、真实 provider 执行和跨进程 crash E2E 不属于本 design 的已验证结论，实施后必须单独报告。
