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
import os
import shlex
import subprocess
import time
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
    preserve_name: bool = False,
) -> type[AgentAdapter] | Any:
    """
    注册一个适配器类。

    支持两种用法::

        register_adapter(CodexLauncher)                      # name 取 cls.name 或类名小写
        register_adapter(CodexLauncher, name="codex")

        @register_adapter(name="codex")
        class CodexLauncher(LauncherAdapter): ...

    同名重复注册默认抛 ``AdapterAlreadyRegistered``；``replace=True`` 允许覆盖。

    ``preserve_name=True`` 时**不回写** ``cls.name``（默认 False：显式 name
    会回写类属性）。用于「一个类注册多个别名」的场景（如 workbuddy / qoder
    共享 WebhookTrigger），避免后续别名覆盖类的逻辑名。
    """

    def _register(cls: type[AgentAdapter]) -> type[AgentAdapter]:
        key = name or getattr(cls, "name", "") or cls.__name__.lower()
        if not key:
            raise ValueError("adapter must have a non-empty name")
        existing = ADAPTERS.get(key)
        if existing is not None and existing is not cls and not replace:
            raise AdapterAlreadyRegistered(key, existing)
        ADAPTERS[key] = cls
        if not preserve_name:
            cls.name = key  # 显式 name 回写类属性（保证 cls.name 与注册键一致）
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


# ===========================================================================
# Story 102 — 模式 A：Launcher（CLI Agent 主动拉起）
# ===========================================================================
#
# 在 Story 101 适配器框架之上实现真实 CLI Agent 拉取：
#   CliLauncher   —— 通用 CLI 基类：命令解析（env 覆盖）+ Popen + 输出捕获
#   CodexLauncher —— `codex exec` 非交互拉起
#   ClaudeLauncher —— `claude -p` 非交互拉起
#   launch_run()  —— 最小单次驱动：pending → running → success/failed 回写 DB
#
# 完整 daemon 主循环（轮询 pending / 并发认领 / 租约续期）留 Story 104。
# ===========================================================================

#: prompt 中项目记忆的最大字符数（防超长 prompt 撑爆 stdin/模型上下文）
MAX_MEMORY_CHARS = 8000

#: 验收标准段落（spec 中提取）的最大字符数
MAX_ACCEPTANCE_CHARS = 2000


