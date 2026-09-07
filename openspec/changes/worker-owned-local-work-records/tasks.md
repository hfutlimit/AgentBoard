# Tasks: Worker-owned 本机任务记录页面

## 1. 本机存储、事件和安全投影（Story #431）

- [ ] 新增 `LocalWorkRecordStore`、history identity、records 和追加 events 表；以 record 快照与 event append 的同事务/CAS 实现完整状态演进。
- [ ] 实现 scope 原子绑定、token SHA-256 attempt 唯一性、签名 keyset cursor、scope/Agent 查询和跨 scope fail-closed。
- [ ] 实现七种工作类型、普通 review 及 discussion 作者/评审回合的严格 allowlist 投影、固定错误代码、`WorkRecordRedactor` 脱敏和长度限制；未知 JSON 字段不写库。

## 2. 既有 Worker 生命周期中的历史写入（Story #431）

- [ ] 在 Provider 前持久化 `running/created`；写入失败时 adapter 零调用，并走既有安全失败边界。
- [ ] 接入 journal 保存后的 `result_pending_delivery`、完成确认后的 `succeeded`、明确失败后的 `failed` 及持锁启动恢复的 `interrupted`，每次转换追加受控事件并保证幂等。
- [ ] 对 pending 或 succeeded 本地历史写入失败实施有界本地重试和安全诊断；不调用 Provider、不伪称成功、不改变成功 completion 不 Remove、明确 fail/new-token 既有 cleanup 语义。
- [ ] 明确且实现“仅当前 live fence、既有身份仍有效时”可沿用现有交付路径；未知或过期结果保持 pending/interrupted，历史浏览不得触发恢复控制面。

## 3. 本机接口和配置页面（Story #431）

- [ ] 在 `/api/local` 新增两个 history GET，并额外要求 portal header；实现安全分页、状态筛选、当前 Agent/scope 校验、安全错误和 no-store。
- [ ] 实现四个中文标签、draft 同步、多 Agent 隔离、任务记录 loading/error/empty/refresh/pagination、状态中文展示与审计折叠区。
- [ ] 实现编码和验证的列表/详情 hash 路由、返回及前进后退；确保 API 错误或敏感字段不进入 DOM。

## 4. 验证（Story #431）

- [ ] 增加 storage/event/CAS/reopen/scope/attempt/cursor 与逐 work kind 投影/泄露边界测试。
- [ ] 增加 Worker 生命周期测试：CreateRunning 失败零 Provider 调用、明确失败、journal 后 pending 写入失败、completion 2xx 后 succeeded 写入失败、running 启动恢复 interrupted；断言不重调 Provider且不把未知交付显示成功。
- [ ] 增加本机 HTTP 安全和 DOM 路由/交互测试；在临时配置、临时 `HistoryDatabasePath`、fake Provider 和隔离服务上运行 focused tests。QA 另行验证 portal/Worker 启停、记录、详情、分页与 drain，不连接生产环境。

## 5. 后续依赖（Story #432，非本 change 实施项）

- [ ] 设计并实现独立托管、固定运行环境与领取前预检。
- [ ] 设计并实现跨租约/跨身份的结果 reservation/recover-result、服务端契约和可审计重试。
- [ ] 在候选枚举和任何 `WorkJournal.Save` 前保护 reservation-active 结果，防止新 token、claim、offer、Provider 调用或覆盖；支持原身份纯补交付及明确人工对账。
- [ ] 验证 lease expiry + completion unknown、原 Agent 删除/停用/工作类型取消/同 ID 重建、Journal 不覆盖、零 Provider 调用和无新 claim/offer。
