# Design: Worker-owned 本机任务记录页面

## 1. 范围、决策和不变量

本设计扩展 `src/nodes/AgentBoard.Node/WorkerOwned/ConfigurationPortal.html`、同进程 .NET host，以及为已启动 fenced attempt 增设的最小服务端恢复协作契约。记录代表当前 Worker-owned 实例的一次物理执行尝试，不是服务端 AgentRun，也不是旧 `ExecutionStore` 的执行记录；本机历史读取不混入其他 Worker、Server scope 或生产 API 数据。

1. 历史使用 `HistoryDatabasePath` 的同一 SQLite 文件，但完全独立于 `worker_owned_journal` 和旧存储。`WorkJournal` 仍保存 claim token 与原始结果供 fenced 重放；历史只保存可展示投影，接口从不读取 journal 原始结果。
2. 兼容现有 `WorkerOwnedService`：成功 `/complete` 后不调用 `WorkJournal.Remove`；普通、未保留 attempt 的服务端明确 `/fail` 成功和 `new_token_required` 继续保持既有 Remove 行为。已登记恢复保留的 attempt 不得因 `new_token_required` 清除 journal；它只能由服务端完成确认、明确终结或保留期转人工处置决定后续清理。不得在本 change 中宣称或实现“成功后删除 journal”。
3. `scope = canonical-server-origin + "|" + worker_id`。历史 identity 首次创建原子写入该 scope；身份不符、数据库损坏、恢复或必要写入失败都 fail-closed，不删除或重建数据库。
4. Provider 实际调用前，`running` 和其首个状态事件必须在一个 SQLite 事务中提交。失败时不得调用 Provider；仅写安全本地日志，按现有 fenced fail/retry 边界退出。
5. 历史 GET 不调用 Provider、Server API、journal replay 或运行控制。只有已持锁运行的 `WorkerOwnedService` 能做恢复、对账、发布与补交付；它必须先取得服务端恢复保留和本地 `running` 记录，才允许 Provider 调用。
6. 停止仍为 drain：停止领取新工作、等待当前调用结束。顶部状态唯一来自 `LocalWorkerRuntime.StatusAsync`。

## 2. 组件、存储与状态事件

```text
claim accepted -> ReserveResultRecovery (live fence) -> CreateRunning + event (atomic) -> Provider
  -> WorkJournal.Save(raw result) -> MarkPending + event (atomic) -> idempotent publish if design -> Save journal
  -> live fenced /complete OR expired-fence /recover-result -> MarkSucceeded + event (atomic)
                 \-> provider/validation/explicit fail -> MarkFailed + event

Worker startup/replay (process lock held)
  -> RecoverRunning -> interrupted event
  -> reservation-aware journal/result reconciliation -> pending or succeeded event, never Provider

portal GET -> LocalWorkRecordStore scoped DTOs only
```

`LocalWorkRecordStore` 建立下列专属表，初始化使用 WAL 与 `synchronous=FULL`：

| 表 | 关键列和约束 | 用途 |
| --- | --- | --- |
| `worker_owned_work_record_identity` | `singleton=1`, `scope`, `cursor_key`（随机 32-byte secret） | 原子 scope 绑定及签名 cursor；key 永不返回/记录。 |
| `worker_owned_work_records` | `record_id` 随机 UUID/ULID PK；`scope`；`work_id`；`claim_token_fingerprint`；`agent_id/provider/model/work_kind`；安全业务项投影；状态、交付状态、时间和投影字段；`UNIQUE(scope, work_id, claim_token_fingerprint)` | 一条物理 attempt 的当前快照。 |
| `worker_owned_work_record_events` | `scope`, `record_id`, `sequence`, `occurred_at`, `state`, `delivery_state`, `event_code`; `UNIQUE(scope, record_id, sequence)` | 只追加的、可展示完整状态演进。 |

记录表至少索引 `(scope, agent_id, started_at DESC, record_id DESC)`、`(scope, state, started_at DESC, record_id DESC)`；事件按 `(scope, record_id, sequence)` 读取。任何状态变更在同一事务内更新 record、将 `sequence + 1` 的事件追加并更新时间。相同 attempt 的 `CreateRunning` 返回既有 handle 而不重复事件；所有后续 transition 以 record id、scope 和期望前态 compare-and-set，重复调用返回已达成状态，非法倒退失败。详情按 sequence 升序返回事件，故“完整状态演进”不依赖会被覆盖的备注列。