class CliLauncher(LauncherAdapter):
    """
    模式 A 的 CLI 基类：负责把 Agent 任务转成 CLI 子进程拉起并捕获输出。

    - ``command``：默认命令列表（如 ``["codex", "exec", "--json"]``）；
    - ``env_var``：环境变量名（如 ``AGENTBOARD_CODEX_BIN``）。若设置了
      **完整命令串**（如 ``python /path/fake_codex.py --flag``），则以该串
      覆盖默认命令（``shlex.split`` 拆分，Windows 保留引号语义）。
    - prompt 通过 ``stdin`` 管道喂入（``communicate(input=prompt)``），
      避免超长参数受 OS 命令行长度限制；``stderr`` 合并进 ``stdout``，
      UTF-8 解码 + ``errors=replace`` 兼容 Windows 非 UTF-8 输出。
    """

    command: list[str] = []
    env_var: str = ""

    def build_command(self, ctx: AgentRunContext) -> list[str]:
        """解析最终命令：env 覆盖优先，否则默认命令。"""
        override = os.environ.get(self.env_var) if self.env_var else None
        if override:
            # Windows 下 shlex.split(posix=False) 保留引号；POSIX 用默认拆分
            parts = shlex.split(override, posix=os.name != "nt")
            if not parts:
                raise AdapterError(f"env {self.env_var} is empty")
            return parts
        if not self.command:
            raise AdapterError(f"{type(self).__name__} has no default command")
        return list(self.command)

    def build_prompt(self, run: Any, task: Any, ctx: AgentRunContext) -> str:
        """组装完整任务上下文：title + spec + 项目记忆 + 验收标准。"""
        lines = [f"你正在 AgentBoard 中执行一次 Agent 运行（run #{ctx.run_id}）。"]
        if ctx.project_name or ctx.project_key:
            proj = ctx.project_key or ""
            lines.append(f"项目：{ctx.project_name or ''}{(' (' + proj + ')') if proj else ''}")
        if ctx.task_title:
            lines.append(f"任务：{ctx.task_title}")
        if ctx.task_spec:
            lines.append(f"需求/规格：\n{ctx.task_spec}")
        if ctx.memory:
            lines.append(f"项目记忆：\n{ctx.memory}")
        acceptance = (ctx.extra or {}).get("acceptance")
        if acceptance:
            lines.append(f"验收标准：\n{acceptance}")
        lines.append(
            "\n请直接执行任务。完成后通过 AgentBoard MCP 自报进度（add_comment / "
            "set_status），无需向执行器回传细节。"
        )
        return "\n".join(lines)

    def launch(self, run: Any, task: Any, ctx: AgentRunContext) -> RunHandle:
        handle = RunHandle(run_id=ctx.run_id, adapter=ctx.agent).mark_running()
        command = self.build_command(ctx)
        prompt = self.build_prompt(run, task, ctx)
        timeout = self.timeout_seconds
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=dict(os.environ),
            )
        except FileNotFoundError as e:
            handle.fail(f"command not found: {command[0] if command else '?'} ({e})")
            return handle
        except OSError as e:
            handle.fail(f"failed to launch {command}: {e}")
            return handle

        handle.process = proc
        handle.metadata["command"] = command
        try:
            stdout, _ = proc.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()  # 收割僵尸进程
            handle.fail(f"timeout after {timeout}s: {command}")
            handle.finished_at = _now_utc()
            return handle
        except Exception as e:  # pragma: no cover - 防御性兜底
            proc.kill()
            proc.communicate()
            handle.fail(f"unexpected error: {e}")
            handle.finished_at = _now_utc()
            return handle

        handle.result = stdout or ""
        handle.finished_at = _now_utc()
        if proc.returncode == 0:
            handle.complete(stdout)
        else:
            handle.fail(f"process exited with code {proc.returncode}")
        return handle

    def poll_status(self, handle: RunHandle) -> RunStatus:
        # CLI 场景在 launch() 内同步等待退出码，poll 直接返回已判定的终态。
        # （保留父类基于 process.poll() 的判定作为兜底）
        if handle.status in (RunStatus.SUCCESS, RunStatus.FAILED):
            return handle.status
        return super().poll_status(handle)


@adapter("codex")
class CodexLauncher(CliLauncher):
    """Codex CLI Agent：`codex exec --json` 非交互拉起。

    环境变量 ``AGENTBOARD_CODEX_BIN`` 可覆盖完整命令（如
    ``python C:/fakes/codex.py``），便于测试注入 Fake CLI。
    """

    name = "codex"
    description = "Codex CLI Agent（codex exec，非交互 print 模式）"
    command = ["codex", "exec", "--json"]
    env_var = "AGENTBOARD_CODEX_BIN"


@adapter("claude")
class ClaudeLauncher(CliLauncher):
    """Claude Code CLI Agent：`claude -p` 非交互拉起。

    环境变量 ``AGENTBOARD_CLAUDE_BIN`` 可覆盖完整命令。
    """

    name = "claude"
    description = "Claude Code CLI Agent（claude -p，print 模式）"
    command = ["claude", "-p"]
    env_var = "AGENTBOARD_CLAUDE_BIN"


# ---------------------------------------------------------------------------
# 最小单次驱动
# ---------------------------------------------------------------------------
def _extract_acceptance(spec: str | None) -> str:
    """从 spec 中提取验收标准段落（含「验收」的行起，截断防超长）。"""
    if not spec:
        return ""
    lines = spec.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if "验收" in ln:
            start = i
            break
    if start is None:
        return ""
    block = "\n".join(lines[start:]).strip()
    return block[:MAX_ACCEPTANCE_CHARS]


