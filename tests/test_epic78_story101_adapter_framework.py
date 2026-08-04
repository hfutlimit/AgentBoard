"""
Epic 78 Story 101 — 执行器适配器框架（AgentAdapter + 注册表）单元测试

覆盖验收标准：
1. 抽象类不可直接实例化；子类实现抽象方法后可实例化。
2. 注册表可导入；register_adapter + @adapter 装饰器两种方式注册均可取回。
3. 未注册名字 get_adapter 抛 AdapterNotFound；default 参数兜底生效。
4. FakeAdapter 全生命周期：launch 返回 RunHandle → poll RUNNING → complete → SUCCESS。
5. 新增 Agent 类型只需写 Adapter 并注册，不需改动 Executor 主干。
6. 兜底 NotConfiguredAdapter：launch 抛可读错误、poll 恒 FAILED。
"""

from __future__ import annotations

import pytest

from agentboard.executor import (
    ADAPTERS,
    AgentAdapter,
    AgentRunContext,
    LauncherAdapter,
    NotConfiguredAdapter,
    RunHandle,
    TriggerAdapter,
    AdapterAlreadyRegistered,
    AdapterError,
    AdapterNotFound,
    adapter,
    get_adapter,
    has_adapter,
    register_adapter,
    registered_adapters,
    resolve_adapter,
)
from agentboard.domains.common.enums import RunStatus


@pytest.fixture(autouse=True)
def _isolate_registry():
    """每个测试隔离全局注册表：保存→清空→执行→恢复。

    Story 102 起模块顶层会注册 codex/claude（CodexLauncher/ClaudeLauncher），
    因此执行期间必须先清空，避免与顶层条目冲突（如 name="codex" 的测试类）；
    结束后恢复快照（含顶层注册），保证模块级注册不丢失。
    """
    snapshot = dict(ADAPTERS)
    ADAPTERS.clear()
    yield
    ADAPTERS.clear()
    ADAPTERS.update(snapshot)


# ---------------------------------------------------------------------------
# 抽象类约束
# ---------------------------------------------------------------------------
def test_agent_adapter_is_abstract():
    with pytest.raises(TypeError):
        AgentAdapter()  # type: ignore[abstract]


def test_launcher_adapter_is_abstract():
    # LauncherAdapter 只实现了 poll_status，launch 仍是抽象的
    with pytest.raises(TypeError):
        LauncherAdapter()  # type: ignore[abstract]


def test_trigger_adapter_is_abstract():
    with pytest.raises(TypeError):
        TriggerAdapter()  # type: ignore[abstract]


class _ConcreteLauncher(LauncherAdapter):
    name = "fake-launcher"

    def launch(self, run, task, ctx):
        return RunHandle(run_id=ctx.run_id, adapter=self.name).mark_running()


def test_concrete_subclass_instantiable():
    inst = _ConcreteLauncher()
    assert inst.name == "fake-launcher"


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
def test_register_and_get():
    register_adapter(_ConcreteLauncher)  # name 取 cls.name
    assert get_adapter("fake-launcher") is _ConcreteLauncher
    assert has_adapter("fake-launcher")
    assert "fake-launcher" in registered_adapters()


def test_register_with_explicit_name():
    register_adapter(_ConcreteLauncher, name="codex")
    assert get_adapter("codex") is _ConcreteLauncher
    # 显式 name 会回写 cls.name
    assert _ConcreteLauncher.name == "codex"


def test_decorator_register():
    @adapter("claude")
    class ClaudeL(LauncherAdapter):
        def launch(self, run, task, ctx):
            return RunHandle(run_id=ctx.run_id, adapter=self.name).mark_running()

    assert get_adapter("claude") is ClaudeL
    assert ClaudeL.name == "claude"


def test_register_as_decorator_without_name():
    @register_adapter
    class WorkbuddyL(LauncherAdapter):
        def launch(self, run, task, ctx):
            return RunHandle(run_id=ctx.run_id, adapter=self.name).mark_running()

    assert get_adapter("workbuddyl") is WorkbuddyL  # 类名小写


def test_duplicate_register_raises():
    register_adapter(_ConcreteLauncher, name="dup")
    with pytest.raises(AdapterAlreadyRegistered):
        register_adapter(TriggerAdapter, name="dup")  # 不同类


def test_duplicate_register_same_class_idempotent():
    register_adapter(_ConcreteLauncher, name="same")
    register_adapter(_ConcreteLauncher, name="same")  # 同对象重复注册不抛


def test_duplicate_register_replace():
    register_adapter(_ConcreteLauncher, name="dup")
    register_adapter(TriggerAdapter, name="dup", replace=True)
    assert get_adapter("dup") is TriggerAdapter


def test_get_unknown_raises():
    with pytest.raises(AdapterNotFound):
        get_adapter("nope")


def test_get_unknown_with_default():
    assert get_adapter("nope", default=NotConfiguredAdapter) is NotConfiguredAdapter


def test_resolve_unknown_returns_not_configured():
    assert resolve_adapter("nope") is NotConfiguredAdapter


