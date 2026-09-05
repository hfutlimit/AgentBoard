# Worker-owned 本机配置台

这是真正读取 / 写入 .NET Worker-owned 本机配置的页面，不再使用旧 Python / Angular Processor Portal 的角色与项目映射协议。仅查看和保存配置不要求部署生产服务器；启用新版协作讨论执行则需要先部署对应服务端及迁移，见 [讨论协议](worker-owned-discussions.md)。

## 可配置内容

- 多个独立 Agent 实例：ID、启用状态、Codex / WorkBuddy / MiniMax、模型、CLI 程序、参数和超时。
- 项目：从生产 `GET /api/projects` 分页读取可见项目，先通过 `/api/auth/me` 校验真实身份；本机保存项目 ID → checkout 绝对路径。
- 独立的「项目路径 Mapping」页面（`/#projects`）：项目范围属于 Worker，所有本地 Agent 自动参与全部映射项目，不再逐个 Agent 选项目。历史 Agent.ProjectIds 被忽略，保存时移除；升级后项目范围会扩大到 Worker 的全部映射，启动前请核对。
- 每个 Agent 仅配置七种工作类型：`proposal / design / design_review / dev / dev_review / qa / qa_review`。Bug 是 Dev 消费的 Task 类型，不是第八种工作类型。
- Agent 通用 pre/post，以及七种工作各自的 pre/post。页面附带可按需填入的建议提示词。

提示词执行顺序：通用 pre → 专属 pre → 当前工作与上下文 → 专属 post → 通用 post → 结构化结果。

Pre 用于开始前准备，post 用于提交前自检，均进入同一次真实 CLI 调用的提示词，不是 shell 钩子，也不是第二次模型执行。空字段不添加内容；未启用工作类型的专属提示词不会被其他类型使用。协议中的只读、禁止生产修改、自审隔离和 JSON 结果格式不因提示词而放宽。

## 启动与凭据

使用环境变量提供 `AgentBoard__ServerUrl`、`AgentBoard__StartupToken`、`RabbitMq__Uri`，不要把密钥写进 JSON 或命令行。本机配置台无需 Portal Key，打开即进入。生产 API 仍使用环境凭据鉴权。

```powershell
./scripts/start_worker_owned_portal.ps1
```

启动脚本默认发布本机 Node，监听 `http://127.0.0.1:18240/`，始终设置 `Portal:ConfigurationOnly=true`。它不会运行注册、心跳、协调、RabbitMQ 消费或 CLI readiness 探测。`-NoBuild` 可复用已发布二进制。

默认本机配置位置：`%LOCALAPPDATA%\AgentBoard\worker-owned.local.json`。启动参数 `-ConfigurationPath` 可指定不同 Worker 的配置文件。该文件是 **WorkerOwnedOptions 本身**，不带外层 `WorkerOwned`；原 `config/examples/worker-owned.json` 仍是完整 appsettings 格式，不能原样当作此文件。

页面不再保存访问密钥或显示登录框。仅接受回环来源、localhost / 回环 Host 和同源浏览器请求；保存操作使用自动附带的非秘密请求标记，避免其他网页伪造提交。旧版 Node 控制 API 的鉴权不变。本次免密仅针对新版 `/api/local/*` 配置台，不是放开生产 API 或允许远程配置。

## 保存与真正生效

保存通过本机 `/api/local/configuration`，使用版本校验、跨进程编辑锁、原子替换及上一版 `.bak` 备份。非法工作类型、无效项目 ID、不存在的绝对目录、重复身份及超长提示词均会拒绝。不同页面的陈旧保存返回 409，不覆盖新设置。

执行进程启动时读取这个完整快照，**不是逐项合并 JSON 数组**，因此删除的实例和工作类型不会从旧 appsettings 或环境数组中重新出现。

保存不自动重启或改变正在运行的任务。正常执行 Worker 必须使用同一个 `--LocalConfigurationPath=<绝对路径>`，并在安全停止后重启。下次运行才采用新任务权限和提示词；已经落盘的执行结果仍按原有 journal 重放，不重新调用模型。

示例（会开始执行，不要仅为查看页面运行）：

```powershell
dotnet <发布目录>/AgentBoard.Node.dll --LocalConfigurationPath=<本机配置路径> --Portal:ConfigurationOnly=false --DurableExecution:Enabled=false
```

仍需通过环境配置生产连接、独立 Node ID 和每个 Worker 独立的 HistoryDatabasePath。单 Worker 目前串行调用其多个实例；多个 Worker 可竞争业务范围内的工作。停用单个 Agent 后不会登记、心跳、订阅或选择它；已有本机配置的 Worker 即使关闭 Worker-owned，也不会回退启动旧版广域消费者。

配置页只显示 RabbitMQ 环境配置是否存在，不把它标记为已连通。生产连接状态以真正的身份校验和项目读取为准。生产凭据不返回浏览器，页面也不编辑服务账号凭据。若旧配置含每 Agent 独立 token，请先迁移到合适的环境凭据方案再使用此页保存；此页只管理无密钥的本地配置。

## 2026-09-05 验证

本机免密更新：317 项 .NET 测试通过，页面 DOM 测试通过；真实无 Portal Key 读取 / 保存配置均成功，生产项目读取仍为 14 个。跨源与 DNS 重绑定 Host 请求返回 403。下面的 309 项及 401 记录是首次带 Portal Key 版本的历史验证，不再表示新版配置台需要密钥。

- .NET Node 测试：309 通过，包含保存重载、能力过滤、禁用实例、陈旧版本、跨进程编辑锁、凭据屏蔽、非法输入、提示词顺序，以及保存的 pre/post 真正进入 Codex adapter stdin。
- `node scripts/test_worker_owned_portal.cjs`：页面 DOM 交互测试通过，覆盖七种工作、项目响应格式、提示词范围切换、多 Agent 隔离、保存重载、CLI 类型切换和增删实例。此测试使用模拟 HTTP，不冒充生产测试。
- 本机已发布 Node 真实连接生产：身份验证后返回 14 个可见项目；配置 API 无密钥 / 错误密钥 401，跨源 403，非法工作类型 400，陈旧保存 409。
- 浏览器实测：新版中文页面、真实生产项目、两套 Codex Terra 实例；编辑通用 pre，保存成功后重新加载保留中文内容。
- 页面进程为 configuration-only，没有启动任务消费者；本轮没有重跑完整 Proposal → Story 生产 E2E，之前的生产 Codex 结果见 `worker-owned-production-validation-20260905.md`。
- 构建存在已有 NU1903 依赖安全警告以及已有编译 / xUnit 警告，本轮未扩大到依赖升级。