def build_run_context(s, run: Any) -> AgentRunContext | None:
    """
    把 AgentRun 扁平化为 AgentRunContext（供 Adapter 使用，与 ORM 解耦）。

    - agent 名：env ``AGENTBOARD_DEFAULT_AGENT``（默认 codex；Story 106 松绑后
      改读 schedule 字段）；
    - memory：该项目 ``Document.type=memory`` 的内容拼接（截断）。
    """
    from .domains.projects.models import Project
    from .domains.scheduling.models import AgentSchedule
    from .domains.work_items.models import Task

    schedule = s.get(AgentSchedule, run.schedule_id)
    if schedule is None:
        log.warning("run %d has no schedule %s", run.id, run.schedule_id)
        return None
    project = s.get(Project, schedule.project_id)
    task = s.get(Task, run.task_id) if run.task_id else None

    ctx = AgentRunContext(
        project_id=schedule.project_id,
        schedule_id=schedule.id,
        run_id=run.id,
        task_id=run.task_id,
        agent=os.environ.get("AGENTBOARD_DEFAULT_AGENT", "codex"),
        project_key=project.key if project else None,
        project_name=project.name if project else None,
        task_title=task.title if task else None,
        task_spec=task.spec if task else None,
        memory="",
        extra={"schedule_title": schedule.title},
    )
    acceptance = _extract_acceptance(task.spec) if task else ""
    if acceptance:
        ctx.extra["acceptance"] = acceptance

    try:
        from .domains.documents.models import Document

        memories = (
            s.query(Document)
            .filter(
                Document.project_id == schedule.project_id,
                Document.type == "memory",
            )
            .order_by(Document.updated_at.desc())
            .all()
        )
        if memories:
            joined = "\n\n".join(doc.content or "" for doc in memories)
            ctx.memory = joined[:MAX_MEMORY_CHARS]
    except Exception:  # pragma: no cover - 记忆加载失败不影响主流程
        log.exception("failed to load project memory for run %d", run.id)

    # 模式 B：项目级 webhook 目标（WebhookTrigger 无 env 时的兜底来源）
    try:
        from .domains.work_items.models import WebhookConfig

        webhook = (
            s.query(WebhookConfig)
            .filter(
                WebhookConfig.project_id == schedule.project_id,
                WebhookConfig.enabled == True,  # noqa: E712
            )
            .order_by(WebhookConfig.created_at.desc())
            .first()
        )
        if webhook is not None:
            ctx.extra["webhook_url"] = webhook.url
            if webhook.secret:
                ctx.extra["webhook_secret"] = webhook.secret
    except Exception:  # pragma: no cover - webhook 配置加载失败不影响主流程
        log.exception("failed to load webhook config for run %d", run.id)
    return ctx


