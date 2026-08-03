# Epic 78 · Story 101 — 执行器适配器框架（AgentAdapter + 注册表）

**status**: in_review
**date**: 2026-08-04

## 问题

AgentBoard 的自主开发闭环停留在「调度扫描」阶段：`scheduler.py` 扫描到期
`AgentSchedule` 并创建 `pending` 的 `AgentRun`（带行锁 lease + 幂等键），但
**没有任何代码真正调起 Agent 并回写结果**——「执行器」是架构承诺而非可用能力。

Epic 78 的目标是补齐执行侧（Executor = 触发器 + 适配器 + 结果汇集）。本 Change
是 Epic 78 的第一块地基：**适配器框架**。它定义执行器与具体 Agent（Codex /
Claude / WorkBuddy / Qoder）之间的解耦层，后续 Story 102（Launcher）、103
（Trigger）、104（执行器主循环）全部构建在其之上。

## 目标

1. `AgentAdapter(ABC)` 抽象：`launch(run, task, ctx) -> RunHandle` 启动一次执行；
   `poll_status(handle) -> RunStatus` 判定完成。
2. 两类场景基类：`LauncherAdapter`（直接 spawn CLI 子进程）、`TriggerAdapter`
   （Webhook 唤醒常驻 Runner）。
3. `RunHandle` / `AgentRunContext` 数据载体：运行句柄与执行上下文（任务、项目、
   记忆等扁平化字段），Adapter 与 ORM 解耦。
4. `ADAPTERS` 注册表 + `register_adapter()` / `@adapter()` 装饰器 /
   `get_adapter()` / `resolve_adapter()`：新增 Agent 类型只需写一个 Adapter 并
   注册，不动 Executor 主干。

## 非目标（后续 Change 承接）

- 具体 Launcher 实现（Codex/Claude 子进程拉起）→ Story 102
- 具体 Trigger 实现（Webhook 唤醒 Runner）→ Story 103
- 执行器主循环（认领 → running → finalize）→ Story 104
- RunStatus 枚举对齐 / AgentSchedule 松绑 → Story 105/106

## 方案

### 模块位置

`agentboard/executor.py`（与 `scheduler.py` 平级），纯新增模块，零 REST 契约
变更、无数据库变更。

### 核心类型

```python
class AgentAdapter(ABC):
    name: str            # 注册键（默认类名小写）
    @abstractmethod
    def launch(self, run, task, ctx: AgentRunContext) -> RunHandle: ...
    @abstractmethod
    def poll_status(self, handle: RunHandle) -> RunStatus: ...
    def build_prompt(self, run, task, ctx) -> str: ...   # 默认 prompt 骨架

class LauncherAdapter(AgentAdapter):   # 模式 A：poll 基于 process.poll() 退出码
class TriggerAdapter(AgentAdapter):    # 模式 B：默认等待显式状态变更

class NotConfiguredAdapter(AgentAdapter):  # 兜底：launch 抛可读错误，poll 恒 FAILED
```

### 注册表

```python
ADAPTERS: dict[str, type[AgentAdapter]]
register_adapter(cls, *, name=None, replace=False)   # 函数式 / 装饰器式
adapter(name, *, replace=False)                       # 便捷装饰器
get_adapter(name, default=None)                       # 未注册抛 AdapterNotFound
resolve_adapter(name)                                 # 未注册回退 NotConfiguredAdapter
has_adapter(name) / registered_adapters()             # 查询
```

重复注册同名默认抛 `AdapterAlreadyRegistered`（同对象幂等），`replace=True` 覆盖。

## 验收

1. `AgentAdapter` / `LauncherAdapter` / `TriggerAdapter` 均不可直接实例化；
   实现抽象方法后可实例化。
2. 两种注册方式（函数式 / 装饰器）均可 `get_adapter` 取回，`cls.name` 与注册键一致。
3. 未注册名字抛 `AdapterNotFound`；`default` 参数与 `resolve_adapter` 兜底生效。
4. FakeAdapter 全生命周期：launch → RUNNING → complete → SUCCESS；
   Launcher 基于真实子进程退出码判定 SUCCESS / FAILED。
5. 新增 Agent 类型只需写 Adapter 并注册（pytest 证明扩展点，不触模块源码）。
6. 回归：既有 pytest 套件无新增失败。
7. 不得修改任何既有 REST 契约；不得触碰端口 18001。
