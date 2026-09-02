"""Runtime provider：host / docker 的执行参数构造（M6 T6.5）。

同样是**只构造不执行**：产出 :class:`ExecutionPlan` 交给既有 worker 的
subprocess 路径执行，保持本包零副作用、可单测。

接入点建议
----------
``agent_runtime/invokers.py::SubprocessAgentInvoker`` 目前直接 ``subprocess.run``
本机命令。接入方式：在 invoker 里持有一个 provider（默认
``get_runtime_provider(RuntimeProviderKind.HOST)``），把
``build_execution_plan(...).argv`` 传给既有 subprocess 调用，``cwd`` 用
:class:`~.models.WorkspaceHandle` 的 ``path``（见 ``providers.prepare_workspace``）。
host 是默认且行为与当前完全一致，因此接入是零风险的灰度切入口。

两个 provider 用 Protocol 描述（与 ``agent_runtime/config.py`` 的
``AgentInvoker`` 同一套约定），便于后续替换实现而不动调用方。
"""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from ...core.exceptions import InvalidValue
from .models import ExecutionPlan, RuntimeProviderKind

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_DOCKER_IMAGE",
    "RuntimeProvider",
    "HostRuntimeProvider",
    "DockerRuntimeProvider",
    "get_runtime_provider",
]

#: 默认运行时镜像。容器里跑 Agent 时用它；留空则 DockerRuntimeProvider 报错，
#: 避免拼出 ``docker run  <cmd>`` 这种把命令当镜像名的畸形 argv。
DEFAULT_DOCKER_IMAGE = "agentboard/agent-runtime:latest"

#: 容器内的工作目录（宿主 handle.path 挂载到这里）。
CONTAINER_WORKDIR = "/workspace"


def _normalize_argv(argv: object) -> tuple[str, ...]:
    if isinstance(argv, str):
        raise InvalidValue("argv 必须是字符串序列，不接受整条命令行字符串（分词的歧义太大）")
    try:
        items = tuple(str(a) for a in argv)  # type: ignore[call-overload]
    except TypeError as exc:
        raise InvalidValue(f"argv 必须是可迭代的字符串序列：{argv!r}") from exc
    if not items or not all(items):
        raise InvalidValue("argv 不能为空或含空元素")
    return items


@runtime_checkable
class RuntimeProvider(Protocol):
    """执行环境适配器：把"要跑的命令"翻译成该环境下的 argv。"""

    kind: RuntimeProviderKind

    def build(
        self,
        argv: tuple[str, ...],
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        network: bool = True,
    ) -> ExecutionPlan:
        """构造执行计划。

        ``network`` 默认 ``True``（与 host 行为一致）：默认断网会让 git
        push / 依赖安装静默失败，是否断网属于策略问题，应由调用方显式决定。
        """
        ...


class HostRuntimeProvider:
    """直接在 Worker 宿主机执行（既有行为，零迁移成本）。"""

    kind = RuntimeProviderKind.HOST

    def build(
        self,
        argv: tuple[str, ...],
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        network: bool = True,
    ) -> ExecutionPlan:
        return ExecutionPlan(
            runtime=self.kind,
            argv=_normalize_argv(argv),
            cwd=cwd,
            env=dict(env or {}),
        )


class DockerRuntimeProvider:
    """在容器里执行：宿主工作目录挂载进 ``/workspace``。

    用 ``--rm``（一次性容器，不留残留）+ ``-w``（固定工作目录）。``env``
    显式透传：容器不会继承宿主环境变量，不传就会丢 token / 代理配置。
    """

    kind = RuntimeProviderKind.DOCKER

    def __init__(self, image: str = DEFAULT_DOCKER_IMAGE) -> None:
        if not (image or "").strip():
            raise InvalidValue("docker runtime 需要非空镜像名")
        self.image = image.strip()

    def build(
        self,
        argv: tuple[str, ...],
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        network: bool = True,
    ) -> ExecutionPlan:
        if not (cwd or "").strip():
            raise InvalidValue("docker runtime 需要宿主工作目录（挂载源）")
        items = _normalize_argv(argv)
        prefix: list[str] = [
            "docker", "run", "--rm",
            "-v", f"{cwd}:{CONTAINER_WORKDIR}",
            "-w", CONTAINER_WORKDIR,
        ]
        if not network:
            prefix += ["--network", "none"]
        environ = dict(env or {})
        for key, value in environ.items():
            prefix += ["-e", f"{key}={value}"]
        plan = ExecutionPlan(
            runtime=self.kind,
            argv=(*prefix, self.image, *items),
            cwd=cwd,
            env=environ,
            image=self.image,
        )
        # docker argv 很长且拼接来源多（挂载、env、镜像），出问题时要能直接
        # 从日志复制出来复现，因此 debug 级打印完整命令。
        log.debug("docker 执行计划：%s", " ".join(plan.argv))
        return plan


def get_runtime_provider(
    kind: RuntimeProviderKind | str,
    *,
    image: str = DEFAULT_DOCKER_IMAGE,
) -> RuntimeProvider:
    """按枚举取 provider，未知值抛 :class:`InvalidValue`。"""
    try:
        resolved = RuntimeProviderKind(str(kind).strip().lower())
    except ValueError as exc:
        raise InvalidValue(
            f"未知 runtime provider={kind!r}，"
            f"仅允许 {[p.value for p in RuntimeProviderKind]}"
        ) from exc
    if resolved is RuntimeProviderKind.DOCKER:
        return DockerRuntimeProvider(image=image)
    return HostRuntimeProvider()
