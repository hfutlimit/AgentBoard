"""Workspace provider：按策略构造工作区句柄与回收计划（M6 T6.5）。

**本模块不执行任何 git 命令**，只产出 :class:`WorkspaceHandle` / :class:`CleanupPlan`
给既有 worker 去跑。这样策略判定可单测，接入既有执行路径时也不会一次性
引入副作用。

接入点建议（既有代码不动，按此顺序逐步接入）
------------------------------------------
1. ``processors/invokers.py::_resolve_project_cwd`` 是当前的"项目本地
   目录"真源（读 ``AGENTBOARD_LOCAL_MAPPINGS`` / ``tmp/project-mappings.json``）。
   ``SubprocessProcessorInvoker.invoke`` 里拿到 cwd 之后，改为调用
   :func:`prepare_workspace`：``handle.path`` 取代裸 cwd 传给 subprocess，
   ``handle.git_args`` 在 agent 启动前执行一次。
2. ``processors/handlers/*.py`` 的 ``_resolve_project_dir``（clarify /
   story / ticket 各有一份）只用于拼 prompt，可继续用既有逻辑；等第 1 步
   接入后再统一改成读 ``handle.path``，避免三处各自漂移。
3. ``AgentBehaviorConfig.preparation.checkout_branch`` 是旧的布尔开关，接
   入后应改为读 :class:`~agentboard.features.policy.models.ActionPolicyLevel`：
   ``create_branch`` 非 AUTO 时本模块会自动 ``--detach``，比布尔量更能表达
   "审批/人工"两态。
4. 回收：任务终态（success / failed / cancelled）时调 :func:`cleanup_workspace`，
   由 worker 执行 ``plan.argv`` 并删除 ``plan.remove_path``。
"""
from __future__ import annotations

import logging

from ...core.exceptions import InvalidValue
from ...features.policy.models import ActionPolicyLevel
from .models import (
    FORCED_MAX_PARALLEL,
    CleanupPlan,
    RepoSpec,
    WorkspaceHandle,
    WorkspaceProviderKind,
)

log = logging.getLogger(__name__)

__all__ = [
    "effective_max_parallel",
    "prepare_workspace",
    "cleanup_workspace",
]

#: 工作目录布局：``<root>/.worktrees/task-<id>``、``<root>/.clones/task-<id>``。
#: 固定命名（而非随机临时目录）是为了可去重、可预测、可在崩溃后回收。
_WORKTREE_DIRNAME = ".worktrees"
_CLONE_DIRNAME = ".clones"


def _join(root: str, *parts: str) -> str:
    root = root.rstrip("/\\")
    return "/".join([root, *parts])


def effective_max_parallel(
    provider: WorkspaceProviderKind, requested: int,
) -> int:
    """收敛并行度：``existing_checkout`` 一律强制为 1。

    为什么强制：该模式复用同一个工作目录，两个任务并行会互相覆盖未提交
    改动、互相切分支，故障表现是"代码莫名其妙被回滚"，极难排查。调用方
    传了 ``>1`` 时记一条 warning（而不是抛错）—— 配置值本身无害，只是本
    模式下无法满足，降级即可。
    """
    cap = FORCED_MAX_PARALLEL.get(provider)
    if cap is not None and requested > cap:
        log.warning(
            "workspace provider=%s 不支持并行（共用同一工作目录），"
            "max_parallel %s 已强制收敛为 %s", provider, requested, cap,
        )
        return cap
    if requested < 1:
        return 1
    return int(requested)


