# 新增 qoder worker provider

## 为什么

WorkerOwned 执行平面目前只认 `codex` / `workbuddy` / `cursor` 三家 provider。Qoder CN CLI 已具备可用的无头契约（实测 v1.1.86），但：

- `WorkerOwnedOptions.ValidateConfiguration` 的 provider 白名单不含 `qoder`，配置门户**加不出** qoder 实例；
- `LocalAdapterFactory` 没有对应分支，即使手写配置也会抛 `Unsupported local provider`。

结果是 Qoder 无法参与 WorkerOwned 的领活与执行。前端 schedule 下拉里的 `qoder` 选项走的是另一条旧 Python webhook 链，与本变更无关。

## 改什么

新增第四家 provider `qoder`，照 Cursor 既有模式扩展：

- `Agents/QoderAdapter.cs`：无头子进程执行器
- `Agents/CliLocator.cs`：`LocateQoder` 已知路径探测
- `Agents/SharedAdapterHelpers.cs`：结果信封提取器改为 provider 中立名，Cursor 与 Qoder 共用
- `Options.cs`：`AgentsOptions.Qoder` 槽位（默认停用）
- `WorkerOwned/LocalWorkConfiguration.cs`：白名单 + 工厂分支
- `WorkerOwned/LocalAgentCatalog.cs`：command / 默认参数 / 官方模型表
- `WorkerOwned/ConfigurationPortal.html`：执行工具下拉、模型表、模型标签
- `Program.cs`：DI 注册
- `Agents/ReadinessProbe.cs`：locator 与 opts 两处分支
- `Process/IProcessExecutor.cs`：补 Qoder PAT 脱敏规则

## 不改什么（明确排除）

- **不动 `features/scheduling/router.py`**。`_SUPPORTED_PROVIDERS` 只服务旧 Durable 平面的 `/api/durable/agent-select`，且 `_provider_from` 对 `executor_type` 直接返回不查白名单；`worker_work.py` 完全不引用它。WorkerOwned 的候选来自本地 `WorkerOwnedOptions.Candidates`，注册端 `executor_type` 是无枚举校验的 string(40)。改它对本目标非必要，属范围蔓延。
- **不改变既有三家 provider 的任何行为**。提取器改名是纯重命名（仅 2 处引用，无测试直接引用该方法名）。
- **不做 provider 注册表集中化重构**。一个 provider 需改约 10 处是仓库既有结构问题（霰弹式修改），本次照模式扩展，不顺手重构。
- **不引入 per-agent MCP token 机械**。见 design.md 的 MCP 决策。

## 影响范围

| 面 | 影响 |
|---|---|
| 既有 provider | 无行为变化；提取器改名需回归 Cursor 测试 |
| 配置兼容 | 纯新增枚举值，旧配置文件继续合法 |
| 凭据 | PAT 只经环境变量，不落任何配置文件（`LocalConfigurationStore` 本就禁止） |
| 日志/历史 DB | 新增 `pt-` 脱敏规则，降低凭据泄露面 |
| 服务端 | 无改动 |

## 风险

`--permission-mode bypass_permissions` 与 Codex / Cursor 的无人值守取舍同构，本地**没有**权限护栏。只读工作类型（proposal / review / qa）依赖 `WorkerOwnedService` 的 git HEAD + status 前后比对兜底。详见 design.md。