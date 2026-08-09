# Proposal — Epic 78 整体验收与收尾（AgentRun 执行器与主动推送闭环）

**status**: in_review

## 背景

Epic 78（AgentRun 执行器与主动推送闭环）的 8 个 Story 已全部完成开发并置 in_review：

| Story | 交付物 | 提交 |
|-------|--------|------|
| 101 执行器适配器框架 | `executor.py` AgentAdapter(ABC) + RunHandle + ADAPTERS 注册表 | Task 965 |
| 102 模式 A Launcher | CliLauncher / CodexLauncher / ClaudeLauncher + prompt 组装 + CLI --run | Task 967 |
| 103 模式 B Trigger | WebhookTrigger + HMAC + trigger_run + CLI --trigger | Task 968 |
| 104 AgentRun 状态机驱动 | AgentRun.summary/log_ref + report_run_result + execute_run 主循环 | Task 969 |
| 105 RunStatus 枚举对齐 | enums/models/migration + docs FR-17 统一 | Task 966 |
| 106 AgentSchedule 绑定松绑 | agent/task_id/筛选 5 列 + pick_eligible_task + scheduler 联动 | Task 978 |
| 107 Agent 记忆自动加载 | get_project_memory / append_agent_memory MCP 工具 | Task 977 |
| 177 Executor daemon 常驻 | run_daemon 主循环 + CLI --daemon | Task 979 |

各 Story 均已通过单元测试 + E2E 验证（前 7 个 Story 曾多轮回归确认，Story 177 上轮新增）。

## 本次收尾

1. 全量复跑 Epic 78 单元测试 + E2E，确认整体无回归；
2. 通过 MCP 将 8 个 Story 全部置 done；
3. Epic 78 由 in_progress 置 done（Epic 验收闭环完成）。

## 验收

- 8 个 Story 全部 done；
- Epic 78 done；
- 回归测试无新增失败。

## 约束

- 不触碰端口 18001（WorkBuddy MCP 通信）；
- 零既有 REST / DB 契约变更（纯状态收尾 + 文档）。
