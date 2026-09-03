"""Implementation Plan M6（T6.4 / T6.5）· Action Policy + Workspace/Runtime provider 回归测试。

背景
----
M6 把"Agent 能做什么"从 prompt 约定升级为**服务端强制**的四态策略
（``DENY < MANUAL < APPROVAL < AUTO``，``effective = min(team, project, task,
user_pref)``），并给 Agent 执行提供受策略约束的 workspace / runtime provider。

本文件锁定五条不变式：

1. 四态序数与 ``min()`` 合并：某层未表态（None / 未配置该动作）不参与比较；
2. **DENY 不可被下层放宽**（核心）：上层 DENY + 下层 AUTO 仍为 DENY；
3. APPROVAL / MANUAL 需要人工介入，AUTO 才允许自动执行；
4. 默认策略"安全但可用"：不是全 MANUAL（否则自动化流程一步都跑不动）；
5. workspace 侧：``existing_checkout`` 强制 ``max_parallel=1``，
   ``create_branch != AUTO`` 时 worktree 必须 ``--detach``（不偷建业务分支）。

非法输入（未知 action / 非法等级 / 未知 provider）统一抛 ``InvalidValue``。

本模块是纯计算逻辑（策略合并 + 参数构造），**不依赖 DB**，因此不需要
``test_m0_review_truth_source.py`` 那套临时 sqlite 引导。

运行：
    python -m pytest tests/test_m6_policy_and_providers.py -q
"""
from __future__ import annotations

import logging
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "src", "backend-fastapi")
for _p in (_ROOT, _BACKEND):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agentboard.processors.workspace import (  # noqa: E402
    CleanupPlan,
    DockerRuntimeProvider,
    HostRuntimeProvider,
    RepoSpec,
    RuntimeProviderKind,
    WorkspaceProviderKind,
    cleanup_workspace,
    effective_max_parallel,
    get_runtime_provider,
    prepare_workspace,
)
from agentboard.core.exceptions import InvalidValue  # noqa: E402
from agentboard.features.policy import (  # noqa: E402
    ActionPolicy,
    ActionPolicyLevel,
    GitAction,
    PolicyDecision,
    decide,
    effective_policy,
    is_allowed,
    requires_approval,
)
from agentboard.features.policy.schemas import ActionPolicyOverrides  # noqa: E402


# ---------------------------------------------------------------- 四态序数

def test_level_ordering_is_deny_lt_manual_lt_approval_lt_auto():
    assert (ActionPolicyLevel.DENY < ActionPolicyLevel.MANUAL
            < ActionPolicyLevel.APPROVAL < ActionPolicyLevel.AUTO)


def test_level_parse_accepts_case_insensitive_and_int():
    assert ActionPolicyLevel.parse("AUTO") is ActionPolicyLevel.AUTO
    assert ActionPolicyLevel.parse(" manual ") is ActionPolicyLevel.MANUAL
    assert ActionPolicyLevel.parse(ActionPolicyLevel.DENY) is ActionPolicyLevel.DENY
    assert ActionPolicyLevel.parse(3) is ActionPolicyLevel.AUTO


# ------------------------------------------------- effective = min(层级)

def test_effective_takes_strictest_across_layers():
    policy = effective_policy(
        team={"push": "approval"},
        project={"push": "auto"},
        task={"push": "deny"},
        user_pref={"push": "auto"},
    )
    assert policy.level_for("push") is ActionPolicyLevel.DENY


def test_layers_without_opinion_are_ignored():
    """某层为 None / dict 里没配该动作 = 不表态，不参与 min。"""
    policy = effective_policy(team=None, project={"commit": "manual"}, task=None)
    # commit 只有 project 表态 → 取 manual
    assert policy.level_for("commit") is ActionPolicyLevel.MANUAL
    # 其他动作无人表态 → 回落默认值，而不是被某层的 None 拉低
    assert policy.level_for("push") is ActionPolicyLevel.APPROVAL


def test_effective_accepts_actionpolicy_and_json_string():
    policy = effective_policy(
        project=ActionPolicy.from_dict({"merge": "approval"}),
        task='{"merge": "manual"}',  # DB 里以 JSON 文本存列
    )
    assert policy.level_for("merge") is ActionPolicyLevel.MANUAL


def test_deny_cannot_be_relaxed_by_lower_layers():
    """核心不变式：上层 DENY，下层（task / user_pref）声明 AUTO 也无效。"""
    policy = effective_policy(
        team={"force_push": "deny", "merge": "deny"},
        project={"force_push": "auto", "merge": "auto"},
        task={"force_push": "auto", "merge": "auto"},
        user_pref={"force_push": "auto", "merge": "auto"},
    )
    assert policy.level_for("force_push") is ActionPolicyLevel.DENY
    assert policy.level_for("merge") is ActionPolicyLevel.DENY
    assert decide(policy, "merge") is PolicyDecision.DENY
    assert is_allowed(policy, "force_push") is False


def test_deny_in_middle_layer_blocks_permissive_task_layer():
    policy = effective_policy(project={"push": "deny"}, task={"push": "auto"})
    assert policy.level_for("push") is ActionPolicyLevel.DENY


