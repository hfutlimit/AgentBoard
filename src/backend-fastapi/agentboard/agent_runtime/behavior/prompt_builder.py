"""Prompt 构建器（Task 5：PromptBuilder）。

按照清晰分层组合生成最终送往 Agent 的运行时 Prompt。
分层结构：
1. Platform Contract（平台契约与输出规范，preview 时可隐藏）
2. WorkType Core Instructions（各 WorkType 核心业务使命）
3. Selected Behavior Blocks（由 AgentBehaviorConfig 选中的行为积木，依序排列）
4. Document / MCP Source Guidance（文档与 MCP 数据源指引）
5. Project Instructions（项目全局指引）
6. Agent Additional Instructions（用户/Agent 补充指令）
7. Relevant Project Learnings（历史项目纠错经验注入）
8. Execution Context（执行对象及上下文）
"""
from __future__ import annotations

from typing import Any

from .models import EffectiveBehaviorConfig
from .prompt_blocks import PROMPT_BLOCK_REGISTRY, get_prompt_block
from ..contract import WorkType


class PromptBuilder:
    """运行时 Prompt 组装引擎。"""

    def __init__(self, platform_contract: str | None = None):
        self.default_platform_contract = platform_contract or (
            "【平台契约】你必须以合法的 JSON 格式返回决策，不得包含任何额外前缀或 Markdown 围栏外的注释。"
        )

    def _render_work_type_core(self, work_type: WorkType | str | None) -> str:
        wt = str(work_type).lower() if work_type else ""
        if "." in wt:
            wt = wt.split(".")[-1]

        if wt in (WorkType.PROPOSAL_CLARIFY.value, "proposal_clarify", "clarify"):
            return (
                "【核心职责：需求澄清（Proposal Clarify）】\n"
                "你的目标是审查需求提案并澄清不确定性。在提问前，必须通过代码与文档审查自行推导答案。\n"
                "仅当遇到真正的业务规则矛盾、产品取舍、重大架构决策等无法通过本地上下文确定的问题时，才向用户提出针对性开放问题。"
            )
        if wt in (WorkType.PROPOSAL_CONVERT.value, "proposal_convert", "convert"):
            return (
                "【核心职责：工单转化（Proposal Conversion）】\n"
                "你的目标是将定稿的需求提案转化为可执行的工单体系（设计文档、Epic、Story 及不少于 3 个明确的拆分 Task）。"
            )
        if wt in (WorkType.DESIGN.value, "design"):
            return (
                "【核心职责：架构与技术设计（Design）】\n"
                "你的目标是产出详尽的技术设计方案。切勿仅凭工单标题空想，必须结合现有代码实现与设计规范给出落地方案。"
            )
        if wt in (WorkType.IMPLEMENTATION.value, WorkType.TASK_IMPLEMENT.value, "implementation", "dev", "task"):
            return (
                "【核心职责：代码实现（Implementation）】\n"
                "你的目标是高标准完成代码开发、单元测试与自测验证。必须在充分理解现有代码路径后再着手修改。"
            )
        if wt in (WorkType.QA.value, "qa", "test"):
            return (
                "【核心职责：质量保证与验证（QA）】\n"
                "你的目标是对交付成果进行严谨的验收测试，提供可复现的测试证据与清晰的 PASS / FAIL 结论。"
            )
        if wt in (
            WorkType.DESIGN_REVIEW.value,
            WorkType.IMPLEMENTATION_REVIEW.value,
            WorkType.QA_REVIEW.value,
            WorkType.TASK_REVIEW.value,
            "review",
            "design_review",
            "implementation_review",
            "qa_review",
        ):
            return (
                "【核心职责：交叉独立评审（Review）】\n"
                "你的目标是独立审查交付物质量。审查必须基于客观证据，驳回时必须指出具体缺陷、违背的设计原则及修复建议。"
            )
        return "【核心职责】请按既定工作项要求高质高效完成交付。"

    def _render_behavior_blocks(
        self,
        behavior: EffectiveBehaviorConfig,
        context: dict[str, Any] | None = None,
    ) -> list[str]:
        blocks = []
        ctx = context or {}

        # 1. Preparation blocks (严格顺序：checkout_branch -> sync_code -> inspect_code -> read_documents -> load_memory)
        prep = behavior.preparation
        if prep.checkout_branch:
            b = get_prompt_block("checkout_branch")
            if b:
                blocks.append(b(ctx))
        if prep.sync_code:
            b = get_prompt_block("sync_code")
            if b:
                blocks.append(b(ctx))
        if prep.inspect_code:
            b = get_prompt_block("inspect_code")
            if b:
                blocks.append(b(ctx))
        if prep.read_documents:
            b = get_prompt_block("read_documents")
            if b:
                blocks.append(b(ctx))
        if prep.load_memory:
            b = get_prompt_block("load_memory")
            if b:
                blocks.append(b(ctx))

        # 2. Collaboration blocks
        collab = behavior.collaboration
        if collab.read_comments:
            b = get_prompt_block("read_comments")
            if b:
                blocks.append(b(ctx))
        if collab.leave_summary:
            b = get_prompt_block("leave_summary")
            if b:
                blocks.append(b(ctx))
        if collab.reply_to_review:
            b = get_prompt_block("reply_to_review")
            if b:
                blocks.append(b(ctx))

        # 3. Learning blocks
        learn = behavior.learning
        if learn.accepted_correction:
            b = get_prompt_block("learn_from_accepted_correction")
            if b:
                blocks.append(b(ctx))
        if learn.judgment_reversal:
            b = get_prompt_block("learn_from_judgment_reversal")
            if b:
                blocks.append(b(ctx))

        return blocks

    def _render_document_sources(self, behavior: EffectiveBehaviorConfig) -> str:
        if not behavior.document_sources:
            return ""
        lines = ["【数据源与参考资料】"]
        for src in behavior.document_sources:
            if src.type == "project_documents":
                lines.append("- 项目核心文档库（Project Documents）：包含全局架构、规范与约定。")
            elif src.type == "linked_documents":
                lines.append("- 工作项关联文档（Linked Documents）：本工单绑定的需求与设计文档。")
            elif src.type == "mcp":
                scope_info = f" (检索范围: {src.scope})" if src.scope else ""
                lines.append(f"- 外部 MCP 数据源 [{src.name or src.source_id}]{scope_info}：如需外部上下文可通过 MCP 工具主动查询。")
        return "\n".join(lines)

    def _render_learnings(self, learnings: list[dict[str, Any]] | None) -> str:
        if not learnings:
            return ""
        lines = [
            "【历史项目经验（Project Learnings）】",
            "以下为本项目的历史纠错经验与经验教训，请在当前任务中作为防坑指南参考：",
        ]
        for idx, item in enumerate(learnings, 1):
            summary = item.get("summary", "")
            lesson = item.get("lesson", "")
            cat = item.get("category", "")
            cat_str = f"[{cat}] " if cat else ""
            lines.append(f"{idx}. {cat_str}{summary}\n   教训: {lesson}")
        return "\n".join(lines)

    def build(
        self,
        work_type: WorkType | str | None,
        behavior: EffectiveBehaviorConfig,
        context: dict[str, Any] | None = None,
        learnings: list[dict[str, Any]] | None = None,
        project_instructions: str | None = None,
        platform_contract: str | None = None,
        preview_mode: bool = False,
    ) -> str:
        """组装完整的运行时 Prompt。"""
        sections: list[str] = []
        ctx = context or {}

        # 1. Platform Contract
        if not preview_mode:
            contract_text = platform_contract or self.default_platform_contract
            if contract_text:
                sections.append(contract_text)

        # 2. WorkType Core
        core_text = self._render_work_type_core(work_type)
        if core_text:
            sections.append(core_text)

        # 3. Behavior Blocks
        behavior_blocks = self._render_behavior_blocks(behavior, ctx)
        if behavior_blocks:
            sections.append("【执行行为指引】\n" + "\n\n".join(behavior_blocks))

        # 4. Document / MCP Sources
        sources_text = self._render_document_sources(behavior)
        if sources_text:
            sections.append(sources_text)

        # 5. Project Instructions
        if project_instructions and project_instructions.strip():
            sections.append(f"【项目级指引】\n{project_instructions.strip()}")

        # 6. Additional Instructions (User / Agent configured)
        if behavior.additional_instructions and behavior.additional_instructions.strip():
            sections.append(f"【补充指令】\n{behavior.additional_instructions.strip()}")

        # 7. Relevant Learnings
        learning_text = self._render_learnings(learnings or ctx.get("learnings"))
        if learning_text:
            sections.append(learning_text)

        # 8. Execution Context (Non-preview only)
        if not preview_mode:
            payload_str = ctx.get("raw_context_summary") or ctx.get("prompt_body")
            if payload_str:
                sections.append(f"【工作项上下文】\n{payload_str}")

        return "\n\n---\n\n".join(sections)


prompt_builder = PromptBuilder()