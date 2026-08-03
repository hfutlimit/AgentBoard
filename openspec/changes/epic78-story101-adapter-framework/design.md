# Design — 执行器适配器框架

## 架构定位

```
                    ┌─────────────────────────────────────────────┐
                    │            Executor 主循环 (Story 104)       │
                    │  认领 pending run → running → finalize      │
                    └──────────────────┬──────────────────────────┘
                                       │ launch() / poll_status()
                    ┌──────────────────▼──────────────────────────┐
                    │         AgentAdapter (ABC)  ★本 Change      │
                    │  launch(run, task, ctx) -> RunHandle         │
                    │  poll_status(handle) -> RunStatus            │
                    └───────┬─────────────────────┬───────────────┘
                            │                     │
               ┌────────────▼───────┐   ┌─────────▼───────────────┐
               │  LauncherAdapter   │   │  TriggerAdapter         │
               │  (模式 A: spawn)   │   │  (模式 B: webhook)      │
               │  Codex/Claude...   │   │  WorkBuddy/Qoder...     │
               └────────────────────┘   └─────────────────────────┘
```

## 关键设计决策

### 1. 抽象方法最小集：launch + poll_status

- **launch**：启动一次执行。Launcher 返回挂有 `process`（subprocess）的
  `RunHandle`；Trigger 返回即可，完成判定走回调 / 显式回写。
- **poll_status**：判定完成。返回 `RUNNING` 则继续轮询；返回 `SUCCESS` /
  `FAILED` 即终态，Executor 停止轮询并 finalize。

只保留这两个抽象方法，把「完成判定与输出捕获」这一各 Agent 差异最大之处
封装进适配器，Executor 主干保持干净。

### 2. RunHandle 携带状态机辅助

`RunHandle` 提供 `mark_running()` / `complete()` / `fail()` 便捷方法，内部维护
`started_at` / `finished_at` 时间戳，供 Executor 写回 `agent_runs` 表
（`started_at` / `finished_at` / `output` / `error_message`）。

### 3. AgentRunContext 与 ORM 解耦

Executor 主循环把 ORM 对象（AgentRun / Task / Project / memory）扁平化为
`AgentRunContext` 传入 Adapter，Adapter 不依赖 SQLAlchemy 模型 —— 便于单测与
后续 SDK 直调场景复用。

### 4. 兜底适配器防 KeyError 裸崩

`NotConfiguredAdapter`：`launch` 抛带已注册列表的可读 `AdapterError`，
`poll_status` 恒返回 `FAILED`。未配置的 agent 不会让 Executor 主循环崩溃，
而是落一条可读的失败记录（含现有适配器清单，便于排障）。

### 5. 注册表语义

- 键 = agent 名（`codex` / `claude` / `workbuddy` / `qoder`，`KNOWN_AGENTS` 预留）。
- 重复注册：默认抛 `AdapterAlreadyRegistered`（防两个模块同 key 互踩）；
  同对象重复注册幂等；`replace=True` 显式覆盖。
- `resolve_adapter()` 永不抛 KeyError，未注册回退 `NotConfiguredAdapter`。

## 兼容性

- 纯新增 `agentboard/executor.py` + `tests/test_epic78_story101_adapter_framework.py`。
- 零 REST 契约变更、零数据库变更、零前端变更。
- 不修改 `scheduler.py` / `service.py` / `api.py` / `mcp_server.py`。
