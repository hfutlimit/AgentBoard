# Tasks: Worker-owned 本机任务记录页面

## 1. 存储、事件与安全投影

- [ ] 新增 `LocalWorkRecordStore`、history identity、records 和追加 events 表；以 record 快照与 event append 的同事务/CAS 实现完整状态演进。
- [ ] 实现 scope 原子绑定、token SHA-256 attempt 唯一性、签名 keyset cursor、scope/agent 查询和跨 scope fail-closed。
- [ ] 实现七种 work kind 的严格 allowlist 投影、固定错误代码、`WorkRecordRedactor` 纵深脱敏及长度限制；未知 JSON 字段不写库。

## 2. 服务端恢复契约、Worker 生命周期与兼容

- [ ] 为 `WorkerWork` 增加 migration 和原 attempt fence 绑定的 result-recovery reservation；实现 live-fence reserve、过期 claim 阻断、有限保留期人工处置和无自动新 offer。
- [ ] 实现认证/worker/Agent/token hash/attempt/input hash 绑定的 `recover-result`，与 `/complete` 共用结果校验、业务 mutation 和 canonical-result digest 幂等逻辑；不得复制或绕开 complete 的业务规则。
- [ ] claim 接受并 reserve 后、Provider 前 durable CreateRunning；reserve 或写入失败时 adapter 零调用，按既有 fail/retry 边界安全退出。
- [ ] 接入 journal-save 后 pending、completion 后 success、已知失败后 failed；每个变更追加 event 并保证重试幂等。
- [ ] 实现持锁启动恢复和 reservation-aware journal/history 对账：running 转 interrupted，journal 有结果只补 pending/发布/live completion 或 recover-result，不重调 Provider；处理 pending 落库和 completion-success 落库失败窗口。
- [ ] 调整 Journal 清理分支：成功 completion 不 Remove；普通 attempt 保持明确 fail/new-token Remove；reservation attempt 收到 `result_recovery_required` 不 Remove，仅在 server completed/terminated/manual 结果后安全处理，不改队列 ACK 和 drain。

## 3. 本机接口与页面

- [ ] 在 `/api/local` 添加两个 history GET，并额外要求 portal header；实现安全分页、状态筛选、当前 Agent/ scope 校验、安全错误与 no-store。
- [ ] 实现四中文标签、draft 同步、多 Agent 隔离、任务记录 loading/error/empty/refresh/分页、状态中文显示与审计折叠区。
- [ ] 实现编码校验的列表/详情 hash 路由、返回和前进后退；确保不把 API 错误或敏感字段插入 DOM。

## 4. 验证

- [ ] 增加存储/事件/CAS/reopen/scope/attempt/cursor 及每个 work kind 投影/泄露边界测试。
- [ ] 增加服务端和 Worker 故障窗口测试：reserve/CreateRunning 失败、reserve 后 Provider 前 crash、journal/pending 失败、completion 成功后 success 写入失败、lease 到期 recover-result、结果/身份/hash 不匹配、保留期人工处置、timeout replay、interrupted 恢复及 Journal 清理兼容。
- [ ] 增加本机 HTTP 安全和 DOM 路由/交互测试；在临时配置与数据库运行 focused tests。QA 独立验证隔离 portal/Worker，不连接生产环境。