def launch_run(
    session_factory,
    run_id: int,
    *,
    poll_interval: float = 1.0,
    max_poll_seconds: float | None = None,
) -> dict | None:
    """
    最小单次驱动：认领一个 pending run → running → success/failed 回写 DB。

    - 非 pending 的 run 直接跳过（返回 None，避免重复执行）；
    - 完整 daemon 主循环（并发认领 / 租约续期 / 后台轮询）留 Story 104。

    Returns:
        回写后的 run 序列化 dict；run 不存在或非 pending 返回 None。
    """
    from . import service

    with session_factory() as s:
        run = s.get(service.AgentRun, run_id)
        if run is None:
            log.warning("run %d not found", run_id)
            return None
        if run.status != RunStatus.PENDING:
            log.info("run %d status=%s, skip (only pending executable)", run.id, run.status)
            return None

        ctx = build_run_context(s, run)
        if ctx is None:
            service.update_run(
                s, run.id, status=RunStatus.FAILED,
                error_message="run has no valid schedule/project context",
                finished_at=_now_utc(),
            )
            return service._ser(run)

        adapter_cls = resolve_adapter(ctx.agent)
        adapter = adapter_cls() if isinstance(adapter_cls, type) else adapter_cls
        # 显式 max_poll_seconds 覆盖适配器默认超时（CLI 在 launch 内同步等待，
        # 该值同时约束 communicate() 的等待，否则默认 1800s 会吞掉小超时）
        if max_poll_seconds is not None:
            try:
                adapter.timeout_seconds = max_poll_seconds
            except Exception:  # pragma: no cover - 属性不存在时忽略
                pass

        started = _now_utc()
        run = service.update_run(
            s, run.id, status=RunStatus.RUNNING, started_at=started,
        )
        log.info("run %d → running (agent=%s, adapter=%s)",
                 run.id, ctx.agent, type(adapter).__name__)

        try:
            handle = adapter.launch(run, None, ctx)
        except Exception as e:
            log.exception("launch failed for run %d", run.id)
            service.update_run(
                s, run.id, status=RunStatus.FAILED,
                error_message=f"launch failed: {e}",
                finished_at=_now_utc(),
            )
            return service._ser(run)

        # poll 至终态（CLI launch 已同步等待，通常首轮即终态；兜底轮询）
        deadline = None
        if max_poll_seconds is not None:
            deadline = time.monotonic() + max_poll_seconds
        final = None
        while True:
            final = adapter.poll_status(handle)
            if final in (RunStatus.SUCCESS, RunStatus.FAILED):
                break
            if deadline is not None and time.monotonic() >= deadline:
                proc = getattr(handle, "process", None)
                if proc is not None:
                    try:
                        proc.kill()
                    except Exception:  # pragma: no cover
                        pass
                final = RunStatus.FAILED
                handle.error = handle.error or f"timeout after {max_poll_seconds}s"
                break
            time.sleep(poll_interval)

        finished = _now_utc()
        if final == RunStatus.SUCCESS:
            run = service.update_run(
                s, run.id, status=RunStatus.SUCCESS,
                output=handle.result or "",
                finished_at=finished,
            )
            log.info("run %d → success", run.id)
        else:
            run = service.update_run(
                s, run.id, status=RunStatus.FAILED,
                error_message=handle.error or "agent execution failed",
                output=(handle.result or "")[:20000],
                finished_at=finished,
            )
            log.info("run %d → failed: %s", run.id, handle.error)
        return service._ser(run)


def launch_first_pending(
    session_factory,
    *,
    poll_interval: float = 1.0,
    max_poll_seconds: float | None = None,
) -> dict | None:
    """执行第一个 pending run（按 id 升序），返回其结果或 None。"""
    from .domains.scheduling.models import AgentRun

    with session_factory() as s:
        run = (
            s.query(AgentRun)
            .filter(AgentRun.status == RunStatus.PENDING)
            .order_by(AgentRun.id.asc())
            .first()
        )
        if run is None:
            return None
        rid = run.id
    return launch_run(session_factory, rid, poll_interval=poll_interval,
                      max_poll_seconds=max_poll_seconds)


# ===========================================================================
# Story 103 — 模式 B：Trigger（Webhook 唤醒常驻 Runner）
# ===========================================================================
#
# 在 Story 101 TriggerAdapter 骨架之上实现真实 webhook 触发：
#   WebhookTrigger —— 把 pending run 打包成事件 POST 给常驻 Runner
#                     （WorkBuddy / QoderWork 自动化），Runner 被叫醒后
#                     直奔指定 task 执行，不再全量 list_tasks 轮询。
#   trigger_run()  —— 最小单次驱动：pending → running → POST webhook
#                     → 轮询 DB run.status（外部经 report_run_result 回写，
#                     Story 104 落地 MCP 工具）→ success/failed 或超时。
#
# 目标 URL 来源优先级：
#   1) env AGENTBOARD_TRIGGER_URL（测试 / 全局覆盖）
#   2) 项目级 WebhookConfig（复用 create_webhook 基础设施，取 enabled 第一个）
# 完成判定：poll_status 默认等待显式状态变更（TriggerAdapter 语义），
#   trigger_run 轮询 DB run.status 感知外部回写。
# ===========================================================================

