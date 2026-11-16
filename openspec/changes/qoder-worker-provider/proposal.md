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
- `CoordinationServices.cs`：`WorkerStartupService` 的 instance upsert 槽位与 `RequireRegistration` 白名单补 qoder；fail-fast 的白名单、诊断与补救提示同源于一张表
- `appsettings.json` / `appsettings.Production.json` / `appsettings.Local.template.json`：Qoder 槽位（默认停用，字段与 Cursor 同构）
- 服务端两处 provider 白名单：见下节「服务端改动」

## 服务端改动（2026-11-16 修正原判断）

原 proposal 断言「注册端 `executor_type` 是无枚举校验的 string(40)」，据此把服务端
整体排除在外。**该断言错误**：`features/scheduling/service.py::upsert_agent_instance`
对 `executor_type` 做 `EXECUTOR_TYPES` 白名单校验，未知值抛 `InvalidValue`。
一次性探针实测（注册 worker + agent 后 upsert）：`executor_type="qoder"` 通过、
`"qoder-not-a-provider"` 被拒。因此 .NET `WorkerStartupService` 要成功挂 qoder
instance，服务端白名单**必须**同步：

- `service.py`：`_AGENT_EXECUTOR_TOOLS` / `EXECUTOR_TYPES` 补 `qoder`——注册与
  `resolve_agent_executor_type` 的 roles 兼容推导都依赖它，属本目标的必要条件。
- `router.py`：`_SUPPORTED_PROVIDERS` 补 `qoder`——只影响旧 Durable 平面
  `/api/durable/agent-select` 的 roles fallback（`_provider_from` 对 `executor_type`
  直传不查白名单）。保留它是为了与 `EXECUTOR_TYPES` 一致：.NET
  `DatabaseAgentSelector` 现在只是薄 HTTP 传输，provider 解析的唯一事实来源就是
  Python，两处白名单分叉会让「executor_type=qoder 可选、roles-only qoder 不可选」。
  由 `tests/unit/test_executor_provider_contract.py` 锁住两者同步。

## 不改什么（明确排除）

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
| 服务端 | `EXECUTOR_TYPES` 与 `_SUPPORTED_PROVIDERS` 各补一个枚举值；无 schema/接口形状变化，旧 executor 行为不变 |

## 风险

`--permission-mode bypass_permissions` 与 Codex / Cursor 的无人值守取舍同构，本地**没有**权限护栏。只读工作类型（proposal / review / qa）依赖 `WorkerOwnedService` 的 git HEAD + status 前后比对兜底。详见 design.md。