# 技术方案：qoder worker provider

## 1. 探针实测证据

全部实测于 Qoder CN CLI **v1.1.86**，Windows x64，入口
`C:\Users\jason\.qoder-cn\bin\qoderclicn\qoderclicn.exe`。
探针在 `D:\tmp\Debug\qoder-probe` 一次性目录进行，产物不进仓库。

| 项 | 实测结果 |
|---|---|
| 认证 | `QODERCN_PERSONAL_ACCESS_TOKEN` 有效（`Login Method: token` / `Auth Source: external`）。`QODER_PERSONAL_ACCESS_TOKEN`、`QODER_PAT`、`QODER_SDK_ACCESS_TOKEN` **均无效** |
| 成功信封 | exit=0，stdout 单个 JSON 对象，stderr 空。字段：`type:"result"` / `subtype:"success"` / `is_error:false` / `num_turns` / `result`(字符串) / `stop_reason` / `duration_ms` / `usage` / `session_id` |
| 业务结果位置 | `result` 字符串内嵌业务 JSON。实测 `result == "{\"result_status\":\"succeeded\",\"summary\":\"probe write done\"}"` |
| 失败语义 | 未登录：exit=1，stdout 0 字节，stderr 824 字节 `NonInteractiveAuthRequiredError`。SDK 污染：exit=42，stderr `sdk_worker_removed` |
| stdin | prompt 经 stdin 传入可用（无 positional query），exit=0 |
| 模型 | `--model GLM-5.3` 用**显示名**直接生效 |
| 写文件 | `--permission-mode bypass_permissions` 下真实创建文件成功，`permission_denials: []` |
| MCP 空配置 | `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` 接受，exit=0 |
| CLI 定位 | PATH 中**不存在**（用户 PATH 超 2047 字符，安装器无法写入），必须探已知安装路径 |

### 1.1 环境隔离是硬约束

从 Qoder Desktop agent 会话继承的下列变量会让 CLI 误判自己是被 Desktop 拉起的 SDK worker，直接 exit=42：

```
QODERCLI_RUNTIME_PACKAGING=worker_mjs
QODER_AGENT_SDK_ENTRYPOINT=sdk-ts
QODER_AGENT_SDK_DAEMON=1
QODERCN_SESSION_TYPE=app
QODER_SDK_AUTH_PAYLOAD_FILE=<临时 payload.json>
```

`ProcessExecutor` 已 `start.Environment.Clear()`（`IProcessExecutor.cs:50`），只复制 spec 显式列出的变量，因此生产天然安全。**adapter 的 allow-list 绝不能收录上述任何变量**，并需有测试守住这条边界。

### 1.2 与 Desktop 授权不共享

Desktop 凭据在 `AppData/Roaming/com.qodercn.app.stable/auth.v1.dat`（`v10` 前缀 = Electron safeStorage 加密）。CLI 二进制中不含 `auth.v1.dat` / `safeStorage`，其凭据库是 `~/.qoder-cn/.auth`。两者格式与加密方式不同，无共享路径。CLI 需独立认证。

## 2. 关键决策

### 2.1 MCP 零加载

`WorkPlanner.Prompt`（`WorkPlanner.cs:41`）明文规定：

> Do not call AgentBoard mutation APIs or MCP tools, claim tasks, change statuses, or choose another Agent. The Worker owns orchestration and will persist your structured result with its fenced lease.

WorkerOwned 的 provider 只返回结构化 JSON，由 Worker 用 fenced lease 写状态。因此：

- 固定传 `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`，加载零个 MCP server；
- **不调用** `ApplyAgentBoardIdentity`。`LocalAdapterFactory` 本就把 provider 的 `AgentBoardToken` 清空；
- 不沿用操作者个人 `settings.json` 里的 MCP（其中 bitbucket 需 npx 下载、localmcptools 需 Python venv，是纯启动开销与故障点，零收益）。

这使 QoderAdapter 比 Codex / Cursor adapter 更简单。

### 2.2 成功判定比 Cursor 多一道闸

Cursor 只看 `ExitCode == 0`。Qoder 官方文档明确警告零退出码不等于成功，故判定为：