业务项只投影已 claim work 的 `entity_type` 和 `entity_id`（如“任务 #1716”），不保存标题或 context。token 只作为 SHA-256 指纹参与唯一约束，绝不进入事件、DTO、页面或日志。

服务端新增 `worker_work_result_recoveries`（名称可随既有迁移命名调整）的 durable row，唯一键为 `(work_id, attempt_no)`；它保存 `work_id`、attempt number、worker/Agent 标识、原 token 的 SHA-256 指纹、claim 时 `input_hash`、`reserved_at`、`expires_at`、受控状态（`reserved`、`completed`、`terminated`、`manual_reconciliation_required`）和可选 canonical-result SHA-256 指纹。它不保存 token、完整 context 或 Provider result。通过 FastAPI migration 创建该表及索引；恢复 row 与 `WorkerWork` 在同一服务端事务内受行锁和条件更新保护。

## 3. 状态机、故障窗口和恢复对账

状态为 `running`、`result_pending_delivery`、`succeeded`、`failed`、`interrupted`；交付状态为 `not_applicable`、`pending`、`confirmed`。事件代码是受控枚举：`created`、`recovery_reserved`、`journal_result_saved`、`completion_confirmed`、`provider_failed`、`validation_failed`、`explicit_fail_confirmed`、`recovered_interrupted`、`pending_reconciled`、`completion_reconciled`、`recovery_expired_manual`，页面以中文静态映射显示，不能写任意备注。

| 时点/故障 | Worker 行为 | 历史事实与后续恢复 |
| --- | --- | --- |
| claim 已接受，Provider 前 | 先以未过期 lease 调用 `ReserveResultRecovery`，再 `CreateRunning`；二者任一失败均不调用 Provider。创建本地记录失败时，Worker 在 live lease 内调用既有 `/fail`；无法送达则保留 reservation，等待恢复或人工处置。 | reservation 和 record 成功后才是 `running`；无 record 时只安全本地诊断，不能把 reservation 当作已执行结果。 |
| Provider/解析/校验失败 | 先 `MarkFailed`，成功后调用既有 `/fail`；失败落库失败时不发送 `/fail`，保留 journal/claim 供重试并记录诊断。 | `failed/not_applicable` 有追加事件；不可持久化时不会伪造成功。 |
| `WorkJournal.Save(raw)` 成功，`MarkPending` 失败 | 不发布设计文档、不 `/complete`，保留 journal 原始结果，作为可重试的本地历史故障退出。 | 下次以同 token 的 reservation-aware replay 先由 journal 原始结果生成严格投影并幂等 `EnsurePending`；不得调用 Provider。 |
| pending 成功，`/complete` 传输失败或 lease 已过期 | 保持 pending、journal 和 reservation；抛出以走现有 requeue/replay。 | lease 仍有效时只补发布（如适用）和 `/complete`；过期时按同一 token 转 `/recover-result`，不请求新 token、不重调 Provider。 |
| `/complete` 2xx，`MarkSucceeded` 失败 | 不调用 `/fail`、不删除 journal；在当前 Worker 作有界退避重试。仍失败则安全日志并结束本次消息。 | 启动/领取恢复用 journal identity 查询 Worker 专用工作状态；已完成则幂等 `MarkSucceeded`，仍活动则保持 pending。此对账不调用 Provider。 |
| 启动时遗留 `running` | 取得进程锁后、消费前 `RecoverRunning(scope)`。 | 原子转 `interrupted/not_applicable` 并追加 `recovered_interrupted`；若同 attempt journal 有 result，`EnsurePending` 后只补交付。无 result 留在 reservation 保留期内，不签发新 token 重跑；期满后转人工处置并记录 `recovery_expired_manual`。 |