#: 走 Trigger 模式的 agent 名（常驻 Runner 场景）
TRIGGER_AGENTS = ("workbuddy", "qoder")

#: webhook 事件名（与既有 fire_webhook 事件生态同构）
EVENT_RUN_TRIGGERED = "agent_run.triggered"


class WebhookTrigger(TriggerAdapter):
    """
    模式 B：通过 Webhook POST 唤醒常驻 Runner（WorkBuddy / QoderWork）。

    ``launch()`` 把 pending run 打包成事件负载 POST 到目标 URL，非 2xx 抛
    ``AdapterError``（由 Executor 主循环转为 failed）。完成判定默认依赖
    ``report_run_result`` 显式回写（Story 104 MCP 工具），``trigger_run``
    轮询 DB run.status 感知。

    事件负载结构::

        {
          "event": "agent_run.triggered",
          "timestamp": "1739000000",
          "data": {
            "run_id": 1, "task_id": 2, "project_id": 3, "schedule_id": 4,
            "agent": "workbuddy",
            "task_title": "...", "task_spec": "...",
            "prompt": "<build_prompt 输出>",
            "token": "<env AGENTBOARD_TRIGGER_TOKEN，非 admin scoped token>",
          }
        }

    配置了 secret 时附加 ``X-AgentBoard-Signature`` / ``X-AgentBoard-Timestamp``
    头（HMAC-SHA256，与既有 ``fire_webhook`` 签名模式一致）。
    """

    name = "webhook"
    description = "Webhook 唤醒常驻 Runner（WorkBuddy / QoderWork 自动化）"
    timeout_seconds = 3600.0
    url_env_var = "AGENTBOARD_TRIGGER_URL"
    token_env_var = "AGENTBOARD_TRIGGER_TOKEN"

    def build_payload(self, run: Any, task: Any, ctx: AgentRunContext,
                      *, prompt: str | None = None) -> dict[str, Any]:
        """组装事件负载：event + run 快照 + 供 Runner 直取任务的字段。"""
        import time as _time
        return {
            "event": EVENT_RUN_TRIGGERED,
            "timestamp": str(int(_time.time())),
            "data": {
                "run_id": ctx.run_id,
                "task_id": ctx.task_id,
                "project_id": ctx.project_id,
                "schedule_id": ctx.schedule_id,
                "agent": ctx.agent,
                "task_title": ctx.task_title,
                "task_spec": ctx.task_spec,
                "prompt": prompt or self.build_prompt(run, task, ctx),
                "token": os.environ.get(self.token_env_var, ""),
            },
        }

    def resolve_url(self, ctx: AgentRunContext) -> tuple[str, str | None]:
        """目标 URL + secret：env 优先，其次 ctx.extra（来自项目级 WebhookConfig）。"""
        url = os.environ.get(self.url_env_var)
        if url:
            return url, None
        url = (ctx.extra or {}).get("webhook_url")
        if url:
            return url, (ctx.extra or {}).get("webhook_secret")
        raise AdapterError(
            f"no webhook target for agent '{ctx.agent}': set {self.url_env_var} "
            "or configure a project-level WebhookConfig"
        )

    def launch(self, run: Any, task: Any, ctx: AgentRunContext) -> RunHandle:
        import hashlib
        import hmac
        import json

        import httpx

        handle = RunHandle(run_id=ctx.run_id, adapter=ctx.agent).mark_running()
        url, secret = self.resolve_url(ctx)
        payload = self.build_payload(run, task, ctx)
        body = json.dumps(payload, ensure_ascii=False)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AgentBoard-Trigger/1.0",
        }
        if secret:
            signature = hmac.new(
                secret.encode(), body.encode(), hashlib.sha256,
            ).hexdigest()
            headers["X-AgentBoard-Signature"] = signature
            headers["X-AgentBoard-Timestamp"] = payload["timestamp"]
        handle.metadata["url"] = url
        try:
            resp = httpx.post(url, content=body, headers=headers, timeout=15.0)
        except Exception as e:
            raise AdapterError(f"webhook POST failed: {e}") from e
        handle.metadata["status_code"] = resp.status_code
        handle.metadata["response"] = (resp.text or "")[:500]
        if not 200 <= resp.status_code < 300:
            raise AdapterError(
                f"webhook returned {resp.status_code}: {(resp.text or '')[:200]}"
            )
        log.info("run %d webhook triggered → %s (%s)", ctx.run_id, url, resp.status_code)
        return handle

    def poll_status(self, handle: RunHandle) -> RunStatus:
        # 完成判定走外部 report_run_result 回写；保持当前状态即可
        return handle.status