# ------------------------------------------------------------ 裁决语义

def test_approval_and_manual_require_human():
    policy = ActionPolicy.from_dict(
        {"push": "approval", "merge": "manual", "commit": "auto"})
    assert requires_approval(policy, "push") is True
    assert requires_approval(policy, "merge") is True
    assert requires_approval(policy, "commit") is False

    assert decide(policy, "push") is PolicyDecision.REQUIRE_APPROVAL
    assert decide(policy, "merge") is PolicyDecision.REQUIRE_MANUAL
    assert decide(policy, "commit") is PolicyDecision.ALLOW

    # MANUAL / APPROVAL 都"有被执行的机会"（走人工/审批），只有 DENY 没有
    assert is_allowed(policy, "merge") is True
    assert is_allowed(policy, "commit") is True


# ------------------------------------------------------------ 默认策略

def test_default_policy_is_safe_but_usable():
    policy = effective_policy()  # 四层全空
    assert policy.level_for("create_branch") is ActionPolicyLevel.AUTO
    assert policy.level_for("commit") is ActionPolicyLevel.AUTO
    assert policy.level_for("push") is ActionPolicyLevel.APPROVAL
    assert policy.level_for("create_pr") is ActionPolicyLevel.APPROVAL
    assert policy.level_for("merge") is ActionPolicyLevel.DENY
    assert policy.level_for("force_push") is ActionPolicyLevel.DENY

    # 评审结论：默认不能全 MANUAL，否则自动化流程完全跑不动
    levels = policy.to_dict().values()
    assert ActionPolicyLevel.MANUAL.label not in levels
    assert decide(policy, "commit") is PolicyDecision.ALLOW


def test_default_policy_covers_every_action():
    assert set(effective_policy().to_dict()) == {str(a) for a in GitAction}


# ------------------------------------------------------------ 输入校验

def test_unknown_action_raises_invalid_value():
    policy = effective_policy()
    with pytest.raises(InvalidValue):
        decide(policy, "rebase_onto_main")
    with pytest.raises(InvalidValue):
        ActionPolicy.from_dict({"force-push": "deny"})  # 拼错也不放过


def test_invalid_level_raises_invalid_value():
    with pytest.raises(InvalidValue):
        ActionPolicy.from_dict({"push": "maybe"})
    with pytest.raises(InvalidValue):
        ActionPolicy.from_dict({"push": 9})
    with pytest.raises(InvalidValue):
        effective_policy(team={"push": "auto"}, project='{"push": ')


def test_schema_overrides_drops_unset_fields():
    """局部覆盖语义：None 字段不表态，不会把上层收紧的策略写死。"""
    overrides = ActionPolicyOverrides(push="deny")
    assert overrides.to_levels() == {"push": ActionPolicyLevel.DENY}
    policy = effective_policy(team=overrides.to_levels(), task={"push": "auto"})
    assert policy.level_for("push") is ActionPolicyLevel.DENY
    # from_policy 全量物化，响应体里每个动作都有值
    assert set(ActionPolicyOverrides.from_policy(policy).to_levels()) == {
        str(a) for a in GitAction}


# ------------------------------------------------- workspace: max_parallel

def test_existing_checkout_forces_max_parallel_one(caplog):
    handle = prepare_workspace(
        RepoSpec(local_path="/srv/repo"),
        provider=WorkspaceProviderKind.EXISTING_CHECKOUT,
        task_id=7,
        max_parallel=4,
    )
    assert handle.max_parallel == 1
    assert handle.path == "/srv/repo"
    # 并行度被降级必须有 warning 日志，否则调用方以为配生效了
    warnings = [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING]
    assert any("max_parallel" in m and "existing_checkout" in m for m in warnings)


def test_worktree_and_clone_keep_requested_parallelism():
    for provider in (WorkspaceProviderKind.WORKTREE, WorkspaceProviderKind.FRESH_CLONE):
        repo = (RepoSpec(url="git@example.com:acme/app.git", local_path="/srv/repo")
                if provider is WorkspaceProviderKind.FRESH_CLONE
                else RepoSpec(local_path="/srv/repo"))
        handle = prepare_workspace(repo, provider=provider, task_id=8, max_parallel=4)
        assert handle.max_parallel == 4


def test_effective_max_parallel_helper():
    assert effective_max_parallel(WorkspaceProviderKind.EXISTING_CHECKOUT, 8) == 1
    assert effective_max_parallel(WorkspaceProviderKind.WORKTREE, 8) == 8
    assert effective_max_parallel(WorkspaceProviderKind.WORKTREE, 0) == 1


# --------------------------------------------- workspace: create_branch 策略

def test_worktree_detaches_when_create_branch_is_manual():
    handle = prepare_workspace(
        RepoSpec(local_path="/srv/repo"),
        provider=WorkspaceProviderKind.WORKTREE,
        task_id=42,
        branch="feature/login",
        create_branch_policy=ActionPolicyLevel.MANUAL,
    )
    assert handle.detach is True
    assert handle.creates_branch is False
    assert "--detach" in handle.git_args
    assert "-b" not in handle.git_args