`ReconcilePendingAndJournal(scope)` 仅在 Worker 持有进程锁、初始化成功后以及正常 replay 中运行。它只枚举本机 journal/历史 identity；必要时查询工作状态，并按 reservation 执行 live `/complete` 或 expired-fence `/recover-result`。服务器已完成才写 `succeeded/confirmed`，服务器明确终结或保留期进入人工处置才写 `failed/not_applicable`，未知/传输失败保持 pending 并稍后重试。它不访问页面请求路径、不会删除成功 journal、不会把 record 的展示结果作为原始结果来源，也不会创建 Provider。

### 3.1 服务端 result-recovery reservation/recover-result 契约

`POST /api/worker-work/{workId}/reserve-result-recovery` 接受现有 `Claim` 身份（project/kind/worker/agent/token），复用认证、project/Agent 归属、消息 scope 和 live `fenced()` 条件。它仅在当前 lease 未过期、`WorkerWork` 尚未终结、输入 hash 未变化时创建或幂等返回该 attempt 的 reservation；返回受控状态与 `expiresAt`，不返回 token/hash。Worker 必须在该调用和 `CreateRunning` 都成功后才可创建 adapter。重复 reserve 不延长保留期；服务端配置固定、有限的保留时长，不能由客户端指定。

保留存在时，`claim` 必须在现有 `attempts >= 3` 终结和 `new_token_required` 分支**之前**检查它，对相同或不同 token 的过期 attempt 一律返回明确的 `result_recovery_required`，不得覆盖 token、递增 attempts 或重新 offer。普通未保留 attempt 的 `new_token_required` 行为不变。已保留 attempt 的显式 `/fail` 仅在 live fence 下可终结 reservation；成功 `/complete` 将它标为 `completed`。保留期到达时后台/惰性清理原子将 reservation 标为 `manual_reconciliation_required`，将 work 置为明确的人工对账失败终态；它不自动发布新 offer 或重置业务状态。

`POST /api/worker-work/{workId}/recover-result` 接受现有 `Completion`。它不采用普通 `fenced()` 的未过期 lease 条件，却必须同时验证认证、project/kind、原 `worker_id`、Agent、token 指纹、attempt number、未过期 reservation、原 `input_hash` 与当前业务前置条件。服务端将 canonical（`sort_keys`、UTF-8）result SHA-256 与 reservation 比较/首次原子记录，再复用与 `/complete` 相同的 result size、schema、业务 mutation 和幂等规则；不得复制第二套 result-application 逻辑。相同 reservation/result 反复调用返回 `completed`；摘要不同或过期/终结/业务输入变化返回不泄露 token 的 409，且不得写入业务结果。该端点仅供持锁 Worker 的 journal replay 调用，portal GET 永不调用。

本机 `WorkJournal` 需要以 `work_id`、token 和 reservation-aware 清理状态保存原始 result；现有主键可保留，因为一个 work 同时只能有一个 active/reserved attempt。收到 `result_recovery_required` 时不得 `Remove(workId)`，而是保留同 token 的 entry 并调用 `/recover-result`；只有 server 返回 `completed`、明确 `terminated` 或 `manual_reconciliation_required` 后才按结果更新本地历史及执行既有/新增的安全清理。这个有意的 at-most-once 边界意味着 reserve 后、Provider 前崩溃也不能自动换 token 重跑；结果未知时宁可人工处置。

design work 在 live `/complete` 或 `/recover-result` 前仍必须执行既有 `DesignDocumentPublisher`，并在发布后把 `design_document_id` 写回 journal；它已有基于 `workId + frozen content digest` 的检索标记，重放必须复用该标记而不创建重复文档或覆盖人工编辑。恢复协作只改变最后的结果交付 fence，不得跳过设计文档发布/回读，且不得把发布内容复制进本机历史投影。

## 4. 严格可展示投影与脱敏边界

Provider `OutputJson` 是不可信任任意 JSON。`WorkRecordProjection` 必须先完成已有业务校验，再按 kind 读取下表的**唯一允许字段**；任何未知字段、对象、数组、`summary`、`spec`、`content`、`artifacts`、`test_steps`、`test_results`、`evidence`、`defects` 文本、URL、prompt 或 context 一律不持久化。缺失或类型/值不合格时结果详情退化为固定“已取得结构化结果，等待/完成交付”，不得复制 raw JSON。