#: 按名字注册 Trigger 适配器（同一实现服务多个常驻 Runner；
#: preserve_name 防止别名覆盖类的逻辑名 "webhook"）
register_adapter(WebhookTrigger, name="workbuddy", preserve_name=True)
register_adapter(WebhookTrigger, name="qoder", preserve_name=True)


def trigger_run(
    session_factory,
    run_id: int,
    *,
    poll_interval: float = 1.0,
    max_poll_seconds: float | None = None,
) -> dict | None:
    """
    模式 B 最小单次驱动：pending → running → POST webhook → 轮询 DB
    run.status（外部经 report_run_result 回写）→ success/failed 或超时。

    - 仅对 agent ∈ ``TRIGGER_AGENTS``（workbuddy / qoder）有意义；
    - 非 pending 的 run 跳过（返回 None，避免重复触发）。
    """
    from . import service

    with session_factory() as s:
        run = s.get(service.AgentRun, run_id)
        if run is None:
            log.warning("run %d not found", run_id)
            return None
        if run.status != RunStatus.PENDING:
            log.info("run %d status=%s, skip (only pending executable)", run.id, run.status)
            return None

        ctx = build_run_context(s, run)
        if ctx is None:
            service.update_run(
                s, run.id, status=RunStatus.FAILED,
                error_message="run has no valid schedule/project context",
                finished_at=_now_utc(),
            )
            return service._ser(run)
        if ctx.agent not in TRIGGER_AGENTS:
            log.warning("run %d agent=%s not in %s, use launch_run instead",
                        run.id, ctx.agent, TRIGGER_AGENTS)
            return None

        adapter_cls = resolve_adapter(ctx.agent)
        adapter = adapter_cls() if isinstance(adapter_cls, type) else adapter_cls
        if max_poll_seconds is not None:
            try:
                adapter.timeout_seconds = max_poll_seconds
            except Exception:  # pragma: no cover
                pass

        run = service.update_run(
            s, run.id, status=RunStatus.RUNNING, started_at=_now_utc(),
        )
        log.info("run %d → running (agent=%s, adapter=%s)",
                 run.id, ctx.agent, type(adapter).__name__)
        try:
            handle = adapter.launch(run, None, ctx)
        except Exception as e:
            log.exception("webhook trigger failed for run %d", run.id)
            service.update_run(
                s, run.id, status=RunStatus.FAILED,
                error_message=f"webhook trigger failed: {e}",
                finished_at=_now_utc(),
            )
            return service._ser(run)
        handle_error = getattr(handle, "error", None)

    # ---- 轮询 DB：外部（Runner / report_run_result）回写终态 ----
    deadline = None
    if max_poll_seconds is not None:
        deadline = time.monotonic() + max_poll_seconds
    final = RunStatus.RUNNING
    while final == RunStatus.RUNNING:
        with session_factory() as s:
            cur = s.get(service.AgentRun, run_id)
            if cur is None:
                return None
            final = RunStatus(cur.status)
        if final in (RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.CANCELLED):
            break
        if deadline is not None and time.monotonic() >= deadline:
            final = RunStatus.FAILED
            handle_error = handle_error or f"timeout after {max_poll_seconds}s"
            break
        time.sleep(poll_interval)

    finished = _now_utc()
    with session_factory() as s:
        cur = s.get(service.AgentRun, run_id)
        if cur is None:
            return None
        if final == RunStatus.SUCCESS and cur.status == RunStatus.SUCCESS:
            if cur.finished_at is None:
                cur = service.update_run(s, run_id, finished_at=finished)
            log.info("run %d → success (external report)", run_id)
            return service._ser(cur)
        if final == RunStatus.CANCELLED:
            if cur.finished_at is None:
                cur = service.update_run(s, run_id, finished_at=finished)
            return service._ser(cur)
        # FAILED：外部已回写 failed 或本执行器超时兜底
        if cur.status != RunStatus.FAILED:
            cur = service.update_run(
                s, run_id, status=RunStatus.FAILED,
                error_message=handle_error or "webhook trigger failed",
                finished_at=finished,
            )
        elif cur.finished_at is None:
            cur = service.update_run(s, run_id, finished_at=finished)
        log.info("run %d → failed: %s", run_id, cur.error_message)
        return service._ser(cur)


