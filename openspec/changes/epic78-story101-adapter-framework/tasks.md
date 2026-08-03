# Tasks — 执行器适配器框架

**status**: in_review

## Task 1.1 — 新增 `agentboard/executor.py` 适配器框架

- [x] `AgentAdapter(ABC)`：`launch(run, task, ctx) -> RunHandle` /
      `poll_status(handle) -> RunStatus` 抽象方法 + `build_prompt` 默认实现
- [x] `LauncherAdapter`（基于 `process.poll()` 退出码判定 SUCCESS/FAILED）
- [x] `TriggerAdapter`（默认等待显式状态变更）
- [x] `NotConfiguredAdapter` 兜底（launch 抛可读错误、poll 恒 FAILED）
- [x] `RunHandle` / `AgentRunContext` dataclass（含 mark_running/complete/fail）
- [x] 注册表 `ADAPTERS` + `register_adapter`（函数式/装饰器式）+ `@adapter(name)`
      + `get_adapter(default=)` / `resolve_adapter` / `has_adapter` / `registered_adapters`
- [x] `AdapterError` / `AdapterNotFound` / `AdapterAlreadyRegistered` 异常
- [x] `KNOWN_AGENTS = ("codex", "claude", "workbuddy", "qoder")` 预留

## Task 1.2 — 单元测试 `tests/test_epic78_story101_adapter_framework.py`

- [x] 抽象类不可直接实例化；子类实现后可实例化
- [x] 两种注册方式（函数式 / 装饰器）均可取回，`cls.name` 与注册键一致
- [x] 未注册抛 `AdapterNotFound`；default / resolve 兜底生效
- [x] 重复注册抛 `AdapterAlreadyRegistered`；同对象幂等；replace 覆盖
- [x] FakeAdapter 全生命周期：launch → RUNNING → complete → SUCCESS
- [x] Launcher 真实子进程：退出码 0 → SUCCESS；非 0 → FAILED + error
- [x] 新增 Agent 类型只需注册（扩展点证明）
- [x] NotConfiguredAdapter：launch 抛可读错误、poll 恒 FAILED
- [x] AgentRunContext.as_dict / build_prompt 默认骨架
- [x] 全局注册表测试隔离 fixture（保存/恢复快照）

## Task 1.3 — OpenSpec 文档

- [x] `proposal.md` / `design.md` / `tasks.md` 三件套

## 验证结果

- `tests/test_epic78_story101_adapter_framework.py`：**24 passed** (1.53s)
- 聚焦回归（test_domain_boundaries + admin_api_key_scope + epic30_cache +
  scheduler）：**22 passed, 1 skipped**
- 全量回归：见执行日志（预期无新增失败，纯新增模块）
- MCP 状态：Task 965 / Story 101 → **in_review**（合法链
  backlog→todo→in_progress→in_review）；Epic 78 → in_progress
- 硬约束：未触碰 18001 / docker；零既有 REST 契约变更