| work kind | 可持久化/返回的结果投影 | 禁止内容 |
| --- | --- | --- |
| `proposal` | `decision` 仅 `ask`/`finalize`；`create_ticket` 布尔值 | `spec`、ticket plan、问题/摘要文本。 |
| `design` | `commit` 仅 40/64 位十六进制；`design_document_id` 仅正整数 | 文档标题、内容、URL、artifacts、summary。 |
| `design_review` | 非 discussion 为 `approve`/`discuss`；discussion 作者为 `respond` 加 `position=agree/disagree/clarify`；discussion 评审方为 `confirm`/`withdraw`/`discuss`/`escalate` | findings、evidence、summary、消息文本。 |
| `dev` | `commit` 仅 40/64 位十六进制 | summary、测试和任意实现输出。 |
| `dev_review` | 非 discussion 为 `approve`/`discuss`；discussion 作者为 `respond` 加 `position=agree/disagree/clarify`；discussion 评审方为 `confirm`/`withdraw`/`discuss`/`escalate` | findings、evidence、summary、消息文本。 |
| `qa` | `tests_passed` 布尔；`defect_count` 为已验证 defects 数量，最大 100 | defects 文本、部署/测试步骤和结果。 |
| `qa_review` | 非 discussion 为 `approve`/`discuss`；discussion 作者为 `respond` 加 `position=agree/disagree/clarify`；discussion 评审方为 `confirm`/`withdraw`/`discuss`/`escalate` | findings、证据、QA follow-up、消息文本。 |

discussion 与普通 review 的投影分支由 accepted work 的 `discussion_id` 和服务器回传 turn 决定，不能相信 Provider 自报类型。允许时只保存决策、作者 `position`、受控 `subject=review_findings/qa_defects` 与固定中文句式；`respond`、`confirm`、`withdraw`、`discuss`、`escalate` 必须可被准确显示。详情只是上述受控标量的标签化展示。失败摘要/详情不复制 `Exception.Message`、provider `ErrorMessage`、HTTP body 或堆栈，而是 `ProviderFailed`、`OutputMissing`、`OutputInvalid`、`JournalSaveFailed`、`HistoryWriteFailed`、`ReservationFailed`、`RecoveryRequired`、`RecoveryExpiredManual`、`CompletionTransport`、`CompletionRejected`、`LeaseLost`、`Cancelled`、`Unknown` 等固定代码、中文说明和 retryable 布尔值。

所有允许字符串仍经中心 `WorkRecordRedactor` 两次处理（写入前和 DTO 前）：移除 secret 键，遮盖 Bearer/token/高熵值，替换 Windows/Unix/UNC 绝对路径，标准化控制字符，并限制摘要 500 字符、结果详情 8 KiB、失败详情 4 KiB、事件说明 200 字符。投影的白名单是主要边界；正则脱敏仅为纵深防御。测试必须对每种 kind 的未知敏感字段断言不落库、不出 DTO。

## 5. 受保护的本机接口

两接口加入既有 `/api/local` group，保留 `ConfigurationPortal.IsLocalRequest` 的 loopback peer、loopback/localhost Host、空或严格同源 Origin 和 `Cache-Control: no-store`。历史 endpoints 再要求 `X-AgentBoard-Local-Portal: 1`，即使 GET 也必须有；失败统一 403、无存储信息。页面 fetch 已统一发送该 header。

### `GET /api/local/agents/{agentId}/work-records`

`agentId` 必须是当前本机配置 Agent，否则 404；删除 Agent 时历史不删除但不可通过 API 枚举，重建相同 id 后可见。服务端以当前 scope、agent id 和可选 `state` 查询。

- `pageSize`：缺省 20；可解析整数 clamp 到 1–100，非整数或超过合理参数长度返回 400。
- `state`：缺省无筛选；仅五个状态，其他值 400。
- `cursor`：最长 512 的 base64url；签名载荷含 scope、agent、state、`started_at`、`record_id` 和 identity `cursor_key` 的 HMAC。缺失可接受；解码、签名、过滤条件或 scope/agent 不匹配均 400。