def trigger_first_pending(
    session_factory,
    *,
    poll_interval: float = 1.0,
    max_poll_seconds: float | None = None,
) -> dict | None:
    """触发第一个 pending 且 agent ∈ TRIGGER_AGENTS 的 run（按 id 升序）。"""
    from .domains.scheduling.models import AgentRun

    with session_factory() as s:
        runs = (
            s.query(AgentRun)
            .filter(AgentRun.status == RunStatus.PENDING)
            .order_by(AgentRun.id.asc())
            .all()
        )
        for r in runs:
            ctx = build_run_context(s, r)
            if ctx is not None and ctx.agent in TRIGGER_AGENTS:
                rid = r.id
                break
        else:
            return None
    return trigger_run(session_factory, rid, poll_interval=poll_interval,
                       max_poll_seconds=max_poll_seconds)


def main() -> None:
    """CLI 入口：python -m agentboard.executor --run <id> / --once / --trigger <id>"""
    import argparse

    from . import database as _db

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="AgentBoard Executor (Story 102/103)")
    parser.add_argument("--run", type=int, help="execute a specific run id")
    parser.add_argument("--once", action="store_true",
                        help="execute the first pending run and exit")
    parser.add_argument("--trigger", type=int,
                        help="webhook-trigger a specific run id (workbuddy/qoder)")
    parser.add_argument("--trigger-once", action="store_true",
                        help="webhook-trigger the first pending workbuddy/qoder run")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--max-poll-seconds", type=float, default=None,
                        help="timeout for one run (default: adapter timeout)")
    args = parser.parse_args()

    if args.run:
        result = launch_run(_db.session_scope, args.run,
                            poll_interval=args.poll_interval,
                            max_poll_seconds=args.max_poll_seconds)
        if result is None:
            print(f"run {args.run}: not found or not pending (nothing to do)")
        else:
            print(f"run {args.run}: status={result.get('status')} "
                  f"output_len={len(result.get('output') or '')} "
                  f"error={result.get('error_message') or ''!r}")
    elif args.once:
        result = launch_first_pending(_db.session_scope,
                                      poll_interval=args.poll_interval,
                                      max_poll_seconds=args.max_poll_seconds)
        if result is None:
            print("no pending run (nothing to do)")
        else:
            print(f"run {result.get('id')}: status={result.get('status')}")
    elif args.trigger:
        result = trigger_run(_db.session_scope, args.trigger,
                             poll_interval=args.poll_interval,
                             max_poll_seconds=args.max_poll_seconds)
        if result is None:
            print(f"run {args.trigger}: not found / not pending / not a "
                  f"trigger-agent run (nothing to do)")
        else:
            print(f"run {args.trigger}: status={result.get('status')} "
                  f"error={result.get('error_message') or ''!r}")
    elif args.trigger_once:
        result = trigger_first_pending(_db.session_scope,
                                       poll_interval=args.poll_interval,
                                       max_poll_seconds=args.max_poll_seconds)
        if result is None:
            print("no pending workbuddy/qoder run (nothing to do)")
        else:
            print(f"run {result.get('id')}: status={result.get('status')}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
