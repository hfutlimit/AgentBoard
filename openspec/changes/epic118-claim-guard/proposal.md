# Proposal — Agent 认领并发护栏（Epic 118）

epic: 118
story: 226
task: 998
status: in_review

## 背景

AgentBoard 支持多 Agent 并行自动化开发（WorkBuddy 自动化每小时轮询认领任务）。MCP 工具 `claim_task`（mcp_server.py `_agent_claim_task`）存在两个真实缺陷：

1. **无并发保护**：任务已被其他 Agent 认领（in_progress）或已结束（done）时，`claim_task` 仍会创建新 Run 并重复 PUT 状态推进——多 Agent 并行时产生重复 Run 与无意义的状态写操作。
2. **死代码**：创建 Run 的路径为 `"/api/schedules/0/runs" if False else "/api/schedules/1/runs"`——`if False` 分支恒不执行，且双分支表达式相同（历史重构遗留）。

## 方案

纯 mcp_server.py 增量改动，零 REST/DB 契约变更，零新增依赖：

1. **死代码清理**：`if False else` 双分支统一为单一路径 `/api/schedules/1/runs`（保留 schedule 1 手动触发占位约定，不扩大改动面）。
2. **占用保护**：GET 任务后若 status 非 `backlog`/`todo`（已被认领 in_progress / 已结束 done / in_review 等）→ 返回 `{"error": "task {id} already claimed or not claimable (status=...)", "task": ..., "run": None}`，不创建 Run、不 PUT 状态。
3. **Run 幂等复用**：创建前先 `GET /api/schedules/1/runs`，命中同 task 的 active Run（status ∈ pending/running）则复用（返回 `reused: true`），不新建；终态 Run（success/failed）不复用。
4. **创建失败兜底**：POST create run 返回 error（如 409 幂等冲突）时透传 error，不推进状态。

## 影响面

- `agentboard/mcp_server.py`：仅 `_agent_claim_task`（约 +20/−8 行）。
- 测试：新增 `tests/test_epic118_claim_guard.py`（9 用例：三分支 mock + AST 死代码护栏 + 工具注册验证）。
- 部署注意：18001 MCP 容器运行内存中旧代码，本变更随下次独立运维窗口重部署生效；本仓库单测即验证载体（历史经验）。