def test_worktree_detaches_for_approval_and_deny_too():
    """审批是执行期的事，prepare 阶段绝不预支 → 非 AUTO 一律 detach。"""
    for level in (ActionPolicyLevel.DENY, ActionPolicyLevel.APPROVAL):
        handle = prepare_workspace(
            RepoSpec(local_path="/srv/repo"),
            provider=WorkspaceProviderKind.WORKTREE,
            task_id=42,
            branch="feature/login",
            create_branch_policy=level,
        )
        assert handle.detach is True
        assert "--detach" in handle.git_args


def test_worktree_creates_branch_only_when_auto():
    handle = prepare_workspace(
        RepoSpec(local_path="/srv/repo"),
        provider=WorkspaceProviderKind.WORKTREE,
        task_id=42,
        branch="feature/login",
        create_branch_policy=ActionPolicyLevel.AUTO,
    )
    assert handle.detach is False
    assert handle.creates_branch is True
    assert handle.git_args == (
        "git", "-C", "/srv/repo", "worktree", "add",
        "-b", "feature/login", "/srv/repo/.worktrees/task-42",
    )


def test_fresh_clone_skips_branch_when_not_auto():
    repo = RepoSpec(url="git@example.com:acme/app.git", local_path="/srv/cache")
    handle = prepare_workspace(repo, provider=WorkspaceProviderKind.FRESH_CLONE,
                               task_id=9, branch="feature/login",
                               create_branch_policy="manual")
    assert "--branch" not in handle.git_args
    assert handle.git_args == ("git", "clone", repo.url, "/srv/cache/.clones/task-9")

    auto = prepare_workspace(repo, provider=WorkspaceProviderKind.FRESH_CLONE,
                             task_id=9, branch="feature/login",
                             create_branch_policy="auto")
    assert "--branch" in auto.git_args


# ------------------------------------------------------------- 校验与回收

def test_invalid_provider_and_missing_repo_fields_raise():
    with pytest.raises(InvalidValue):
        prepare_workspace(RepoSpec(local_path="/srv/repo"), provider="nfs", task_id=1)
    with pytest.raises(InvalidValue):
        prepare_workspace(RepoSpec(url="git@example.com:acme/app.git"),
                          provider=WorkspaceProviderKind.WORKTREE, task_id=1)
    with pytest.raises(InvalidValue):
        prepare_workspace(RepoSpec(url="git@example.com:acme/app.git"),
                          provider=WorkspaceProviderKind.FRESH_CLONE, task_id=1)


def test_cleanup_never_deletes_existing_checkout():
    handle = prepare_workspace(RepoSpec(local_path="/srv/repo"),
                               provider=WorkspaceProviderKind.EXISTING_CHECKOUT,
                               task_id=7)
    assert cleanup_workspace(handle) == CleanupPlan(argv=(), remove_path="")


def test_cleanup_plans():
    worktree = prepare_workspace(RepoSpec(local_path="/srv/repo"),
                                 provider=WorkspaceProviderKind.WORKTREE, task_id=7)
    assert cleanup_workspace(worktree).argv == (
        "git", "worktree", "remove", "--force", worktree.path)

    clone = prepare_workspace(
        RepoSpec(url="git@example.com:acme/app.git", local_path="/srv/cache"),
        provider=WorkspaceProviderKind.FRESH_CLONE, task_id=7)
    assert cleanup_workspace(clone) == CleanupPlan(argv=(), remove_path=clone.path)


# --------------------------------------------------------- runtime provider

def test_host_runtime_passes_argv_through():
    plan = get_runtime_provider(RuntimeProviderKind.HOST).build(
        ("git", "push"), cwd="/srv/repo")
    assert plan.runtime is RuntimeProviderKind.HOST
    assert plan.argv == ("git", "push")
    assert plan.cwd == "/srv/repo"


def test_docker_runtime_mounts_workspace_and_forwards_env():
    plan = get_runtime_provider("docker", image="acme/agent:1").build(
        ("git", "push"), cwd="/srv/work", env={"TOKEN": "x"}, network=False)
    assert plan.argv[:3] == ("docker", "run", "--rm")
    assert "-v" in plan.argv and "/srv/work:/workspace" in plan.argv
    assert "--network" in plan.argv and "none" in plan.argv
    assert plan.argv[-2:] == ("git", "push")
    assert "acme/agent:1" in plan.argv
    assert "TOKEN=x" in plan.argv
    assert plan.image == "acme/agent:1"


def test_runtime_providers_are_swappable_and_validate_input():
    assert isinstance(get_runtime_provider("host"), HostRuntimeProvider)
    assert isinstance(get_runtime_provider("docker"), DockerRuntimeProvider)
    with pytest.raises(InvalidValue):
        get_runtime_provider("k8s")
    with pytest.raises(InvalidValue):
        DockerRuntimeProvider(image="").build(("git", "status"), cwd="/srv/repo")
    with pytest.raises(InvalidValue):
        HostRuntimeProvider().build((), cwd="/srv/repo")
