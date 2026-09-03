"""Tests for RoutedSubprocessInvoker (multi-agent in single worker).

Covers:
- env parsing: AGENTBOARD_WORKER_AGENT_COMMANDS / AGENTBOARD_WORKER_AGENT_ROUTING
- default-routing when routing map is empty / action not in map
- per-action routing picks the right child invoker
- prompt header injection (so children see which slot they were routed to)
- child invoker is called with the routed context
"""
from __future__ import annotations

import os
import json
import subprocess  # noqa: F401  (ensure invokers can be imported without runtime CLI)
import sys
from pathlib import Path
from unittest import mock

# Backend path bootstrap (matches conftest in this repo)
BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from agentboard.processors.invokers import (  # noqa: E402
    RoutedSubprocessInvoker, parse_agent_command_map, parse_agent_routing,
)


class _FakeChild:
    """Stand-in for SubprocessProcessorInvoker: records call, returns canned decision."""
    def __init__(self, alias: str, cmd: str):
        self.alias = alias
        self.cmd = cmd
        self.invocations: list[dict] = []

    def invoke(self, context: dict):
        self.invocations.append(context)
        from agentboard.processors.config import AgentDecision
        return AgentDecision(action="ask", questions=["q"])


class _FakeChildren(dict):
    """dict-like with the same shape RoutedSubprocessInvoker needs."""
    def __init__(self, cmds: dict[str, str]):
        super().__init__({alias: _FakeChild(alias, cmd) for alias, cmd in cmds.items()})


def _patch_env(monkeypatch, commands: dict[str, str] | None,
               routing: dict[str, str] | None) -> None:
    monkeypatch.delenv("AGENTBOARD_WORKER_AGENT_COMMANDS", raising=False)
    monkeypatch.delenv("AGENTBOARD_WORKER_AGENT_ROUTING", raising=False)
    if commands is not None:
        monkeypatch.setenv("AGENTBOARD_WORKER_AGENT_COMMANDS", json.dumps(commands))
    if routing is not None:
        monkeypatch.setenv("AGENTBOARD_WORKER_AGENT_ROUTING", json.dumps(routing))


def test_parse_commands_rejects_non_object(monkeypatch):
    _patch_env(monkeypatch, ["not", "a", "dict"], None)
    assert parse_agent_command_map() == {}


def test_parse_routing_rejects_non_object(monkeypatch):
    _patch_env(monkeypatch, {"a": "b"}, "not-a-dict")
    assert parse_agent_routing() == {}


def test_routes_by_action(monkeypatch):
    cmds = {
        "minimax": '"python" "inv_min.py"',
        "codebuddy": '"node" "inv_cb.cmd"',
    }
    routing = {
        "clarify": "minimax",
        "create_ticket": "minimax",
        "process_story": "codebuddy",
        "review": "codebuddy",
        "owner_response": "codebuddy",
    }
    _patch_env(monkeypatch, cmds, routing)

    invoker = RoutedSubprocessInvoker()
    # 替换子 invoker，避免真起进程
    invoker._children = _FakeChildren(cmds)

    invoker.invoke({"action": "clarify", "payload": "x"})

    assert invoker.last_routed == "minimax"
    assert "inv_min.py" in invoker.last_invoker.cmd


def test_unknown_action_falls_back_to_first(monkeypatch):
    cmds = {
        "minimax": '"python" "a.py"',
        "codebuddy": '"python" "b.py"',
    }
    _patch_env(monkeypatch, cmds, {})  # no routing → first wins
    invoker = RoutedSubprocessInvoker()
    invoker._children = _FakeChildren(cmds)

    invoker.invoke({"action": "unknown_action"})
    assert invoker.last_routed == "minimax"


def test_empty_commands_raises(monkeypatch):
    _patch_env(monkeypatch, None, None)
    try:
        RoutedSubprocessInvoker()
    except ValueError as e:
        assert "AGENTBOARD_WORKER_AGENT_COMMANDS" in str(e)
    else:
        raise AssertionError("expected ValueError when no commands configured")


def test_prompt_header_includes_route(monkeypatch):
    cmds = {"codebuddy": '"python" "x.py"'}
    _patch_env(monkeypatch, cmds, {})
    invoker = RoutedSubprocessInvoker()

    captured = {}

    class FakeChild:
        cmd = '"python" "x.py"'
        def invoke(self, context):
            captured["ctx"] = context
            from agentboard.processors.config import AgentDecision
            return AgentDecision(action="ask", questions=["q"])

    invoker._children = {"codebuddy": FakeChild()}  # bypass real SubprocessProcessorInvoker

    invoker.invoke({"action": "clarify", "title": "demo"})
    assert captured["ctx"]["_routed_alias"] == "codebuddy"
    assert captured["ctx"]["_routed_action"] == "clarify"


