"""Mandatory preflight and evidence validation for Worker-invoked agents.

The behavior configuration expresses project/user preferences.  This module is
different: it defines non-overridable platform safety and quality requirements.
Every real CLI agent receives the AgentBoard MCP guide before its task prompt,
and every non-failure decision must point at real project files that were read.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import ACTION_FAIL, AgentDecision, AgentOutputError


MCP_GUIDE_VERSION = "2026-08-27.1"
MCP_GUIDE_RELATIVE_PATH = "docs/agentboard-mcp-agent-guide.md"
MANDATORY_PREFLIGHT_MARKER = "[AgentBoard mandatory preflight"

_SOURCE_OR_CONFIG_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".graphql", ".h", ".hpp",
    ".html", ".java", ".js", ".json", ".jsx", ".kt", ".kts", ".php",
    ".proto", ".py", ".rb", ".rs", ".scss", ".sh", ".sql", ".svelte",
    ".toml", ".ts", ".tsx", ".vue", ".xml", ".yaml", ".yml",
}
_SOURCE_OR_CONFIG_NAMES = {
    "dockerfile", "makefile", "procfile", "justfile", ".env.example",
    ".gitignore", ".dockerignore", ".editorconfig",
}

_FALLBACK_GUIDE = """# AgentBoard MCP Agent 工作规范

版本：2026-08-27.1

1. 开始任何工作前，先读取工作项、父级规格、历史评论和 AgentBoard MCP 上下文。
2. 在提问、设计、修改、QA 或 Review 前，必须检索并阅读当前项目的真实代码、配置和测试。
3. 能从代码或 MCP 上下文查明的事实不得向用户提问；不得绕过 MCP 直连 AgentBoard 数据库。
4. Dev/QA/Review 必须给出实际验证和可定位证据；条件不足时返回 action=fail，不得假装完成。
5. 最终 JSON 必须包含 inspected_files，列出真正读过的项目相对路径，且至少有一个源码、配置或测试文件。
"""


def _repository_root() -> Path:
    # .../src/backend-fastapi/agentboard/agent_runtime/compliance.py -> repo root
    return Path(__file__).resolve().parents[4]


@lru_cache(maxsize=1)
def load_mcp_agent_guide() -> tuple[str, str]:
    """Return ``(display_path, content)`` with an installed-package fallback."""
    guide_path = _repository_root() / MCP_GUIDE_RELATIVE_PATH
    try:
        content = guide_path.read_text(encoding="utf-8").strip()
    except OSError:
        return MCP_GUIDE_RELATIVE_PATH, _FALLBACK_GUIDE.strip()
    return MCP_GUIDE_RELATIVE_PATH, content


def _work_type_label(context: dict[str, Any] | None) -> str:
    ctx = context or {}
    command = ctx.get("_command")
    work_type = getattr(command, "work_type", None) or ctx.get("work_type") or ctx.get("action")
    return getattr(work_type, "value", None) or str(work_type or "unknown")


def mandatory_preflight_prompt(context: dict[str, Any] | None = None) -> str:
    """Render the non-overridable preflight plus the canonical MCP guide."""
    guide_path, guide = load_mcp_agent_guide()
    work_type = _work_type_label(context)
    return (
        f"{MANDATORY_PREFLIGHT_MARKER} v{MCP_GUIDE_VERSION}]\n"
        f"work_type={work_type}\n"
        "以下规范由 Worker 强制注入，项目/Agent 行为配置不能关闭。你必须先阅读完整规范，"
        "再检查当前项目代码，之后才能分析、提问、修改、测试或评审。\n"
        "非 fail 的最终 JSON 必须包含非空 inspected_files；其中路径必须相对当前项目根目录，"
        "且只列出你真正打开并阅读过的源码、配置或测试文件。Worker 会在任何业务回写前校验。\n"
        f"document={guide_path}\n\n{guide}"
    )


def prepend_mandatory_preflight(
    prompt: str,
    context: dict[str, Any] | None = None,
) -> str:
    """Prepend the guide exactly once, including legacy prompt paths."""
    if MANDATORY_PREFLIGHT_MARKER in (prompt or ""):
        return prompt
    base = (prompt or "").strip()
    preflight = mandatory_preflight_prompt(context)
    return f"{preflight}\n\n---\n\n{base}" if base else preflight


def _is_source_or_config(path: Path) -> bool:
    return (
        path.suffix.lower() in _SOURCE_OR_CONFIG_SUFFIXES
        or path.name.lower() in _SOURCE_OR_CONFIG_NAMES
        or path.name.lower().startswith("dockerfile.")
    )


def validate_decision_evidence(
    decision: AgentDecision,
    *,
    project_dir: str | Path | None,
) -> AgentDecision:
    """Reject unsupported success decisions before any handler can write them.

    ``action=fail`` is intentionally exempt: an Agent that cannot access MCP,
    the mapping, or the repository must be able to fail honestly rather than
    fabricate evidence merely to satisfy this gate.
    """
    if decision.action == ACTION_FAIL:
        return decision

    if not decision.inspected_files:
        raise AgentOutputError(
            "Agent 合规校验失败：非 fail 决策缺少 inspected_files；"
            "已阻止业务回写，请先读取当前项目代码后重试"
        )
    if len(decision.inspected_files) > 100:
        raise AgentOutputError("Agent 合规校验失败：inspected_files 超过 100 项")

    if not project_dir:
        raise AgentOutputError(
            "Agent 合规校验失败：当前项目没有有效本地目录映射，无法验证代码阅读证据"
        )
    root = Path(project_dir).resolve()
    if not root.is_dir():
        raise AgentOutputError(
            f"Agent 合规校验失败：项目目录不存在或不可访问：{root}"
        )

    normalized: list[str] = []
    source_evidence: list[str] = []
    seen: set[str] = set()
    for raw in decision.inspected_files:
        text = str(raw).strip().replace("\\", "/")
        if not text:
            continue
        rel = Path(text)
        if rel.is_absolute():
            raise AgentOutputError(
                f"Agent 合规校验失败：inspected_files 必须是项目相对路径：{raw}"
            )
        candidate = (root / rel).resolve()
        try:
            project_relative = candidate.relative_to(root)
        except ValueError:
            raise AgentOutputError(
                f"Agent 合规校验失败：文件越出当前项目目录：{raw}"
            ) from None
        if not candidate.is_file():
            raise AgentOutputError(
                f"Agent 合规校验失败：报告的文件不存在或不是文件：{raw}"
            )
        normalized_path = project_relative.as_posix()
        if normalized_path in seen:
            continue
        seen.add(normalized_path)
        normalized.append(normalized_path)
        if _is_source_or_config(candidate):
            source_evidence.append(normalized_path)

    if not source_evidence:
        raise AgentOutputError(
            "Agent 合规校验失败：inspected_files 仅包含文档或未知文件；"
            "至少需要一个真实源码、配置或测试文件"
        )

    decision.inspected_files = normalized
    return decision