def prepare_workspace(
    repo: RepoSpec,
    *,
    provider: WorkspaceProviderKind | str,
    task_id: int,
    branch: str | None = None,
    create_branch_policy: ActionPolicyLevel | str = ActionPolicyLevel.AUTO,
    max_parallel: int = 1,
    workspace_root: str = "",
) -> WorkspaceHandle:
    """按策略构造一次工作区准备计划（**不执行**）。

    参数
    ----
    repo:                 仓库来源（url / local_path / default_branch）；
    provider:             ``worktree`` / ``fresh_clone`` / ``existing_checkout``；
    task_id:              任务 ID，用于生成确定性工作目录；
    branch:               目标业务分支（``None`` = 不指定）；
    create_branch_policy: ``create_branch`` 的生效策略等级；
    max_parallel:         期望并行度（``existing_checkout`` 下会被收敛为 1）；
    workspace_root:       worktree / clone 的父目录，留空则用 ``repo.local_path``。

    策略判定（``detach``）
    ----------------------
    只有 ``create_branch == AUTO`` 且给了 ``branch`` 才会建/切业务分支；
    其余等级（DENY / MANUAL / APPROVAL）一律 ``detach=True``。

    APPROVAL 也走 detach 的理由：审批是**执行期**才可能发生的事，而
    ``prepare`` 发生在 agent 启动前，此时没有任何审批记录；若在 prepare
    阶段就带上分支创建参数，等于预支了一次尚未发生的审批。
    """
    try:
        kind = WorkspaceProviderKind(str(provider).strip().lower())
    except ValueError as exc:
        raise InvalidValue(
            f"未知 workspace provider={provider!r}，"
            f"仅允许 {[p.value for p in WorkspaceProviderKind]}"
        ) from exc
    repo.require(kind)

    level = ActionPolicyLevel.parse(create_branch_policy)
    wanted_branch = (branch or "").strip() or None
    # 没有分支名时天然无法建分支；有分支名但策略不允许时也不建。
    detach = wanted_branch is None or level is not ActionPolicyLevel.AUTO

    root = (workspace_root or "").strip() or repo.local_path
    parallel = effective_max_parallel(kind, max_parallel)
    warnings: list[str] = []

    if kind is WorkspaceProviderKind.WORKTREE:
        path = _join(root, _WORKTREE_DIRNAME, f"task-{task_id}")
        if detach:
            git_args: tuple[str, ...] = (
                "git", "-C", root, "worktree", "add", "--detach", path,
            )
        else:
            git_args = (
                "git", "-C", root, "worktree", "add", "-b", wanted_branch or "", path,
            )
    elif kind is WorkspaceProviderKind.FRESH_CLONE:
        if not root.strip():
            # 不给父目录就会退化成 cwd 相对路径，克隆产物落在哪全看进程
            # 启动位置，崩溃后无法回收 —— 宁可显式报错。
            raise InvalidValue(
                "fresh_clone 需要 workspace_root（或 RepoSpec.local_path）作为克隆父目录"
            )
        path = _join(root, _CLONE_DIRNAME, f"task-{task_id}")
        if detach:
            git_args = ("git", "clone", repo.url, path)
        else:
            git_args = ("git", "clone", "--branch", wanted_branch or "", repo.url, path)
    else:  # existing_checkout
        path = repo.local_path
        git_args = ()
        if wanted_branch:
            warnings.append(
                f"existing_checkout 不自动切换到分支 {wanted_branch!r}"
                "（会污染用户当前工作区）"
            )

    if wanted_branch and detach:
        warnings.append(
            f"create_branch 策略为 {level.label}，未创建分支 {wanted_branch!r}"
            f"（provider={kind} 以 detach 方式准备）"
        )
    if max_parallel > parallel:
        warnings.append(f"max_parallel 由 {max_parallel} 收敛为 {parallel}")

    return WorkspaceHandle(
        provider=kind,
        task_id=int(task_id),
        path=path,
        branch=wanted_branch,
        detach=detach,
        git_args=git_args,
        max_parallel=parallel,
        warnings=tuple(warnings),
    )


def cleanup_workspace(handle: WorkspaceHandle) -> CleanupPlan:
    """构造回收计划（**不执行**）。

    - ``worktree`` → ``git worktree remove --force <path>``；
    - ``fresh_clone`` → 无 git 命令，直接删目录（克隆产物没有登记在
      主仓库的 worktree 元数据里，rm 即可）；
    - ``existing_checkout`` → 什么都不做（那是用户的既有工作目录，删除属于
      不可逆破坏，未提交改动也一并消失）。
    """
    if handle.provider is WorkspaceProviderKind.WORKTREE:
        return CleanupPlan(
            argv=("git", "worktree", "remove", "--force", handle.path),
            remove_path="",  # worktree remove 已连带清理目录，不要额外 rm
        )
    if handle.provider is WorkspaceProviderKind.FRESH_CLONE:
        return CleanupPlan(argv=(), remove_path=handle.path)
    return CleanupPlan(argv=(), remove_path="")
