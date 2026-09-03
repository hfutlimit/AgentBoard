"""Phase 0 regression tests (2026-08-26 review 三件套 + small bug 收口)。

覆盖：
1. ``worker.mark_failed`` 委派给 ``clarify.mark_failed``（不是 ``_mark_failed`` —— 之前
   拼错下划线，任何旧 caller 调 ``worker.mark_failed`` 立刻 ``AttributeError``）。
2. ``StoryHandler.build_task_context`` / ``ReviewHandler.build_context`` 对未知
   ``work_type`` 抛 ``ValueError``，不再 fail-open 静默回退到 ``IMPLEMENTATION`` /
   ``IMPLEMENTATION_REVIEW``（与 ``WorkType.canonical_for`` fail-closed 原则一致）。
"""
from __future__ import annotations

import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


# =============== 1. mark_failed 委派正确 ===============

def test_worker_mark_failed_dispatches_to_clarify_mark_failed():
    """``ProposalProcessor.mark_failed`` 委派给 ``clarify.mark_failed``（无下划线）。"""
    from agentboard.processors import worker as worker_mod

    class _FakeWorker:
        def __init__(self):
            self.invoker = object()
        def __getattr__(self, name):
            raise AttributeError(name)

    # 模拟 self._handlers["clarify"].mark_failed(pid, err) 被调用
    captured = {}

    class _FakeClarify:
        def mark_failed(self, pid, err):
            captured["pid"] = pid
            captured["err"] = err
            return "failed"

    w = _FakeWorker()
    w._handlers = {"clarify": _FakeClarify()}
    # 调 ProposalProcessor.mark_failed 的实际方法体（line 248-249）
    out = worker_mod.ProposalProcessor.mark_failed(w, 42, "boom")
    assert out == "failed"
    assert captured == {"pid": 42, "err": "boom"}


def test_worker_mark_failed_does_not_call_underscore_version():
    """验证没有下划线的旧方法名 _mark_failed —— 它不存在所以应 AttributeError。"""
    from agentboard.processors import handlers

    clarify = handlers.ClarifyHandler
    # 旧错方法 _mark_failed 应该不存在
    assert not hasattr(clarify, "_mark_failed"), \
        "ClarifyHandler 不应该有 _mark_failed（错拼），mark_failed 才是正确方法名"
    # 新方法存在
    assert hasattr(clarify, "mark_failed"), \
        "ClarifyHandler 应该有 mark_failed"


# =============== 2. StoryHandler.build_task_context fail-closed ===============

def test_story_build_task_context_invalid_work_type_raises():
    """``StoryHandler.build_task_context`` 对未知 work_type 抛 ValueError，不再静默
    回退到 IMPLEMENTATION。"""
    from agentboard.processors.handlers.story import StoryHandler
    from agentboard.processors.contract import WorkType

    handler = StoryHandler.__new__(StoryHandler)  # 绕过 __init__（不需要 client）
    # _command attribute 由 build_task_context 设置，先确认起始无
    with pytest.raises(ValueError):
        handler.build_task_context(
            {"id": 1, "title": "T"}, work_type="totally_invalid_work_type_xyz",
        )


# =============== 3. ReviewHandler.build_context fail-closed ===============

def test_review_load_context_invalid_work_type_raises():
    """``ReviewHandler.load_context`` 对未知 work_type 抛 ValueError，不再静默
    回退到 IMPLEMENTATION_REVIEW。绕开 pydantic 用 dict 路径（DB 来的脏数据
    进入 dispatch 时会走这条路径，pydantic 不会先校验）。"""
    from agentboard.processors.handlers.review import ReviewHandler
    from agentboard.processors.contract import WorkType

    handler = ReviewHandler.__new__(ReviewHandler)

    class _FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"task": {"id": 1}, "epic": {}, "project": {}}

    handler._request = lambda *a, **k: _FakeResp()

    bad_work_item = {
        "task_id": 1, "event": "task.review_requested",
        "work_type": "not_a_real_type_xyz",
    }
    with pytest.raises((ValueError, KeyError)):
        handler.load_context(bad_work_item)


# =============== 4. 已知合法 work_type 仍然能跑通（回归保护） ===============

def test_story_build_task_context_valid_work_type_passes():
    """合法 work_type（"implementation" / "design" / "qa"）不应抛错。"""
    from agentboard.processors.handlers.story import StoryHandler
    from agentboard.processors.contract import WorkType

    handler = StoryHandler.__new__(StoryHandler)
    handler._get_json = lambda *a, **k: {"id": 1, "project_id": 99, "title": "T"}
    handler._request = lambda *a, **k: types.SimpleNamespace(status_code=200, text="ok")
    for wt in ("design", "implementation", "qa"):
        ctx = handler.build_task_context(
            {"id": 1, "title": "T", "project_id": 99}, work_type=wt,
        )
        # _command 被注入
        assert "_command" in ctx
        assert isinstance(ctx["_command"].work_type, WorkType)


# =============== 5. WorkType 枚举本身 fail-closed 行为 ===============

def test_worktype_unknown_value_raises():
    """``WorkType("garbage")`` 必须抛 ValueError，不允许静默回退。"""
    from agentboard.processors.contract import WorkType

    with pytest.raises(ValueError):
        WorkType("garbage_value_xyz")


import pytest
