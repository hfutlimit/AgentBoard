from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agentboard.processors import invokers
from agentboard.processors.compliance import (
    MANDATORY_PREFLIGHT_MARKER,
    MCP_GUIDE_RELATIVE_PATH,
    MCP_GUIDE_VERSION,
    load_mcp_agent_guide,
    prepend_mandatory_preflight,
    validate_decision_evidence,
)
from agentboard.processors.config import AgentDecision, AgentOutputError
from agentboard.processors.contract import ExecutionCommand, WorkType
from agentboard.processors.invokers import (
    CallableProcessorInvoker,
    ComplianceEnforcingInvoker,
    SubprocessProcessorInvoker,
)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "docs" / "notes.md").write_text("notes\n", encoding="utf-8")
    return root


def test_canonical_mcp_guide_is_versioned_and_loadable():
    path, content = load_mcp_agent_guide()
    assert path == MCP_GUIDE_RELATIVE_PATH
    assert f"版本：{MCP_GUIDE_VERSION}" in content
    assert "inspected_files" in content
    assert "Review" in content
    assert "QA" in content


def test_preflight_is_non_optional_and_only_prepended_once():
    context = {"work_type": "implementation"}
    first = prepend_mandatory_preflight("business prompt", context)
    second = prepend_mandatory_preflight(first, context)
    assert first == second
    assert first.startswith(MANDATORY_PREFLIGHT_MARKER)
    assert "项目/Agent 行为配置不能关闭" in first
    assert "business prompt" in first


def test_non_failure_decision_requires_inspected_files(tmp_path: Path):
    root = _project(tmp_path)
    with pytest.raises(AgentOutputError, match="缺少 inspected_files"):
        validate_decision_evidence(
            AgentDecision(action="ask", questions=["真实业务取舍是什么？"]),
            project_dir=root,
        )


def test_reported_file_must_exist_inside_project(tmp_path: Path):
    root = _project(tmp_path)
    with pytest.raises(AgentOutputError, match="不存在"):
        validate_decision_evidence(
            AgentDecision(action="approve", inspected_files=["src/missing.py"]),
            project_dir=root,
        )

    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")
    with pytest.raises(AgentOutputError, match="越出当前项目目录"):
        validate_decision_evidence(
            AgentDecision(action="approve", inspected_files=["../outside.py"]),
            project_dir=root,
        )


def test_document_only_evidence_is_rejected(tmp_path: Path):
    root = _project(tmp_path)
    with pytest.raises(AgentOutputError, match="至少需要一个真实源码"):
        validate_decision_evidence(
            AgentDecision(action="reject", inspected_files=["docs/notes.md"]),
            project_dir=root,
        )


def test_real_code_evidence_is_normalized_and_accepted(tmp_path: Path):
    root = _project(tmp_path)
    decision = validate_decision_evidence(
        AgentDecision(
            action="story_handled",
            inspected_files=["src\\feature.py", "src/feature.py"],
        ),
        project_dir=root,
    )
    assert decision.inspected_files == ["src/feature.py"]


def test_honest_fail_does_not_require_fabricated_evidence():
    decision = AgentDecision(action="fail", error="MCP unavailable")
    assert validate_decision_evidence(decision, project_dir=None) is decision


def test_compliance_wrapper_blocks_decision_before_caller_can_persist(tmp_path: Path):
    root = _project(tmp_path)
    delegate = CallableProcessorInvoker(
        lambda _ctx: AgentDecision(action="approve", comment="LGTM")
    )
    guarded = ComplianceEnforcingInvoker(delegate)
    with pytest.raises(AgentOutputError, match="缺少 inspected_files"):
        guarded.invoke({"project_dir": str(root), "work_type": "implementation_review"})


def test_subprocess_prompt_contains_guide_for_nested_task_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = _project(tmp_path)
    mapping = tmp_path / "project-mappings.json"
    mapping.write_text(
        json.dumps({"projects": {"7": {"local_dir": str(root)}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTBOARD_LOCAL_MAPPINGS", str(mapping))
    monkeypatch.setattr(invokers, "_prompt_builder", lambda _ctx: "business prompt")

    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"action":"approve","comment":"ok","inspected_files":["src/feature.py"]}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    decision = SubprocessProcessorInvoker("fake-agent").invoke(
        {"task": {"id": 9, "project_id": 7}, "work_type": "implementation_review"}
    )

    assert decision.action == "approve"
    assert captured["cwd"] == str(root)
    assert MANDATORY_PREFLIGHT_MARKER in str(captured["input"])
    assert MCP_GUIDE_RELATIVE_PATH in str(captured["input"])
    assert "business prompt" in str(captured["input"])


def test_prepared_prompt_keeps_full_handler_business_context(monkeypatch: pytest.MonkeyPatch):
    business = (
        "## 提案 #42\nREAL_PROPOSAL_BODY\n"
        "## 历史问答（全量重放）\n- [第1轮 · codex] Q: REAL_QUESTION\n"
        '{"action":"ask","questions":[...],"inspected_files":[...]}'
    )
    monkeypatch.setattr(invokers, "_prompt_builder", lambda _ctx: business)
    context = {"title": "Proposal 42", "content": "REAL_PROPOSAL_BODY"}
    context["_command"] = ExecutionCommand(
        execution_id="proposal_42",
        work_type=WorkType.PROPOSAL_CLARIFY,
        entity_type="proposal",
        entity_id=42,
        context=context,
    )

    prompt = invokers.build_prompt(context)

    assert "【核心职责：需求澄清" in prompt
    assert "【当前工作项完整业务上下文与决策协议】" in prompt
    assert "REAL_PROPOSAL_BODY" in prompt
    assert "REAL_QUESTION" in prompt
    assert '"action":"ask"' in prompt