# ---------------------------------------------------------------------------
# RunHandle 生命周期
# ---------------------------------------------------------------------------
def test_run_handle_lifecycle():
    h = RunHandle(run_id=1, adapter="fake")
    assert h.status == RunStatus.PENDING
    h.mark_running()
    assert h.status == RunStatus.RUNNING
    assert h.started_at is not None
    h.complete("done!")
    assert h.status == RunStatus.SUCCESS
    assert h.result == "done!"
    assert h.finished_at is not None


def test_run_handle_fail():
    h = RunHandle(run_id=1, adapter="fake").mark_running()
    h.fail("boom")
    assert h.status == RunStatus.FAILED
    assert h.error == "boom"


# ---------------------------------------------------------------------------
# FakeAdapter 全生命周期
# ---------------------------------------------------------------------------
class _FakeAgent(AgentAdapter):
    name = "fake"

    def launch(self, run, task, ctx):
        handle = RunHandle(run_id=ctx.run_id, adapter=self.name).mark_running()
        self._handle = handle
        return handle

    def poll_status(self, handle):
        return handle.status


def test_fake_adapter_full_lifecycle():
    register_adapter(_FakeAgent)
    cls = get_adapter("fake")
    inst = cls()
    ctx = AgentRunContext(project_id=1, schedule_id=2, run_id=7, task_id=3)
    handle = inst.launch(run=None, task=None, ctx=ctx)
    assert handle.run_id == 7
    assert inst.poll_status(handle) == RunStatus.RUNNING
    handle.complete("ok")
    assert inst.poll_status(handle) == RunStatus.SUCCESS


# ---------------------------------------------------------------------------
# 扩展点：新增 Agent 类型只需注册，不改主干
# ---------------------------------------------------------------------------
def test_new_agent_registered_without_touching_module():
    before = set(registered_adapters())

    @adapter("qoder")
    class QoderTrigger(TriggerAdapter):
        def launch(self, run, task, ctx):
            return RunHandle(run_id=ctx.run_id, adapter=self.name).mark_running()

    after = set(registered_adapters())
    assert after - before == {"qoder"}
    assert get_adapter("qoder") is QoderTrigger
    # 模块内不需要任何改动即可扩展 —— 注册后立即可用
    assert resolve_adapter("qoder") is QoderTrigger


# ---------------------------------------------------------------------------
# 兜底适配器
# ---------------------------------------------------------------------------
def test_not_configured_launch_raises_readable_error():
    inst = NotConfiguredAdapter()
    ctx = AgentRunContext(project_id=1, schedule_id=2, run_id=9, agent="ghost")
    with pytest.raises(AdapterError, match="ghost"):
        inst.launch(run=None, task=None, ctx=ctx)


def test_not_configured_poll_failed():
    inst = NotConfiguredAdapter()
    handle = RunHandle(run_id=1, adapter="__not_configured__")
    assert inst.poll_status(handle) == RunStatus.FAILED


# ---------------------------------------------------------------------------
# 上下文与 prompt
# ---------------------------------------------------------------------------
def test_agent_run_context_as_dict():
    ctx = AgentRunContext(
        project_id=1, schedule_id=2, run_id=3, task_id=4, agent="codex",
        task_title="写个登录", task_spec="- [ ] a\n- [ ] b", memory="团队约定：中文注释",
    )
    d = ctx.as_dict()
    assert d["run_id"] == 3 and d["agent"] == "codex"
    assert d["task_spec"] == "- [ ] a\n- [ ] b"


def test_build_prompt_default():
    inst = _FakeAgent()
    ctx = AgentRunContext(
        project_id=1, schedule_id=2, run_id=5,
        task_title="T", task_spec="S", memory="M",
    )
    prompt = inst.build_prompt(run=None, task=None, ctx=ctx)
    assert "run #5" in prompt
    assert "T" in prompt and "S" in prompt and "M" in prompt


def test_launcher_poll_process():
    import subprocess
    import sys

    class _ProcLauncher(LauncherAdapter):
        name = "proc-launcher"

        def launch(self, run, task, ctx):
            handle = RunHandle(
                run_id=ctx.run_id, adapter=self.name,
                process=subprocess.Popen(
                    [sys.executable, "-c", "import sys; sys.exit(0)"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                ),
            ).mark_running()
            return handle

    register_adapter(_ProcLauncher)
    inst = _ProcLauncher()
    ctx = AgentRunContext(project_id=1, schedule_id=2, run_id=6)
    handle = inst.launch(run=None, task=None, ctx=ctx)
    handle.process.wait(timeout=30)
    assert inst.poll_status(handle) == RunStatus.SUCCESS


def test_launcher_poll_nonzero_exit_failed():
    import subprocess
    import sys

    class _FailingLauncher(LauncherAdapter):
        name = "fail-launcher"

        def launch(self, run, task, ctx):
            handle = RunHandle(
                run_id=ctx.run_id, adapter=self.name,
                process=subprocess.Popen(
                    [sys.executable, "-c", "import sys; sys.exit(3)"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                ),
            ).mark_running()
            return handle

    register_adapter(_FailingLauncher)
    inst = _FailingLauncher()
    ctx = AgentRunContext(project_id=1, schedule_id=2, run_id=8)
    handle = inst.launch(run=None, task=None, ctx=ctx)
    handle.process.wait(timeout=30)
    status = inst.poll_status(handle)
    assert status == RunStatus.FAILED
    assert "3" in (handle.error or "")
