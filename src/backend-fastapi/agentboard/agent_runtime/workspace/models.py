"""Workspace / Runtime provider 的领域模型（M6 T6.5）。

放置位置说明
------------
本包放在 ``agent_runtime/workspace`` 而不是 ``features/workspace``：workspace
是**执行期基础设施**（无 DB 实体、无 HTTP 路由），与同级的
``agent_runtime/behavior`` 同性质；``features/*`` 是"实体 + router + service"
的领域切片，放进 features 会凭空多出一个没有实体的空切片。

本层不执行任何 git / docker 命令
--------------------------------
只做**策略判定 + 句柄/参数构造**：``prepare_workspace`` 返回带 ``git_args``
的句柄，``cleanup_workspace`` 返回回收计划，真正执行由既有 worker 逻辑
（``agent_runtime/invokers.py`` 的 subprocess 路径）负责。这样本包可单测、
可逐步接入，不会引入"改了一半的副作用"。

核心不变式
----------
1. ``existing_checkout`` 复用用户既有工作目录，并行任务必然互相污染，
   因此 ``max_parallel`` 被强制收敛为 1（见
   :data:`FORCED_MAX_PARALLEL`）；
2. ``create_branch`` 策略不是 ``AUTO`` 时，workspace 一律**不建/不切业务
   分支**（worktree 走 ``--detach``），否则等于绕过策略"顺手"建了分支。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ...core.exceptions import InvalidValue


class WorkspaceProviderKind(StrEnum):
    """工作区供给方式（三选一）。

    ``worktree``          基于既有本地仓库建独立 worktree（隔离 + 省磁盘，推荐）；
    ``fresh_clone``       每次任务全新 clone（最干净，代价是耗时与磁盘）；
    ``existing_checkout`` 直接用用户既有工作目录（**不隔离**，只能串行）。
    """

    WORKTREE = "worktree"
    FRESH_CLONE = "fresh_clone"
    EXISTING_CHECKOUT = "existing_checkout"


class RuntimeProviderKind(StrEnum):
    """执行运行时（在哪跑命令）。

    ``host``   直接在 Worker 宿主机跑（默认，与既有 SubprocessAgentInvoker 一致）；
    ``docker`` 在容器里跑（隔离依赖与副作用，需要镜像）。
    """

    HOST = "host"
    DOCKER = "docker"


#: 各 workspace provider 的并行度硬上限。
#:
#: ``existing_checkout`` 必须串行：多个任务共用同一个工作目录，并行会互相
#: 覆盖未提交改动、切分支打架。空 provider 表示不受限（由调用方资源决定）。
FORCED_MAX_PARALLEL: dict[WorkspaceProviderKind, int] = {
    WorkspaceProviderKind.EXISTING_CHECKOUT: 1,
}


@dataclass(frozen=True)
class RepoSpec:
    """仓库来源描述。

    ``local_path``  既有本地仓库/工作目录的绝对路径；
    ``url``         远端地址（``fresh_clone`` 必需）；
    ``default_branch`` 未指定分支时的基准分支。
    """

    url: str = ""
    default_branch: str = "main"
    local_path: str = ""

    def require(self, provider: WorkspaceProviderKind) -> "RepoSpec":
        """按 provider 校验必备字段，缺失抛 :class:`InvalidValue`。

        为什么在这里校验：provider 选错字段（如 fresh_clone 没给 url）会导致
        后面拼出 ``git clone <空> <path>`` 这种诡异命令，越早失败越好定位。
        """
        if provider == WorkspaceProviderKind.FRESH_CLONE and not self.url.strip():
            raise InvalidValue(
                f"workspace provider={provider} 需要 RepoSpec.url（全新克隆的远端地址）"
            )
        if provider in (WorkspaceProviderKind.WORKTREE,
                        WorkspaceProviderKind.EXISTING_CHECKOUT):
            if not self.local_path.strip():
                raise InvalidValue(
                    f"workspace provider={provider} 需要 RepoSpec.local_path（本地仓库路径）"
                )
        return self


@dataclass(frozen=True)
class WorkspaceHandle:
    """一次工作区准备的产物（**尚未执行**，只是计划）。

    ``path``      计划中的工作目录（确定性路径，便于去重与回收）；
    ``branch``    目标业务分支（``None`` = 不指定，按 ``detach`` 处理）；
    ``detach``    ``True`` 表示**不建 / 不切业务分支**：worktree 用
                  ``--detach``，fresh_clone 不带 ``--branch``；
    ``git_args``  待执行的 git 命令 argv（本层不执行，交给既有 worker）；
    ``max_parallel`` 收敛后的并行度上限；
    ``warnings``  需要人类/日志注意的降级说明（不是错误）。
    """

    provider: WorkspaceProviderKind
    task_id: int
    path: str
    branch: str | None = None
    detach: bool = False
    git_args: tuple[str, ...] = ()
    max_parallel: int = 1
    warnings: tuple[str, ...] = ()

    @property
    def creates_branch(self) -> bool:
        """本次准备是否真的会创建/切到业务分支。"""
        return bool(self.branch) and not self.detach


@dataclass(frozen=True)
class CleanupPlan:
    """工作区回收计划（本层不执行）。

    ``argv``        回收前需要执行的 git 命令（如 ``worktree remove``），可为空；
    ``remove_path`` 需要删除的目录（空串 = 不要删）。

    ``existing_checkout`` 的 ``remove_path`` 恒为空：那是用户的既有工作目录，
    任务跑完删掉属于不可逆破坏。
    """

    argv: tuple[str, ...] = ()
    remove_path: str = ""


@dataclass(frozen=True)
class ExecutionPlan:
    """Runtime provider 构造出的一次执行命令（本层不执行）。

    ``cwd`` 是**宿主侧**工作目录；docker 下会被挂载到容器 ``/workspace``，
    ``argv`` 已经是完整可 ``subprocess.run`` 的形态（含 ``docker run`` 前缀）。
    """

    runtime: RuntimeProviderKind
    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str] = field(default_factory=dict)
    image: str = ""
