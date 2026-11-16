# 实现清单

按 TDD 推进：每项先写失败测试、看它失败、再写最小实现。

## 1. 凭据脱敏（独立先行，不依赖 provider）

- [x] 1.1 写失败测试：stdout 含 `pt-` 长 token 时 `RedactedOutput` 不含原文且含 `***REDACTED***`（RED：`Assert.DoesNotContain() Failure: Sub-string found`）
- [x] 1.2 在 `ProcessExecutor.Redactors` 新增一条规则（不动既有两条）
- [x] 1.3 回归既有脱敏测试
- [x] 1.4 追加词边界：`(?<![A-Za-z0-9_])pt-` 防止 `except-<长串>` 被误脱敏（先写失败测试证实误报真实存在，再收紧）

## 2. 结果信封提取器共用

- [x] 2.1 `TryExtractCursorJson` → `TryExtractResultEnvelopeJson`，更新 `CursorAdapter.cs:73` 唯一调用点（纯重构，绿灯下进行）
- [x] 2.2 回归 `CursorAdapterTests` 4/4 通过
- [x] 2.3 真实信封提取断言落在 `QoderAdapterTests`（见 4.x），用 v1.1.86 实测 payload 做夹具

## 3. CliLocator

- [x] 3.1 写失败测试：`LocateQoder` 尊重配置的绝对路径（RED：CS0117 方法不存在）
- [x] 3.2 实现 `LocateQoder`（已知路径 → npm-global → where），入口为 `.exe`
- [x] 3.3 已知路径探测（`Command` 为空）由真实 harness 验证：`LOCATOR_SOURCE=known-path:…\qoderclicn.exe`

## 4. QoderAdapter

- [x] 4.1 默认无头参数 + stdin prompt + `--model` 注入 + WorkingDirectory/MaxOutputBytes/Timeout 透传
- [x] 4.2 `is_error:true` 且 exit=0 时 `Success == false`
- [x] 4.3 env 不含任何 Desktop SDK 污染变量（边界守卫，构造即成立；另有真实 harness 反证）
- [x] 4.4 PAT 经 `ApiKeyEnv` 转发
- [x] 4.5 未配 `ApiKeyEnv` 时也转发 `QODERCN_PERSONAL_ACCESS_TOKEN`（无人值守可用）
- [x] 4.6 固定携带 `--strict-mcp-config` 与空 `--mcp-config`（含在 4.1 参数断言中）
- [x] 4.7 配置 `Arguments` 时覆盖默认参数
- [x] 4.8 `QODERCN_CONFIG_DIR` 转发
- [x] 4.9 真实 v1.1.86 信封提取出内层业务 JSON

## 5. 配置与注册

- [x] 5.1 `Models("qoder")` 返回 12 个官方模型名，断言不含 `qoder-custom-` 个人 BYOK ID
- [x] 5.2 `Create` 产出 command=`qoderclicn`；默认参数复用 `QoderAdapter.HeadlessArguments` 单一事实来源
- [x] 5.3 qoder profile 通过 `ValidateConfiguration`（Theory 新增两行）
- [x] 5.4 `AgentsOptions.Qoder` 槽位、Catalog 三处分支、白名单、`LocalAdapterFactory` 分支
- [x] 5.5 `Program.cs` DI 注册 `QoderAdapter`
- [x] 5.6 `ReadinessProbe` 两处分支补 qoder（探活用通用 `--version`，实测支持）
- [x] 5.7 `WorkerStartupService`：instance upsert 槽位 + `RequireRegistration` 白名单补 qoder（`3136720`）；新增「只配 qoder 也能启动并心跳」测试，断言注册 / instance / heartbeat 三段 URL、payload 与 Bearer 头
- [x] 5.8 三份 appsettings 补 Qoder 槽位（默认停用，字段与 Cursor 同构）（`3136720`）
- [x] 5.9 服务端 `EXECUTOR_TYPES` 补 qoder（`3136720`）——`upsert_agent_instance` 确有枚举校验，原 proposal「无校验」断言错误，已在 proposal 修正
- [x] 5.10 服务端 `_SUPPORTED_PROVIDERS` 补 qoder（`3136720`），与 `EXECUTOR_TYPES` 保持一致；.NET `DatabaseAgentSelector` 已是薄 HTTP 传输，Python 是 provider 解析唯一事实来源

## 6. 配置门户

- [x] 6.1 执行工具下拉（新增对话框 + 两处 renderEditor）补 qoder
- [x] 6.2 `models` 表补 12 个官方模型；`modelLabels` 无需新增（Qoder 用显示名，本身可读）
- [x] 6.3 provider 切换处理器的 command / arguments 映射补 qoder
- [x] 6.4 脚本核对：JS 语法通过、模型表与 C# 逐项相等、参数表与 adapter 常量逐项相等、无残留三provider列表、无 BYOK 泄露

## 7. 验证与收尾

