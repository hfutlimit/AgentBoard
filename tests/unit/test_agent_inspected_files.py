"""Tests for agent inspected_files reporting (2026-08-26 增强).

Validates:
- AgentDecision.from_dict parses inspected_files list (str or list)
- AgentDecision.from_dict tolerates missing inspected_files (default [])
- clarify / ticket / story build_*_prompt 顶部包含 铁律 段 + project_dir
- handlers 在 handle_decision 之前/之后 log agent 报告的 inspected_files
- handler _log_inspected 不在 agent 给空 list 时抛
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from agentboard.processors.config import AgentDecision  # noqa: E402


def test_from_dict_parses_inspected_files_list():
    d = AgentDecision.from_dict({
        "action": "ask",
        "questions": ["q1"],
        "summary": "s",
        "inspected_files": ["a/b.py", "c/d.ts"],
    })
    assert d.inspected_files == ["a/b.py", "c/d.ts"]


def test_from_dict_parses_inspected_files_single_string():
    d = AgentDecision.from_dict({
        "action": "ask",
        "questions": ["q1"],
        "inspected_files": "only_one.py",
    })
    assert d.inspected_files == ["only_one.py"]


def test_from_dict_missing_inspected_files_defaults_empty():
    d = AgentDecision.from_dict({"action": "ask", "questions": ["q1"]})
    assert d.inspected_files == []


def test_from_dict_strips_empty_strings():
    d = AgentDecision.from_dict({
        "action": "ask",
        "questions": ["q1"],
        "inspected_files": ["", "  ", "real.py"],
    })
    assert d.inspected_files == ["real.py"]


def test_clarify_prompt_includes_iron_law_and_project_dir():
    from agentboard.processors.handlers.clarify import build_clarify_prompt
    prompt = build_clarify_prompt({
        "proposal_id": 7, "title": "demo", "content": "x",
        "current_round": 1, "history": [],
        "project_dir": "E:\\Projects\\AgentBoard",
    })
    assert "铁律" in prompt
    assert "inspected_files" in prompt
    assert "E:\\Projects\\AgentBoard" in prompt
    assert "read_file" in prompt and "glob" in prompt


def test_ticket_prompt_includes_iron_law_and_project_dir():
    from agentboard.processors.handlers.ticket import build_ticket_prompt
    prompt = build_ticket_prompt({
        "proposal_id": 7, "title": "demo", "content": "x",
        "ticket_type": "story", "project_dir": "E:\\Projects\\AgentBoard",
    })
    assert "铁律" in prompt
    assert "inspected_files" in prompt
    assert "E:\\Projects\\AgentBoard" in prompt


def test_story_prompt_includes_iron_law_and_project_dir():
    from agentboard.processors.handlers.story import build_story_prompt
    prompt = build_story_prompt({
        "story_id": 381, "title": "demo", "description": "x",
        "tasks": [], "needs_design": False, "project_dir": "E:\\Projects\\AgentBoard",
    })
    assert "铁律" in prompt
    assert "inspected_files" in prompt
    assert "E:\\Projects\\AgentBoard" in prompt


def test_task_prompt_includes_iron_law_and_project_dir():
    from agentboard.processors.handlers.story import build_task_prompt
    prompt = build_task_prompt({
        "task": {"id": 1096, "title": "t", "type": "dev", "status": "in_progress"},
        "story_id": 381, "needs_design": False, "assignee_id": None,
        "project_dir": "E:\\Projects\\AgentBoard",
    })
    assert "铁律" in prompt
    assert "inspected_files" in prompt
    assert "E:\\Projects\\AgentBoard" in prompt


def test_clarify_handler_log_inspected_does_not_raise_on_empty(caplog):
    """Empty list is allowed; just emits an info line saying '未报'."""
    import logging
    from agentboard.processors.handlers.clarify import ClarifyHandler
    # 仅构造 handler 调 _log_inspected；不接 client 也行
    class _Stub:
        def __init__(self): self.client = None; self.config = None
    h = ClarifyHandler.__new__(ClarifyHandler)
    h._log_inspected(
        AgentDecision(action="ask", questions=["q"]),
        label="clarify-test",
    )  # 没 inspected_files → 仅 log 一行，不抛


def test_clarify_handler_log_inspected_does_not_raise_on_files(caplog):
    from agentboard.processors.handlers.clarify import ClarifyHandler
    h = ClarifyHandler.__new__(ClarifyHandler)
    h._log_inspected(
        AgentDecision(action="ask", questions=["q"],
                      inspected_files=["src/foo.py", "src/bar.py"]),
        label="clarify-test",
    )  # 不抛