返回 `{ items, nextCursor }`。`items` 含用于跳转的 `recordId`（页面默认不显示）、工作类型、业务项安全标签、状态、交付状态、开始/结束时间、耗时和受控结果或失败摘要。以 `(started_at DESC, record_id DESC)` keyset 查询，取 `pageSize + 1` 生成下一页，避免新增数据下 offset 跳项/重复。

### `GET /api/local/work-records/{recordId}`

只接受合理长度 UUID/ULID。按当前 scope 查询，且所属 agent 必须仍是当前配置 Agent；不存在、跨 scope、已移除 agent 一律 404。返回摘要、Provider/模型、work kind、业务项、时间、交付状态/时间、严格投影详情、固定失败详情和按 sequence 升序的事件。审计 ID 仅由页面在折叠区展示；token、prompt、context、路径、raw result 绝不返回。参数/存储异常映射为无路径、连接串或 secret 的 400/500 problem detail 与安全本机结构化日志；接口不使用 HTTP client、journal、Provider、`ExecutionStore` 或 `/api/executions`。

## 6. 页面与路由

每个 Agent 编辑区有四个中文标签：基本信息（ID、启用、工具、模型、CLI、超时）、任务类型（七种 work kind/职责）、提示词（通用/专属 pre/post、顺序说明）和任务记录。切换标签或 Agent 前调用 `readEditor()` 同步 DOM 至现有 `snap`；切换不保存、不重载、不变更 revision，故 draft 保留且多 Agent 隔离。

任务记录首次进入和“刷新”调用列表，显示加载、错误、空态、上一页、下一页。列表 hash 为 `#agents/<encoded-agent-id>/work-records`，详情为 `#agents/<encoded-agent-id>/work-records/<record-id>`；`hashchange` 支持直接访问、前进/后退和“返回任务记录”。编码后再验证 route 参数，所有服务器文本 HTML escape，API 错误只显示固定中文提示。状态中文映射为“执行中、结果待交付、已成功交付、执行失败、执行中断/待恢复”；默认不渲染审计 ID、凭据、提示词、完整上下文、路径或 token。保留现有 runtime 状态、先 CAS 保存后启动、重复 start gate 和 drain stop 行为。

## 7. 验收与测试

- 存储：reopen、identity/scope/agent 隔离、同 token 幂等和新 token 新 attempt；record/event 同事务、完整有序事件、CAS 不倒退、五态迁移、分页和 cursor 签名篡改。
- 故障/恢复：reserve 或 CreateRunning 失败 adapter 零调用；journal 成功/pending 失败不 complete、重启只补 pending；completion 2xx/MarkSucceeded 失败有界重试和 Worker 对账；live lease completion、expired reservation recover-result、保留期人工处置和网络超时重放均不调 Provider；running 恢复 interrupted；保持 journal success 不 Remove、普通 fail/new-token Remove 与 reservation attempt 的受控清理。
- 服务端契约：迁移、live reserve 幂等、reservation 对过期 claim 的阻断、原 worker/Agent/token/input hash 认证、recover-result 的 digest 幂等、与 complete 共享业务校验、保留期终结和无新 offer；覆盖 reserve 后 Provider 前崩溃、journal 丢失、结果不同、输入已变及 token/Agent 冒用。
- 投影与安全：每种 work kind 允许字段，以及普通 review、discussion 作者/评审方的全部合法决策；未知敏感字段、raw output、prompt/context/token、Bearer、secret、路径、控制字符和超长输入均不进入 SQLite/DTO/DOM；失败详情不含原始异常文本。
- HTTP：loopback/Host/Origin/header/no-store、非法 cursor/state/record、未知/删除 Agent、跨 scope、500 安全映射；断言 GET 未实例化 Provider、未使用 production HTTP client、journal raw result、`ExecutionStore` 或 `/api/executions`。
- DOM：四个中文标签、draft 保留、多 Agent 隔离、loading/error/empty/refresh/pagination、hash 详情/返回/前进后退、敏感内容不渲染；现有保存启动、重复启动与 drain 停止回归。
- QA 在隔离配置、临时 `HistoryDatabasePath` 和 fake provider/隔离服务中实际验证 portal/Worker；不连接或变更生产环境。真实 provider、生产部署、跨进程 crash E2E 不在本 design 的已验证范围，实施后另报。
