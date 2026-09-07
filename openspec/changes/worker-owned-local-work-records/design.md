# Design: Worker-owned 本机任务记录页面

## 1. 范围、决策和不变量

本设计实现 Story #431 / Task #1716 的本机配置和历史浏览范围。每条记录代表当前 Worker-owned 实例的一次已知物理执行 attempt；它不是服务端 AgentRun，也不读取旧 `ExecutionStore`、`/api/executions`、其他 Worker/Server scope 或生产 API 数据。

1. `LocalWorkRecordStore` 使用 `HistoryDatabasePath` 的 SQLite 文件，但表和生命周期独立于 `worker_owned_journal`。历史只保存安全展示投影；接口绝不读取 journal 原始结果。
2. `scope = canonical-server-origin + "|" + worker_id`。identity 首次写入原子绑定 scope；scope 不符、数据库损坏或必要历史写入失败 fail-closed，不删除或重建数据库。
3. 实际 Provider 调用前，`running` 快照及 `created` 事件必须在同一事务提交。失败时不得创建 adapter 或调用 Provider。
4. 每次状态变更都在同一 SQLite 事务更新快照、追加顺序事件并更新时间；重复调用幂等、非法倒退拒绝。详情按 sequence 升序返回完整事件序列。
5. 历史 GET 仅查询本机 history store：不得调用 Provider、Server API、journal replay、发布或运行控制。恢复和交付仍仅由持有 Worker 进程锁的现有服务路径执行。
6. 保留现有 revision-CAS、单实例 gate 及 drain stop：停止只停止领取新项并等待当前调用结束；顶部状态唯一来自 `LocalWorkerRuntime.StatusAsync`。

## 2. 存储、事件与状态机

```text
claim accepted
  -> CreateRunning + created (atomic) -> Provider
  -> WorkJournal.Save(raw result) -> MarkPending + journal_result_saved (atomic)
  -> existing fenced /complete -> MarkSucceeded + completion_confirmed (atomic)
  \-> provider / validation / explicit fail -> MarkFailed + fixed failure event

startup, process lock held
  -> RecoverRunning -> MarkInterrupted + recovered_interrupted

portal GET -> LocalWorkRecordStore (scope + configured Agent) -> redacted DTO
```

`LocalWorkRecordStore` 初始化使用 WAL 与 `synchronous=FULL`，创建：

| 表 | 关键列和约束 | 用途 |
| --- | --- | --- |
| `worker_owned_work_record_identity` | `singleton=1`、`scope`、随机 32-byte `cursor_key` | 原子 scope 绑定及 HMAC cursor；key 永不返回或记录。 |
| `worker_owned_work_records` | 随机 `record_id` PK、scope、work ID、token SHA-256 指纹、Agent/provider/model/kind、安全业务项投影、状态/交付状态/时间/受控摘要；`UNIQUE(scope, work_id, claim_token_fingerprint)` | attempt 当前快照。 |
| `worker_owned_work_record_events` | scope、record ID、sequence、occurred_at、state、delivery_state、`event_code`；`UNIQUE(scope, record_id, sequence)` | 只追加的状态事实。 |

至少索引 `(scope, agent_id, started_at DESC, record_id DESC)`、`(scope, state, started_at DESC, record_id DESC)` 和 `(scope, record_id, sequence)`。业务项只投影 claim work 的 `entity_type` 与 `entity_id`（例如“任务 #1716”），不保存标题或 context。token 仅保存 SHA-256 指纹，绝不进入日志、事件、DTO 或页面。

状态为 `running`、`result_pending_delivery`、`succeeded`、`failed`、`interrupted`；交付状态为 `not_applicable`、`pending`、`confirmed`。受控事件码至少为 `created`、`journal_result_saved`、`completion_confirmed`、`provider_failed`、`validation_failed`、`explicit_fail_confirmed`、`recovered_interrupted`、`history_write_failed`、`completion_known_history_pending`。页面对这些枚举作固定中文映射，不能持久化或展示任意备注。

## 3. 现有运行边界、故障窗口与诚实展示

