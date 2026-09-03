"""Workspace / Runtime provider（M6 T6.5）。

给 Agent 执行提供**受策略约束的工作区与运行时**，且只做参数构造 ——
git / docker 的真实执行仍由既有 worker 逻辑负责。

- :mod:`.models`    provider 枚举、:class:`RepoSpec` / :class:`WorkspaceHandle` /
                    :class:`CleanupPlan` / :class:`ExecutionPlan`
- :mod:`.providers` ``prepare_workspace`` / ``cleanup_workspace`` /
                    ``effective_max_parallel``
- :mod:`.runtime`   host / docker 执行计划构造

两条硬约束：``existing_checkout`` 强制 ``max_parallel=1``；``create_branch``
非 AUTO 时 worktree 一律 ``--detach``（不偷建业务分支）。
"""
from __future__ import annotations

from .models import (
    FORCED_MAX_PARALLEL,
    CleanupPlan,
    ExecutionPlan,
    RepoSpec,
    RuntimeProviderKind,
    WorkspaceHandle,
    WorkspaceProviderKind,
)
from .providers import (
    cleanup_workspace,
    effective_max_parallel,
    prepare_workspace,
)
from .runtime import (
    DEFAULT_DOCKER_IMAGE,
    DockerRuntimeProvider,
    HostRuntimeProvider,
    RuntimeProvider,
    get_runtime_provider,
)

__all__ = [
    "WorkspaceProviderKind",
    "RuntimeProviderKind",
    "FORCED_MAX_PARALLEL",
    "RepoSpec",
    "WorkspaceHandle",
    "CleanupPlan",
    "ExecutionPlan",
    "prepare_workspace",
    "cleanup_workspace",
    "effective_max_parallel",
    "RuntimeProvider",
    "HostRuntimeProvider",
    "DockerRuntimeProvider",
    "get_runtime_provider",
    "DEFAULT_DOCKER_IMAGE",
]
