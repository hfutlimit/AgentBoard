# Durable 自动入口修复验证（2026-09-05）

基线：`2e8502f2b02ccc07188d3c844435b06d03257bd6`。本报告区分隔离回归与生产验收。

## 通过的检查

- `dotnet test src/backend-dotnet/AgentBoard.slnx --no-restore`：245 项通过（Domain 60、Application 18、Infrastructure 65、API 102）。
- `dotnet test src/nodes/AgentBoard.Node.Tests/AgentBoard.Node.Tests.csproj --no-restore --filter FullyQualifiedName~Durable`：51 项通过。
- `PYTHONPATH=src/backend-fastapi python -m pytest tests/e2e/happy_path/test_durable_intake.py -q`：6 项通过。
- 从本地当前 FastAPI 源码导出 OpenAPI、同步快照、重生成 .NET client；schema-drift-check 的 live 比对无差异。同步同时补入此前 HTTP bridge 已有的 introspect/agent-select 契约。
- `git diff --check`：通过。

覆盖：复用 Proposal 转换生成 Story/DAG；仅接收就绪任务；项目授权与开关；人类/Story/assignment 门禁；分页不饿死后续候选；不走旧派发；重复与崩溃恢复无重复 Story/Task/Run；前置完成后自动接续；传递提交和证据；重启保留上游开发者排除项；QA 不自审；业务终态与 Story 收尾；状态回写严格保序且不被重试延迟打乱。

## 未通过/未运行的独立检查

- 扩展 Python proposals/ticket/legacy-dispatch 组：71 通过、1 失败。`test_dispatch_no_candidate_leaves_task_in_todo` 预期 todo，当前旧派发逻辑返回 blocked；在 detached 基线中复现相同断言失败。本次不改变未启用 Durable 项目的旧派发策略。
- 旧 `tests/e2e/happy_path` 的另外 4 个测试仍失败。PR7/PR8/PR9 的无 owner 夹具被 403 拒绝，在基线中也复现；golden 测试在当前树缺 reviewer，在独立基线首先报 `no such table: users`，因此没有声称该测试在新旧树中失败原因完全相同或该旧链已经修好。
- `tests/e2e/cross_stack/test_proposal_full_happy_path.py`：1 skipped，缺专用跨栈 MariaDB/RabbitMQ 测试配置。它使用 scenario adapter，也不等于真实 Codex。
- 构建仍报告已有依赖漏洞/弃用警告，未在本次扩大范围升级依赖。

## 生产边界

上述测试包含协议结果夹具和隔离数据库，不是运行真实 CLI 的生产 E2E。本次没有把手工状态写入或模拟结果作为真实测试成功。
需要按 `durable-intake-rollout.md` 更新 FastAPI/Python 服务与 .NET 控制面，配置 allow-list、发布图版本及绑定，再验证真实 Proposal → Story → 全部 Task → Story done。
不得仅凭 operations 返回 200 或二进制发布成功宣布端到端完成。
