# Worker-owned 协作讨论

## 用户确认的边界

保持七种工作。普通工作在项目/工作类型共享队列竞争；讨论属于原工作类型的只读阶段，不新增角色。
项目路径统一由 Worker 配置；所有本机 Agent 参与所有映射项目，用户通过 WorkKinds 决定职责。
停用 Agent、移除项目或取消对应能力后不会偷偷代领讨论，原参与者离线时保持等待；需要原实例恢复或人工处理。

## 协议

1. Review 没有问题可直接 `approve`；任何拒绝建议先 `discuss`，附 summary 与 evidence 引用。
2. `WorkerDiscussion` 记录原实施 Agent、原 Reviewer、review_round、轮次与回复评论 ID；Task 保持 `in_review`。
3. Worker 根据 snapshot 的讨论状态明确 offer 下一轮，Server 校验 scope / turn / recipient，并转发到
   `agentboard.work.v2.project.<id>.<kind>.agent.<sha256(UTF-8 Agent ID)>`。Node 只消费本地启用身份及能力的定向队列。
4. 原作者 `respond`，position 为 `agree / disagree / clarify`；Reviewer 可以 `discuss / confirm / withdraw / escalate`。
   `confirm` 必须有原作者上一条明确同意。不同意不能强行驳回；最多三次作者回复，再继续只能撤回或转人工。
5. 确认评审问题才进入原工作返工；撤回误报则正常通过。超限/疑难 `escalate` 会阻塞 Task，并在 Story 留言提示人工裁决。
   所有发言写入 Task 评论，含回复对象、上一条评论 ID、证据；Task/Story 页面按讨论展示状态和往返消息，可手动刷新。
6. 人工裁决沿用现有 Task/Story 评论和管理流程；本次不自动替人做决策，也不提供绕过讨论的 Agent 特权。

当前一个 Task 同时一个讨论，可包含一组相互关联的发现，**不是每个 finding 的独立状态机**；一组未完全达成一致就继续讨论或转人工，不允许部分确认后偷偷把全部缺陷建单。
Story 展示子 Task 讨论与升级留言；尚不支持 Agent 自动发起独立的 Story 级跨 Task 讨论。

## QA 的两类问题

- `subject=review_findings`：质疑 QA 测试方法/覆盖/证据。确认后 QA 补测，不创建产品 Bug。
- `subject=qa_defects`：对合理失败 QA 报告中的产品缺陷核实。确认后创建对应 Bug + 独立 QA 复测。
  撤回则要求 QA 修正报告，不创建 Bug。只有失败 QA 才能使用该 subject。
- 撤回对失败 QA 的方法质疑后，仍需要单独核实产品缺陷，不能把“方法没问题”当成“全部缺陷已确认”。

普通工作和讨论复用同一租约 CAS、输入指纹、结果幂等、三次运行重试机制；回复重试不改变 Task 分配、不回退 todo。
讨论阶段所有工作类型均要求 checkout/HEAD 不变，包括 dev/design；pre/post 不能覆盖此约束。

## 发布顺序

1. 安全暂停旧 Worker，等待在途执行完成。备份业务数据库。
2. 同步 FastAPI **及 MCP** 代码，Alembic `upgrade head` 至 `a19d58e204bc`（新增 worker_discussions、WorkerWork 讨论关联/目标身份），重启对应服务。
3. 发布 Angular 前端（Task/Story 展示）与新 Node。不要重启旧 .NET durable 中央编排。
4. 先验证鉴权、snapshot `protocol=worker-work.discussions.v1`、讨论读接口。Node 启动会拒绝旧协议服务器，避免消费后连续失败。
5. 核对本地统一项目范围与七种能力，再启动执行 Worker。配置台可提前更新，configuration-only 不消费消息。

测试目录中的业务协议测试使用隔离 DB，不代表真实生产 Agent E2E。生产尚未部署这一版本时，不能沿用旧版本 E2E 结论声称讨论已上线。

## 本轮验证（2026-09-05）

- 业务协议与迁移：`python -m pytest tests/e2e/happy_path/test_worker_owned_work.py tests/unit/test_worker_discussion_migration.py -q`，22 passed。
  包括多轮反驳/撤回、原参与者限制、重复评论幂等、过期租约、讨论重试、三轮升级、QA 两类问题及确认后 Bug/复测闭环。
- Node：`dotnet test src/nodes/AgentBoard.Node.Tests/AgentBoard.Node.Tests.csproj --no-restore`，319 passed。
- Angular 讨论组件：3 passed；覆盖 Task/Story 查询、回复引用、未部署提示、请求取消、Agent 文本转义；生产构建成功。
- Portal：`node scripts/test_worker_owned_portal.cjs` 通过；浏览器确认生产项目列表、独立 `/#projects` 页面、全项目共享提示及七种能力。
  本机配置台保持 configuration-only，未改写现有用户配置，未启动生产消费者。
- OpenAPI：同步快照、SHA 和 NSwag 14.5.0 客户端；独立本机 FastAPI（关闭 lifespan / 不连接生产数据库）live schema 检查零差异。
  原生成客户端尚未包含已有 worker-work 路由，本轮一并按快照补齐，导致匿名 DTO 编号机械顺延；二次生成一致，.NET API 构建成功。
- 已知构建警告：既有 .NET 依赖安全公告、进程封装 CS9124，Angular 初始包超过 1 MB 预算；本次未扩展到依赖升级/拆包。
- **未执行本次新版协议的真实 CLI / 生产 E2E，未部署生产服务端或前端。** 需要按上文先部署再运行独立 Agent 实测。
