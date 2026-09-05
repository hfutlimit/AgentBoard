# Proposal → Story → Durable Task 自动入口部署

本次修复把持久化的业务 `todo` 任务接入 .NET Durable 控制面。复用既有
ProposalConversionService 生成 Story/DAG，不由 Node 修改图，也不复制业务表到 SQLite。
默认关闭；只接管显式配置的项目。**发布二进制但不配置项目和版本绑定，不会启动自动流程。**

## 边界

- Proposal 的分析、澄清、收敛仍由现有 Proposal 消费链完成；本入口处理 `auto_story` 请求的物化与后续任务执行。
- 任务必须有 owner、依赖全部完成、无人类确认门禁、无旧 assignment，Story 不得为 backlog/blocked/done。
- 同一个项目/Workspace 同时只执行一个业务任务；不同实例可依次承担开发和独立评审/QA。
- QA 排除上游开发者（含传递依赖）；独立 Reviewer 可以继续 QA。审核设计也排除设计作者。
- `business-task-{id}` 是稳定 Run ID。重复扫描和进程重启不会生成第二个 Run。
- 状态回写按 Task 的持久化入队顺序执行，前项重试时后项不会越过它；独立 QA 也按 in_progress → in_review → done 回写。
- 下游携带上游已接受的 outcome、结果证据和提交版本。失败/取消的任务不会因重新设成 todo 而无限自动重跑；需按既有运维恢复机制处理。
- 本次不做数据库结构迁移；Durable SQLite 只保存控制面状态，必须保留现有 DB/WAL/outbox。

## 上线顺序

1. 选择独立测试项目或已清空旧执行/消息的项目。不要中途接管有旧 assignment、旧评审或旧消费消息的 Story；旧完成任务没有 Durable 上游证据。
2. 暂停该项目的执行实例。备份 .NET 发布目录、应用配置和一致性的 Durable SQLite 数据；停止对应应用池后备份/发布，禁止拷贝一半运行中的 DB。
3. 更新 FastAPI、MCP 及使用相同业务服务代码的 Python workflow worker。所有这些进程设置相同的 `AGENTBOARD_DURABLE_PROJECT_IDS`，例如 `8`。它会禁止该项目的旧 task/story claim 和旧 reviewer 派发；不要停掉仍需处理其他项目的整个服务。
4. 更新 .NET API（包含 Domain DLL）。保持现有 RabbitMQ、`AgentBoard:FastApi:InternalUrl`、`InternalToken` 配置；IIS 场景 InternalUrl 为本机 FastAPI 地址。使用有项目成员资格与 `api:read`/`api:write` 权限的专用服务 Key，不复制用户凭据摘要、不关闭鉴权。
5. 先保持 `DurableWorkflow:Intake:Enabled=false`，通过已鉴权的 `POST /api/durable-workflows/versions` 发布审核过的不可变图。请求体为仓库的 WorkflowVersion 契约，ContentHash 用 `WorkflowGraph.ComputeContentHash` 生成。给 design/dev/qa（如用 bug 也包括 bug）分别选定版本；需要返工时使用显式有界反馈图，不用测试里的 Development-only 图代替生产评审流程。
6. 配置下例中的真实 Workspace ID、已存在的起始提交和已发布版本 ID，再启用 intake。保持一个 .NET 控制面进程（IIS 应用池 maxProcesses=1，发布期间避免新旧进程重叠写同一控制面库）。

```json
{
  "DurableWorkflow": {
    "Enabled": true,
    "Intake": {
      "Enabled": true,
      "PollSeconds": 5,
      "Projects": [{
        "ProjectId": 8,
        "WorkspaceId": "<Node 上已映射的 Workspace ID>",
        "BaseVersion": "<真实起始 commit>",
        "WorkflowVersions": {
          "design": "<已发布的 design 版本 ID>",
          "dev": "<已发布的 dev 版本 ID>",
          "qa": "<已发布的 qa 版本 ID>"
        }
      }]
    }
  }
}
```

环境变量形式为 `DurableWorkflow__Intake__Enabled`、
`DurableWorkflow__Intake__Projects__0__ProjectId` 等；不要把占位符原样上线。
新 `/api/durable/*` 端点属于 **FastAPI**，继续走 IIS `/api` 的 FastAPI 兜底；
既有 `/api/durable-workflows/*` 才转发 .NET，无需扩大公网端口。

## 验收（不能只看 operations=200）

以下 PowerShell 使用环境中已有 Key，既不打印 Key，也不要求粘贴密码：

```powershell
$intakeHeaders = @{ Authorization = "Bearer $env:AgentBoard_Api_Key" }
Invoke-RestMethod 'http://124.220.44.12/api/durable-workflows/operations' -Headers $intakeHeaders
Invoke-RestMethod 'http://124.220.44.12/api/durable/ready-tasks?project_id=8' -Headers $intakeHeaders
```

- 未认证请求应 401；错误成员/权限应 403；未配置项目应 409。HTTP 200 空队列不等于真实测试完成。
- 第一轮只启用两个 Codex/terra 实例：不同 Agent ID、同一任务 owner 下的动态工作能力、正确 Worker/项目/Workspace 映射。其他 provider 实例关闭。Node Durable 消费启用，测试 Node 的旧 task workflow 消费关闭；Proposal 分析消费仍需可用。
- 正常创建一条 auto-create Proposal 并完成必要的澄清。**不手工调用 POST runs、不手工设 Task/Story done、不使用 fake adapter 冒充真实 CLI。**
- 确認自动生成 Story 与依赖任务、服务日志出现 `Durable intake accepted task ... as business-task-...`，观察每一项 Task 都有对应 Run。
- 前置任务未完成时下游没有 Run；完成后下游自动启动。核对交接中的 commit/evidence、独立 Reviewer/QA 身份、所有 Task done 和最终 Story done。
- 记录 Proposal/Story/Task/Run ID、真实 CLI 模型、提交、测试结果及终态。Codex 成功后再单独测试 WorkBuddy、MiniMax Code；同类 agent 同原因失败三次后按约定切换，不清库掩盖失败。

`deferred_request_ids` 与服务的 deferred/failed 日志表示尚未接受，不应报成功。
版本不存在、服务 Key 失效、配置不一致、无人可接时修复原因后重试，不回退旧派发。

## 回滚

优先仅将 .NET `Intake.Enabled` 设为 false，停止接收新任务；已持久化 Run 仍按现有 Durable 链路完成。
有活动 Run 时不要移除 FastAPI allow-list，也不要打开旧 task consumer，否则可能双重执行。
确认 Run/消息已排空后，才按备份整体回滚。不要删除 Durable DB、outbox 或 Node journal。

## 本次验证边界

自动入口、鉴权、幂等、崩溃恢复、依赖接续、证据传递、QA 身份隔离由隔离测试覆盖。
这些测试使用业务/协议夹具，不冒充生产 CLI E2E；正式端到端结果必须在部署配置完成后单独记录。