def test_invoke_with_prompt_passes_exact_sentinel_to_routed_subprocess(tmp_path: Path, monkeypatch):
    """Regression test (2026-08-27 review):
    SENTINEL_RENDERED_PROMPT -> ComplianceEnforcingInvoker -> RoutedSubprocessInvoker -> SubprocessProcessorInvoker -> subprocess stdin.
    Verifies that invoke_with_prompt does not drop the prepared prompt on routed invocation.
    """
    from agentboard.processors.compliance import MANDATORY_PREFLIGHT_MARKER
    from agentboard.processors.invokers import ComplianceEnforcingInvoker

    # Create dummy project directory with a real file so compliance validation passes
    proj_dir = tmp_path / "project"
    proj_dir.mkdir()
    (proj_dir / "src").mkdir()
    (proj_dir / "src" / "sample.py").write_text("# code", encoding="utf-8")

    mapping = tmp_path / "project-mappings.json"
    mapping.write_text(
        json.dumps({"projects": {"42": {"local_dir": str(proj_dir)}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTBOARD_LOCAL_MAPPINGS", str(mapping))

    cmds = {
        "minimax": '"fake-agent" "minimax"',
        "codebuddy": '"fake-agent" "codebuddy"',
    }
    routing = {
        "proposal_clarify": "minimax",
        "implementation": "codebuddy",
    }
    _patch_env(monkeypatch, cmds, routing)

    routed_invoker = RoutedSubprocessInvoker()
    guarded_invoker = ComplianceEnforcingInvoker(routed_invoker)

    captured_runs = []

    def fake_run(argv, **kwargs):
        captured_runs.append({"argv": argv, "kwargs": kwargs})
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps({
                "action": "ask",
                "questions": ["Real question?"],
                "inspected_files": ["src/sample.py"],
            }),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    SENTINEL = "=== SENTINEL_RENDERED_PROMPT_WITH_FULL_CONTEXT_42 ==="
    context = {
        "work_type": "proposal_clarify",
        "project_id": 42,
        "project_dir": str(proj_dir),
    }

    decision = guarded_invoker.invoke_with_prompt(SENTINEL, context)

    assert decision.action == "ask"
    assert routed_invoker.last_routed == "minimax"
    assert len(captured_runs) == 1
    stdin_input = captured_runs[0]["kwargs"].get("input", "")
    assert SENTINEL in stdin_input
    assert MANDATORY_PREFLIGHT_MARKER in stdin_input

    # Test route to second child
    SENTINEL_2 = "=== SENTINEL_CODEBUDDY_PROMPT_99 ==="
    context_2 = {
        "work_type": "implementation",
        "project_id": 42,
        "project_dir": str(proj_dir),
    }
    decision_2 = guarded_invoker.invoke_with_prompt(SENTINEL_2, context_2)
    assert decision_2.action == "ask"
    assert routed_invoker.last_routed == "codebuddy"
    assert len(captured_runs) == 2
    stdin_input_2 = captured_runs[1]["kwargs"].get("input", "")
    assert SENTINEL_2 in stdin_input_2


def test_routed_invoker_with_instance_prompt_builder(tmp_path: Path, monkeypatch):
    """Verify instance-level prompt_builder propagates to child invokers."""
    cmds = {"minimax": '"fake-agent" "minimax"'}
    _patch_env(monkeypatch, cmds, {})

    custom_builder_called = []
    def custom_builder(ctx):
        custom_builder_called.append(ctx)
        return "CUSTOM_INSTANCE_PROMPT_OUTPUT"

    routed_invoker = RoutedSubprocessInvoker(prompt_builder=custom_builder)

    captured_runs = []
    def fake_run(argv, **kwargs):
        captured_runs.append({"argv": argv, "kwargs": kwargs})
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"action":"fail","error":"test"}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    routed_invoker.invoke({"work_type": "proposal_clarify"})
    assert len(custom_builder_called) == 1
    assert "CUSTOM_INSTANCE_PROMPT_OUTPUT" in captured_runs[0]["kwargs"].get("input", "")

