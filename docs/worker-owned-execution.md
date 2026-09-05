# Worker-owned execution v2

本页描述用户确认的执行边界，不把旧 durable v1 的 Server 编排配置继续下移复制。

## 七种工作

| 工作 | 责任 | 后续 |
| --- | --- | --- |
| proposal | 需求分析、持续 grill、收敛并拆 Story/Task DAG；拆 ticket 不是单独能力 | 等待用户回答或 design |
| design | 设计并提交设计产物 | design_review |
| design_review | 独立评审设计，讨论确认问题后回 design | dev |
| dev | 开发、修复、测试并提交代码 | dev_review |
| dev_review | 独立评审实现，讨论确认问题后回 dev | qa |
| qa | 独立 Task；本地部署、实际测试、提交部署步骤/测试步骤/实际结果 | qa_review |
| qa_review | 评估 QA 工作及证据是否合理充分；不是再次做代码评审 | 所有 Task 完成后 Worker 请求关闭 Story |

同一 Agent 不可自审。QA 排除上游 Dev 实施 Agent；上游 Dev 可以评审另一 Agent 完成的 QA。

## 边界与可靠性

Worker 的 `WorkerOwned` 配置独占项目/本地路径、Agent 实例、模型、工作能力和下一步决策。
Projects 为 Worker 统一 mapping，所有 Agent 参与全部映射项目；每个 Agent 只按 WorkKinds 区分工作职责。
Server 不配置 WorkspaceId、BaseVersion、provider、阶段 WorkflowVersion，不选择 Agent。
Server 仍负责业务数据、权限、依赖/人工门禁校验、租约 fencing、结果幂等和 RabbitMQ 持久化转发；
“只发任务”不表示取消认证或允许 Worker 随意覆盖业务状态。

Worker 读取 `/api/worker-work/snapshot`，本地规划后提交 `offers`。Server 校验并持久化 outbox，
发布到 direct exchange `agentboard.work.v2`，共享队列/路由键为
`agentboard.work.v2.project.<ProjectId>.<kind>`。不同 Worker 在相同项目/工作队列上竞争，
dev-only Worker 不订阅 qa 队列。没有每个 Worker 一份的广播工作副本。
例外是双方讨论回复：使用上述队列名加 `.agent.<sha256(agent-id)>` 定向给原参与 Agent，依然受本地项目/工作类型配置约束。讨论不是第八种工作；完整协议见 [协作讨论](worker-owned-discussions.md)。

claim token 在本地 SQLite journal 先落盘；Server CAS 认领，租约 3 分钟，每 30 秒续租。
结果先存 journal，再提交 fenced completion，最后 ACK。丢失 HTTP 响应重放结果，不重新运行模型。
最多 3 次物理执行尝试；失败/租约耗尽留存证据并阻塞，不能冒充业务成功。
暂停停止领取新工作，已领取工作继续；退出取消进程树。不要同时运行共享同一 journal 的两个 Worker。
journal 绑定 Server URL 与 Worker 身份；切换 Server/Worker 要使用新 journal 路径，保留旧文件供审计，避免跨环境重放同号任务。

执行前读取本地 Git HEAD，校验上游证据 commit 已在 checkout 祖先链中。同机 checkout 使用跨进程锁。
实施需提交且不留下新增未提交修改；Proposal/QA/review 不能改变 checkout/HEAD，报告通过结果 JSON 提交。
跨机器独立 checkout 暂不自动传输/推送提交：必须事先有可用的上游提交，否则 fail-closed。
当前每个 Worker 串行执行，配置多个 Agent profile 不等于单进程并行；需要并行可运行多个独立 Worker。

## QA 缺陷闭环

QA 失败需提交 `defects=[{title,description}]`，包含复现步骤、期望/实际结果和证据。
测试/部署阻塞也需如实记录，不能编造产品缺陷。QA Review 判断测试工作是否合理，而不是要求产品必须无缺陷：