| 时点 | #431 实现行为 | 历史事实和展示 |
| --- | --- | --- |
| claim 已接受、Provider 前 | `CreateRunning` 成功后才创建 adapter；失败时不调用 Provider。 | `running/not_applicable`；无记录时只记安全本机诊断。 |
| Provider、解析或校验明确失败 | 先 `MarkFailed`，成功后走现有 `/fail`；不能持久化时不伪造完成。 | `failed/not_applicable` 与固定错误码。 |
| journal 保存成功、`MarkPending` 失败 | 不发送 `/complete`；当前 live lease 内可有界重试历史写入。仍失败则保留现有 journal、停止本次交付，绝不重调 Provider。 | 已落库记录保持 `running`，下次启动转 `interrupted`；日志记录 `HistoryWriteFailed`。不能标作待交付或成功。 |
| pending 成功、`/complete` 传输结果未知 | 保留 pending 和 journal，依现有同 token/live lease 路径重试；不调 Provider。 | `result_pending_delivery/pending`，直到确有完成确认。lease 过期后的完整补交付不属于 #431。 |
| `/complete` 成功、`MarkSucceeded` 失败 | 当前进程有界重试本地状态；仍失败只记录安全诊断。后续仅可在已有安全身份和事实可确认时补写；否则不猜测。 | 未持久化确认不得显示成功；重启遗留 running 转 `interrupted`。 |
| 启动发现遗留 `running` | 持有进程锁、消费前 `RecoverRunning(scope)` 原子转 interrupted；不访问 Provider。 | `interrupted/not_applicable`、`recovered_interrupted`；原始结果、交付是否可恢复未知时不标成功。 |

现有 `WorkJournal` 的成功 `/complete` 不删除、明确 `/fail` 与 `new_token_required` 分支清理语义保持原样。本设计不修改 `WorkJournal` 主键/覆盖行为、不新增 server reservation 或 `recover-result`。因此下列窗口**未覆盖**：lease 到期且 completion 结果未知、原 Agent 被删除/停用/不再匹配、同 ID 重建、新 token/claim、以及在候选选择前覆盖同一 `work_id` journal。对这些情况，历史只保留已知状态与固定诊断，不能以自动重跑、换 token 或历史 GET 代替恢复。

Story #432 是上述完整方案的前置依赖：它必须在候选枚举和任何 Journal Save 前保护 reservation-active 结果，禁止新 token、claim、offer、Provider 调用和覆盖；原 Agent 不可用时先用原身份纯补交付，身份/input/保留期不成立才进入可审计人工对账。#431 不实现或验收该行为。

## 4. 严格投影和数据安全

Provider `OutputJson` 是任意不可信 JSON。`WorkRecordProjection` 必须先通过既有业务校验，再按下表读取**唯一允许的标量**；未知字段、对象、数组、`summary`、`spec`、`content`、`artifacts`、`test_steps`、`test_results`、`evidence`、`defects`、URL、prompt 和 context 一律不落库或返回。缺失或无效时使用固定“已取得结构化结果，待确认交付”说明，不复制 raw JSON。

| 工作类型/回合 | 允许结果投影 |
| --- | --- |
| `proposal` | `decision=ask/finalize`、`create_ticket` 布尔值。 |
| `design` | 40/64 位十六进制 `commit`、正整数 `design_document_id`。 |
| `dev` | 40/64 位十六进制 `commit`。 |
| `qa` | `tests_passed` 布尔、0–100 `defect_count`。 |
| 普通 `design_review`、`dev_review`、`qa_review` | `decision=approve/discuss`。 |
| discussion 作者回合 | `decision=respond`、`position=agree/disagree/clarify`。 |
| discussion 评审回合 | `decision=confirm/withdraw/discuss/escalate`、受控 `subject=review_findings/qa_defects`。 |

discussion 分支由 accepted work 的 `discussion_id` 与服务端回传的 turn 判定，不能相信 Provider 自报类型。结果摘要和详情只由受控标量及固定中文句式组成。失败只保存固定代码、中文说明和 `retryable`，例如 `ProviderFailed`、`OutputMissing`、`OutputInvalid`、`JournalSaveFailed`、`HistoryWriteFailed`、`CompletionTransport`、`CompletionRejected`、`LeaseLost`、`Cancelled`、`Unknown`；不得复制异常消息、HTTP body 或堆栈。

`WorkRecordRedactor` 在写入前及 DTO 输出前各执行一次：移除 secret 键、遮盖 Bearer/token/高熵值、替换 Windows/Unix/UNC 绝对路径、标准化控制字符，并限制摘要 500 字符、结果详情 8 KiB、失败详情 4 KiB、事件说明 200 字符。allowlist 是主边界，正则脱敏只是纵深防御。

## 5. 只读接口

