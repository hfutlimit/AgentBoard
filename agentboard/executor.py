"""
AgentRun 执行器适配器框架（Epic 78 Story 101）
=================================================
执行器与具体 Agent 之间的解耦层。

- ``AgentAdapter(ABC)``：核心抽象 —— ``launch(run, task, ctx) -> RunHandle``
  与 ``poll_status(handle) -> RunStatus``。
- ``LauncherAdapter`` / ``TriggerAdapter``：两类场景基类
  （模式 A 直接 spawn CLI Agent 子进程；模式 B 通过 Webhook 唤醒常驻 Runner）。
- ``RunHandle`` / ``AgentRunContext``：运行句柄与执行上下文。
- ``ADAPTERS`` 注册表：按 agent 名（codex / claude / workbuddy / qoder）分发；
  新增 Agent 类型只需写一个 Adapter 并注册，无需改动 Executor 主干。

本模块为纯新增代码，不修改任何既有 REST 契约。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .domains.common.enums import RunStatus

log = logging.getLogger("agentboard.executor")

#: 内置预留的 agent 名字（后续 Story 102/103 交付具体实现）
KNOWN_AGENTS = ("codex", "claude", "workbuddy", "qoder")


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class AdapterError(Exception):
    """适配器执行过程中的错误（启动失败 / 执行异常）。"""


class AdapterNotFound(KeyError):
    """注册表中不存在指定名字的适配器。"""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"no adapter registered for agent '{name}'")


class AdapterAlreadyRegistered(AdapterError):
    """同名适配器重复注册（除非 replace=True）。"""

    def __init__(self, name: str, existing: type) -> None:
        self.name = name
        self.existing = existing
        super().__init__(f"adapter '{name}' already registered as {existing.__name__}")


# ---------------------------------------------------------------------------
# 数据载体
# ---------------------------------------------------------------------------
@dataclass
class AgentRunContext:
    """
    一次 Agent 运行的执行上下文（供后续 prompt 组装 / 参数注入）。

    ``task`` / ``run`` 为 ORM 对象时由 Executor 主循环负责抽取，本类只承载
    扁平化字段，避免 Adapter 与 ORM 强耦合。
    """

    project_id: int
    schedule_id: int
    run_id: int
    task_id: int | None = None
    agent: str = "workbuddy"
    project_key: str | None = None
    project_name: str | None = None
    epic_id: int | None = None
    story_id: int | None = None
    task_title: str | None = None
    task_spec: str | None = None
    memory: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "schedule_id": self.schedule_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "agent": self.agent,
            "project_key": self.project_key,
            "project_name": self.project_name,
            "epic_id": self.epic_id,
            "story_id": self.story_id,
            "task_title": self.task_title,
            "task_spec": self.task_spec,
            "memory": self.memory,
            "extra": dict(self.extra),
        }


@dataclass
class RunHandle:
    """
    一次 Agent 执行的运行句柄：由 ``launch()`` 返回，``poll_status()`` 消费。

    子进程场景可挂 ``process``（由 LauncherAdapter 负责填充），
    poll_status 通过 ``process.poll()`` 判定完成。
    """

    run_id: int
    adapter: str
    status: RunStatus = RunStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    log_ref: str | None = None
    poll_interval: float = 5.0
    timeout: float | None = None
    process: Any = None
    result: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def mark_running(self) -> "RunHandle":
        self.status = RunStatus.RUNNING
        if self.started_at is None:
            self.started_at = _now_utc()
        return self

    def complete(self, result: str | None = None) -> "RunHandle":
        self.status = RunStatus.SUCCESS
        self.finished_at = _now_utc()
        if result is not None:
            self.result = result
        return self

    def fail(self, error: str) -> "RunHandle":
        self.status = RunStatus.FAILED
        self.finished_at = _now_utc()
        self.error = error
        return self


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------
class AgentAdapter(ABC):
    """
    Agent 执行适配器抽象基类。

    子类必须实现 ``launch`` 与 ``poll_status``；``build_prompt`` 提供默认
    实现（子类可覆写）。``name`` 默认取类名小写，可通过类属性显式覆盖。
    """

    #: 适配器逻辑名（agent 名）；注册进 ADAPTERS 的键
    name: str = ""

    #: 人类可读描述
    description: str = ""

    @abstractmethod
    def launch(self, run: Any, task: Any, ctx: AgentRunContext) -> RunHandle:
        """
        启动一次 Agent 执行。

        - Launcher：spawn 子进程（或调 SDK），返回挂有 ``process`` 的 RunHandle；
        - Trigger：POST webhook 唤醒 Runner，返回即可（完成判定走回调/超时）。

        启动失败抛 ``AdapterError``，由 Executor 主循环转为 failed。
        """
        raise NotImplementedError

    @abstractmethod
    def poll_status(self, handle: RunHandle) -> RunStatus:
        """
        判定一次执行是否完成。

        - 返回 ``RUNNING``：仍在执行，Executor 会继续轮询；
        - 返回 ``SUCCESS`` / ``FAILED``：终态，Executor 停止轮询并 finalize。
        """
        raise NotImplementedError

    def build_prompt(self, run: Any, task: Any, ctx: AgentRunContext) -> str:
        """组装 Agent 提示词（子类可覆写；默认给基础骨架）。"""
        lines = [f"你正在 AgentBoard 中执行一次 Agent 运行（run #{ctx.run_id}）。"]
        if ctx.task_title:
            lines.append(f"任务：{ctx.task_title}")
        if ctx.task_spec:
            lines.append(f"需求/规格：\n{ctx.task_spec}")
        if ctx.memory:
            lines.append(f"项目记忆：\n{ctx.memory}")
        return "\n".join(lines)


class LauncherAdapter(AgentAdapter):
    """
    模式 A：直接拉起 CLI Agent 子进程（Codex / Claude / WorkBuddy CLI 等）。

    ``poll_status`` 基于 ``handle.process.poll()`` 判定退出码；
    ``timeout_seconds`` 供 Executor 主循环超时兜底（0 / None = 不超时）。
    """

    timeout_seconds: float | None = 1800.0

    def poll_status(self, handle: RunHandle) -> RunStatus:
        proc = handle.process
        if proc is None:
            # 无子进程句柄：保持当前状态（由子类决定何时置终态）
            return handle.status
        code = proc.poll()
        if code is None:
            return RunStatus.RUNNING
        handle.finished_at = _now_utc()
        if code == 0:
            handle.status = RunStatus.SUCCESS
        else:
            handle.status = RunStatus.FAILED
            handle.error = f"process exited with code {code}"
        return handle.status


class TriggerAdapter(AgentAdapter):
    """
    模式 B：通过 Webhook / Runner API 唤醒常驻 Agent。

    完成判定通常靠回调或 ``report_run_result`` 显式回写，Executor 轮询
    到终态即 finalize；子类可覆写 ``poll_status`` 实现回调探测。
    """

    timeout_seconds: float | None = 3600.0

    def poll_status(self, handle: RunHandle) -> RunStatus:
        # 默认实现：等待显式状态变更（由 Executor / report_run_result 置终态）
        return handle.status


# ---------------------------------------------------------------------------
# 占位适配器
# ---------------------------------------------------------------------------
class NotConfiguredAdapter(AgentAdapter):
    """
    兜底适配器：注册表中没有该 agent 时使用。

    ``launch`` 抛可读错误，``poll_status`` 恒返回 FAILED ——
    保证 Executor 主循环不会因 KeyError 裸崩，而是落一条可读的失败记录。
    """

    name = "__not_configured__"
    description = "占位适配器：表示该 agent 尚未配置具体执行适配器"

    def launch(self, run: Any, task: Any, ctx: AgentRunContext) -> RunHandle:
        raise AdapterError(
            f"no adapter configured for agent '{ctx.agent}' "
            f"(registered adapters: {sorted(registered_adapters())})"
        )

    def poll_status(self, handle: RunHandle) -> RunStatus:
        handle.status = RunStatus.FAILED
        handle.error = handle.error or "not configured"
        return RunStatus.FAILED


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
ADAPTERS: dict[str, type[AgentAdapter]] = {}


def register_adapter(
    cls: type[AgentAdapter] | None = None,
    *,
    name: str | None = None,
    replace: bool = False,
) -> type[AgentAdapter] | Any:
    """
    注册一个适配器类。

    支持两种用法::

        register_adapter(CodexLauncher)                      # name 取 cls.name 或类名小写
        register_adapter(CodexLauncher, name="codex")

        @register_adapter(name="codex")
        class CodexLauncher(LauncherAdapter): ...

    同名重复注册默认抛 ``AdapterAlreadyRegistered``；``replace=True`` 允许覆盖。
    """

    def _register(cls: type[AgentAdapter]) -> type[AgentAdapter]:
        key = name or getattr(cls, "name", "") or cls.__name__.lower()
        if not key:
            raise ValueError("adapter must have a non-empty name")
        existing = ADAPTERS.get(key)
        if existing is not None and existing is not cls and not replace:
            raise AdapterAlreadyRegistered(key, existing)
        ADAPTERS[key] = cls
        if getattr(cls, "name", "") != key:
            cls.name = key  # 保证 cls.name 与注册键一致
        log.debug("adapter '%s' registered (%s)", key, cls.__name__)
        return cls

    if cls is None:
        # 装饰器用法：@register_adapter(name="x")
        return _register
    return _register(cls)


def adapter(name: str, *, replace: bool = False) -> Any:
    """
    装饰器形式的便捷注册::

        @adapter("codex")
        class CodexLauncher(LauncherAdapter): ...
    """
    return register_adapter(name=name, replace=replace)


def get_adapter(name: str, default: Any = None) -> type[AgentAdapter] | Any:
    """
    按 agent 名取适配器类；未注册时返回 ``default``（若为 None 则抛 AdapterNotFound）。
    """
    cls = ADAPTERS.get(name)
    if cls is not None:
        return cls
    if default is not None:
        return default
    raise AdapterNotFound(name)


def has_adapter(name: str) -> bool:
    return name in ADAPTERS


def registered_adapters() -> list[str]:
    return sorted(ADAPTERS.keys())


def resolve_adapter(name: str) -> type[AgentAdapter]:
    """取适配器；未注册时返回 NotConfiguredAdapter 兜底（永不抛 KeyError）。"""
    return get_adapter(name, default=NotConfiguredAdapter)