```
ExitCode == 0 && !TimedOut && !Cancelled && 信封 is_error == false
```

`is_error` 缺失时视为 false（兼容只含 `result` 的精简信封，与 Cursor 现有测试夹具一致）。

### 2.3 结果信封提取器共用

实测 Qoder 与 Cursor 的信封结构一致（顶层对象 → `result` 字符串 → 内层业务 JSON）。将 `TryExtractCursorJson` 重命名为 provider 中立名 `TryExtractResultEnvelopeJson`，两家共用，避免重复逻辑。

改名安全性已核实：全仓库仅 2 处引用（定义 + `CursorAdapter.cs:73`），无测试直接引用该方法名。属纯重命名，不改变行为。

### 2.4 凭据

- 变量名 `QODERCN_PERSONAL_ACCESS_TOKEN`，经既有 `AgentOptions.ApiKeyEnv` 转发（与 Codex / Cursor 同机制）；
- 不写入任何配置文件——`LocalConfigurationStore.Save` 本就拒绝（`LocalConfigurationStore.cs:47`）；
- 另转发 `QODERCN_CONFIG_DIR`（若操作者设置），用于自定义凭据根。

### 2.5 PAT 脱敏

`ProcessExecutor.Redactors` 现有两条规则覆盖 `(openai|anthropic|minimax|codex)_api_key` 与 `Authorization: Bearer`，**不覆盖** `pt-` 形式的 Qoder PAT。引入新凭据格式必须同时补规则，否则 CLI 一旦回显就会原样进入 `RedactedOutput` → 本地历史 DB + 执行日志。

新增一条匹配 `pt-` 前缀长 token 的规则，替换为既有 `***REDACTED***`。不改动现有两条规则。

### 2.6 模型表

`--list-models` 实测返回 13 项，其中 `gpt-6.1-sol (qoder-custom-cafc3c61-…)` 是**操作者个人 BYOK custom provider ID**，禁止写入仓库：既泄露个人配置，对其他人也无效。

只收录官方模型名。`Auto` / `Fast` 是伪模型（路由档位而非具体模型），收录并置于列表首位作为默认。

模型目录由 Qoder 服务端可变，硬编码会随上下架失效——这是**已知取舍**。复用门户 `modelOptions` 既有降级分支：当前值不在列表时显示"当前值：X（请选择模型）"并要求重选，不静默失败。

### 2.7 CLI 入口指向 .exe

`ProcessExecutor.NeedsCmdWrapper` 会把 `.cmd` 包进 `cmd.exe`，而 `qodercn.cmd` dispatcher 还要再拉一层 powershell。已知路径探测直接命中 `qoderclicn.exe`，避免两层额外进程与 PATH 依赖。

## 3. 风险边界

`--permission-mode bypass_permissions` 意味着本地**没有**权限护栏，与 Codex 的 unattended bypass、Cursor 的 `--force --trust` 同构。约束来自两层：

1. `WorkPlanner.Prompt` 的协议文本（禁止生产部署、删数据、推远端）；
2. `WorkerOwnedService.cs:495-499` 的 git HEAD + `status --porcelain` 前后比对——只读工作类型若改动仓库直接抛错，design/dev 若留下未提交改动同样抛错。

不引入沙箱。这是沿用既有无人值守取舍，不是本变更新增的风险面。

## 4. 验收

### 4.1 自动化

- QoderAdapter：默认无头参数、stdin 传 prompt、`--model` 注入、配置参数覆盖、信封提取、`is_error` 闸、env allow-list 不含 SDK 污染变量、PAT 经 `ApiKeyEnv` 转发
- CliLocator：已知路径命中
- LocalAgentCatalog：qoder 模型表、command、默认参数、模型表不含个人 BYOK ID
- 配置校验：qoder 通过白名单
- ProcessExecutor：`pt-` PAT 被脱敏，既有两条规则回归通过
- Cursor 回归：提取器改名后全部既有测试通过

### 4.2 端到端（单测与探针不足以证明集成完成）

在配置门户建一个 qoder 实例 → 映射一个项目 → 启用 → 真实领取一个 work kind → `/complete` 成功且本地历史记录为 succeeded。