接口置于既有 `/api/local` group，复用 `ConfigurationPortal.IsLocalRequest` 的 loopback peer、loopback/localhost Host、空或严格同源 Origin，并返回 `Cache-Control: no-store`。两个 history GET 额外强制 `X-AgentBoard-Local-Portal: 1`；失败统一 403，不泄露存储信息。

### `GET /api/local/agents/{agentId}/work-records`

只接受当前本机配置 Agent；未知或已移除 Agent 返回 404（历史不删除，同 ID 重建后可见）。按当前 scope、Agent 和可选状态查询：

- `pageSize` 默认 20，整数限制 1–100；非法或超长参数 400。
- `state` 仅接受五种状态，其他值 400。
- `cursor` 最长 512，base64url HMAC 载荷含 scope、agent、state、`started_at` 和 `record_id`；解码、签名或过滤条件不匹配为 400。

返回 `{ items, nextCursor }`。按 `(started_at DESC, record_id DESC)` keyset 取 `pageSize + 1`，摘要仅含路由用 `recordId`、工作类型、业务项安全标签、状态、交付状态、起止时间、耗时和受控摘要。

### `GET /api/local/work-records/{recordId}`

只接受合理长度 UUID/ULID，按当前 scope 查询且所属 Agent 仍为当前配置 Agent；不存在、跨 scope、已移除 Agent 都返回 404。返回安全摘要、Provider/模型、work kind、业务项、时间、交付状态/时间、严格投影详情、固定失败详情及按 sequence 升序事件。不得接受 work ID、token 或 Agent override；不得使用 HTTP client、Provider、journal raw result、`ExecutionStore` 或 `/api/executions`。参数/存储异常映射为无路径、连接串或 secret 的 400/500 与安全本机日志。

## 6. 页面和路由

每个 Agent 编辑区有“基本信息”“任务类型”“提示词”“任务记录”四个中文标签。切换标签或 Agent 前调用 `readEditor()` 将 DOM 同步进现有 `snap`；切换不保存、不重载、不修改 revision，因此草稿保留且多 Agent 隔离。

首次进入任务记录及点击刷新加载列表，展示加载、错误、空态、上一页、下一页和刷新。列表路由为 `#agents/<encoded-agent-id>/work-records`，详情为 `#agents/<encoded-agent-id>/work-records/<record-id>`；`hashchange` 支持直达、前进、后退和“返回任务记录”。编码后验证参数，所有服务器文本 HTML escape，API 错误仅显示固定中文提示。

固定状态文案为“执行中”“结果待交付”“已确认交付”“执行失败”“执行中断/待恢复”。默认列表不渲染审计 ID、凭据、提示词、完整上下文、路径或 token；详情只在折叠审计区显示 record/work ID。不得把已中断、结果未知或交付未知显示为成功。

## 7. 验收、测试与交付边界

- 存储：reopen、identity/scope/Agent 隔离、同 token 幂等/新 token 新 attempt、record/event 同事务、完整有序事件、CAS 不倒退、五态迁移、keyset 分页、cursor 篡改和状态筛选。
- 生命周期：CreateRunning 失败 adapter 零调用；Provider/校验失败；journal 成功而 pending 写入失败；completion 2xx 而成功状态写入失败；启动 running 转 interrupted。断言不重调 Provider、不伪称成功，且成功 completion 不因本功能删除 journal。
- 安全投影：七种 work kind、普通 review 和 discussion 两方全部合法决定；未知敏感字段、raw output、prompt/context/token、Bearer、secret、绝对路径、控制字符和超长输入均不进入 SQLite、DTO 或 DOM。
- HTTP/DOM：loopback/Host/Origin/header/no-store、非法 cursor/state/record、未知/删除 Agent、跨 scope、安全 500；四标签、草稿保留、多 Agent 隔离、加载/错误/空态/刷新/分页、hash 详情/返回/前进后退和敏感内容不渲染。
- 回归：隔离配置、临时 `HistoryDatabasePath`、fake Provider 和隔离服务验证 portal/Worker 启停、重复启动 gate、drain stop、记录、详情和分页；不连生产环境。
- #432 依赖验收（不计入本 change）：lease expiry + completion unknown、原 Agent 删除/停用/工作类型取消/同 ID 重建、Journal 不覆盖、零 Provider 调用、无新 claim/offer、原身份补交付和人工对账。

本 change 是设计，不实施运行时代码。真实 Provider、生产部署、跨进程崩溃 E2E，以及 #432 的恢复/防覆盖验收都必须在后续实施任务中单独报告。