- 不合理报告：先进行 `review_findings` 讨论，双方确认后原 QA Task 返工，不创建 Bug。
- 合理报告且发现问题：先进行 `qa_defects` 讨论，双方确认后 Worker 明确提交 `qa_followup`，Server 在同一 fenced completion 事务创建每个缺陷对应的 `bug` Task 和一个独立 QA 复测 Task。重放不会重复建单。
- Bug 依赖原 QA；复测依赖所有 Bug。Bug 走 `dev` / `dev_review` 队列，由本地具备对应能力的 Agent 竞争，不指定或退回原 Dev Agent。
- 原 QA 完成代表其工作已获认可，失败证据不改写；原 Dev Task 不回退。新 Bug 和复测未完成时 Story 不能关闭。
- 复测排除全部上游 `dev` 和 `bug` 实施者。复测再失败会进入新一轮 Bug / 复测链，直到复测和 QA Review 通过。

计划在 Worker 生成，Server 只验证来源、完整缺陷列表、归属和依赖并持久化，不负责选择执行 Agent。
新版讨论需同时更新 FastAPI/MCP 与 Node，并迁移至 `a19d58e204bc`；Task/Story 讨论展示还需发布前端。

## 部署（默认不开启，不能与 v1 混跑）

1. 备份真实业务数据库，暂停并排空旧 ProposalWorker/Node/v1 durable 的在途工作。
2. 发布最新 FastAPI 与新 Node；执行 Alembic `upgrade head`，增加 `worker_work` 表。
3. FastAPI 设置 `AGENTBOARD_WORKER_OWNED_ENABLED=1` 和可用的 `AGENTBOARD_MQ_URL`。
   该开关是全局模式切换，不是 Server 配置某些项目给某些 Agent。
4. 停止旧 .NET durable Intake/Outbox/ResultConsumer（不得继续中央编排），新 Node 设置
   `WorkerOwned.Enabled=true`、`DurableExecution.Enabled=false`。
5. Worker 配置 `AgentBoard.ServerUrl`、凭据、RabbitMQ，以及本地 Projects/Agents。
   新端点 `/api/worker-work/*` 属于 **FastAPI**，不要被旧 `/api/durable-workflows/*` 的 .NET IIS 规则截走。
6. 验证无 token 401、有效 token+项目成员可读；启动且仅启动这一轮选定 provider 的多个 Worker。
   检查七种队列、独立身份、业务证据，不能只用 HTTP 200 当完成。

配置结构见 `config/examples/worker-owned.json`。Node 默认读取 appsettings/环境变量；
示例不是自动载入的生产配置。把相应节合入本地配置，或用 `WorkerOwned__Agents__0__...` 环境变量。
凭据只经安全环境/私有配置注入；不提交密钥，不要求把凭据贴到对话中。
公网运行必须使用 HTTPS 和受保护的 RabbitMQ 连接，不通过明文 HTTP 发送长期凭据。
回滚前必须排空 v2 租约/队列；不要直接关开关后重启旧调度器，不要删除结果表。

## 可复现实测

`scripts/run_worker_owned_e2e.py` 创建隔离的真实 FastAPI、SQLite、RabbitMQ，使用真实 CLI，
只提交 Proposal，随后由 Worker 完成七种工作、Task DAG 和 Story 关闭。
这不是生产 URL 的部署验收，也不是内存 fake adapter 测试。

```powershell
dotnet publish src/nodes/AgentBoard.Node/AgentBoard.Node.csproj -c Release -o tmp/worker-owned-e2e/node-bin
python scripts/run_worker_owned_e2e.py --provider codex --model gpt-5.6-terra --cli <真实Codex.exe> --workspace <干净专用Git-worktree> --output tmp/worker-owned-e2e/<新目录> --split-workers
```

WorkBuddy 用 `--provider workbuddy --cli <安装目录中的codebuddy> --model auto`。
MiniMax 必须使用核实可运行的官方 headless CLI；当前本机安装的 wrapper 指向缺失 daemon/cli.js，
因此 harness 尚未启用它，不用其他 provider 或第三方 invoker 冒充 MiniMax Code。
报告含 Proposal/Story/Task 状态；DB 包含每个 work 的 Agent、attempt/result/history；
Node 日志与专用 Git 提交作为补充证据。输出目录含本地 journal，应按敏感运行数据保护。
测试结束仅停止本次创建的子进程和 broker，保留数据及专用 checkout，不删除用户仓库或现有容器。