- [x] 7.1 `dotnet test` 全量 **374 passed / 0 failed**（基线 360 + 新增 14）
- [x] 7.2 真实链路 harness（`D:\tmp\Debug\QoderHarness`，一次性、不进仓库）：父进程被 SDK 变量污染的前提下，locator 命中已知路径 → 真实 CLI 执行 → `SUCCESS=True` / `EXIT=0` / `OUTPUT_JSON={"decision":"submit","summary":"harness write done"}` / 文件真实写入 `HARNESS_OK`，耗时 22.4s
- [ ] 7.3 服务端在环验收：门户建 qoder 实例 → 映射项目 → 启用 → 真实领一个 work kind → `/complete` 成功且本地历史 succeeded（需 AgentBoard Server + RabbitMQ 在环，尚未执行）
- [ ] 7.4 归档变更至 `openspec/changes/archive/` 并同步 `openspec/specs/`（待 7.3 完成）
- [x] 7.5 提交并推送：`ff46687` → `origin/main`（18 文件，745 增 14 删）

## 8. 审查收口（2026-11-16 双轴审查后）

- [x] 8.1 修正 proposal 的错误事实与范围声明：注册端有 `EXECUTOR_TYPES` 校验（一次性探针实测 qoder 通过 / 未知值被拒），服务端两处白名单纳入范围，影响范围表与「改什么」清单同步
- [x] 8.2 补服务端 provider 契约回归 `tests/unit/test_executor_provider_contract.py`（11 passed）：四家 CLI 注册被接受、未知 executor 被拒、durable roles fallback 与 executor_type 直传、两个白名单同步。临时摘掉 qoder 实测 3 条失败，证明守卫非空（`533dc70`）
- [x] 8.3 fail-fast 的白名单 / 诊断 / 补救提示同源于一张表，补「四个工具逐个诊断 + 示例命令」测试；`WorkerStartupServiceTests` 13 passed（`fd28009`）
- [x] 8.4 复核并撤回两条不成立的风险，按「只修确认项」不改代码：
  - .NET presence 耦合导致心跳停摆——`LocalWorkConfiguration.Validate()` 禁止 `Projects` 为空、`ExecuteAsync` 要求非空 `StartupToken`、preflight 还索引 `Projects[0]`，且全仓唯一运行时调用方就是它，场景不可达
  - `_caller_uid_admin` 漏校验 `revoked_at`——`revoke_api_key` 同时置 `enabled=False`，应用内造不出「enabled 且已撤销」的行；改为修正 `revoke_api_key` 里「lookup 已过滤 enabled」的假陈述，并补 admin API key 回归（已撤销→401、有效→200）（`67bc427`）
- [x] 8.5 同源竞态收口：`scan_stale_agent_runs` 的 `task_id is None` 分支此前直接 ORM 改 `failed`，抽出 `_cas_fail_stale_run` 供两条回收路径共用，补 3 条测试（`6521182`）
- [x] 8.6 声明端点守卫去重：抽出 `write_actor` / `authorize_member`，凭据每请求只解析一次（3 项目声明实测 5 次 → 2 次，含审计中间件那一次），docstring 补 401/409 契约（`f200d2b`）
- [ ] 8.7 7.3 服务端在环验收仍未执行（需 AgentBoard Server + RabbitMQ 在环），故 7.4 归档继续等待

## 附：发现的既有 flaky 测试（非本次回归，未修改）

全量回归中偶发两个失败，均已定位为既有缺陷：

1. `LocalWorkRecordStoreTests.Pagination_cursor_is_signed_and_recovery_is_honest`
   - 根因：测试用 `NextCursor[..^1] + "x"` 制造非法游标，但 `EncodeCursor` 输出 base64url，**当末字符本来就是 `x` 时该操作是空操作**，签名依然合法，`Assert.Throws<ArgumentException>` 落空。
   - 证据：`--no-build` + 过滤器**隔离运行 25 次，24 通过 1 失败**（本次新增测试完全不参与、无并行负载）。
2. `Sprint1_StateMachineTests.Terminal_branch_writes_correct_status(TimedOut)`
   - 根因：`ExecutionCoordinator` 对 SQLite BUSY/LOCKED 有限重试（约 1.5s）后落 `Degraded`，并行磁盘争用下可能取代 `TimedOut`。连接串 `DefaultTimeout = 1`。
   - 证据：隔离运行通过；后续 5 轮全量未复现。

按"不做无关修复"原则本次不改，留待独立变更处理。

3. `tests/e2e/happy_path/test_worker_owned_work.py`（Python 侧，2026-11-16 审查收口时发现）
   - 现象：整文件运行时每轮失败的用例都不同（`test_failed_qa_creates_new_bugs_then_independent_retest_atomically`、`test_discussion_retry_expiry_and_round_limit_escalate_without_rework`、`test_expired_third_attempt_becomes_terminal_and_retains_history`、`test_qa_is_independent_task_with_its_own_review_and_steps` 等），单条隔离运行通过。
   - 证据：同一文件在 `worker_work.py` 的 HEAD 版本与重构版本上**各跑 3 轮**——HEAD 为 2 失败 / 1 失败 / 23 全通过，重构版为 1 / 2 / 2 失败，且同名用例在两侧都失败过。故与本轮重构无关，属既有非确定性。
   - 未定位根因（涉及租约到期与讨论轮次的时间判定），同样留待独立变更。