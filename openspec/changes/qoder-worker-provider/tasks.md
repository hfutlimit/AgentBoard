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
- [ ] 7.5 提交并推送（待用户确认）

## 附：发现的既有 flaky 测试（非本次回归，未修改）

全量回归中偶发两个失败，均已定位为既有缺陷：

1. `LocalWorkRecordStoreTests.Pagination_cursor_is_signed_and_recovery_is_honest`
   - 根因：测试用 `NextCursor[..^1] + "x"` 制造非法游标，但 `EncodeCursor` 输出 base64url，**当末字符本来就是 `x` 时该操作是空操作**，签名依然合法，`Assert.Throws<ArgumentException>` 落空。
   - 证据：`--no-build` + 过滤器**隔离运行 25 次，24 通过 1 失败**（本次新增测试完全不参与、无并行负载）。
2. `Sprint1_StateMachineTests.Terminal_branch_writes_correct_status(TimedOut)`
   - 根因：`ExecutionCoordinator` 对 SQLite BUSY/LOCKED 有限重试（约 1.5s）后落 `Degraded`，并行磁盘争用下可能取代 `TimedOut`。连接串 `DefaultTimeout = 1`。
   - 证据：隔离运行通过；后续 5 轮全量未复现。

按"不做无关修复"原则本次不改，留待独立变更处理。