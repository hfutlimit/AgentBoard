"""可配置行为 Prompt 积木库（Task 4：PromptBlockRegistry）。

提供高内聚、纯净且确定性的行为提示词积木，供 PromptBuilder 组装。
绝不包含业务专有契约（如特定 JSON 输出格式），仅聚焦行为与执行规范。
"""
from __future__ import annotations

from typing import Any, Callable

PromptBlock = Callable[[dict[str, Any]], str]


def block_checkout_branch(context: dict[str, Any] | None = None) -> str:
    """分支切换前置指令。"""
    branch_hint = ""
    if context and context.get("branch"):
        branch_hint = f" (关联分支: {context.get('branch')})"
    return (
        "【分支准备】在执行同步与修改前：\n"
        f"1. 确定与当前工作项关联的工作分支{branch_hint}；\n"
        "2. 使用可用 Git/仓库工具切换并确认处于正确的工作分支；\n"
        "3. 验证当前活动分支后再开始后续操作。"
    )


def block_sync_code(context: dict[str, Any] | None = None) -> str:
    """代码同步前置指令。"""
    return (
        "【代码同步】在开始任务工作前：\n"
        "1. 使用可用的仓库/Git 工具同步当前工作分支的最新代码；\n"
        "2. 在同步完成前，不得开始修改项目源码；\n"
        "3. 若当前运行环境无 Git/仓库操作工具，严禁虚假声称已完成同步，请如实报告工具不可用。"
    )


def block_inspect_code(context: dict[str, Any] | None = None) -> str:
    """代码审查前置指令。"""
    return (
        "【代码审查】在修改代码、输出设计或向用户提问前：\n"
        "1. 必须先检索并审查本地实际代码、配置与既有架构模式；\n"
        "2. 顺藤摸瓜理清相关代码调用路径，基于真实代码现状推进；\n"
        "3. 严禁凭空猜测；凡是能从现有代码中直接查明的事实，绝不得向用户发问或盲目设计。"
    )


def block_read_documents(context: dict[str, Any] | None = None) -> str:
    """文档查阅指令。"""
    return (
        "【文档查阅】在开展工作前：\n"
        "1. 仔细阅读关联的项目文档、需求规范、架构方案（Design）及背景材料；\n"
        "2. 确保对业务目标、约束边界与设计意图有完整理解。"
    )


def block_load_memory(context: dict[str, Any] | None = None) -> str:
    """项目记忆与经验查阅指令。"""
    return (
        "【经验复用】充分参考提供的项目历史经验与教训（Project Learnings / MEMORY），"
        "将其作为启发和防坑指南，避免重复踩已知问题。"
    )


def block_read_comments(context: dict[str, Any] | None = None) -> str:
    """历史沟通与评论查阅指令。"""
    return (
        "【历史沟通】先阅读工作项的历史评论、讨论记录与评审意见，了解上下文脉络后再推进。"
    )


def block_leave_summary(context: dict[str, Any] | None = None) -> str:
    """工作总结与留痕指令。"""
    return (
        "【工作总结】工作完成后，必须留下清晰结构化的执行总结评论，明确包含：\n"
        "- 变更内容（Changes）\n"
        "- 关键设计与架构考量（Design Decisions）\n"
        "- 受影响的文件与组件（Affected Files/Components）\n"
        "- 测试与验证情况（Verification & Tests）\n"
        "- 未尽事项或风险提示（Notes & Caveats）"
    )


def block_reply_to_review(context: dict[str, Any] | None = None) -> str:
    """评审回复与回应指令。"""
    return (
        "【评审回应】若收到 Review 驳回意见：\n"
        "- 若认可意见（ACCEPTED）：说明具体修复措施及如何解决 Reviewer 关切；\n"
        "- 若申诉（CHALLENGED）：提供可验证的代码、测试或规范证据，客观阐明当前方案的合理性。"
    )


def block_learn_from_accepted_correction(context: dict[str, Any] | None = None) -> str:
    """接受纠错沉淀学习。"""
    return (
        "【纠错沉淀】当 Review 指出的问题被采纳并修复后，提炼出具有复用价值的项目教训，防止未来重蹈覆辙。"
    )


def block_learn_from_judgment_reversal(context: dict[str, Any] | None = None) -> str:
    """评审误判反思沉淀。"""
    return (
        "【评审反思】当之前的评审判断被申诉并更正后，复盘最初遗漏的关键证据，沉淀更准确的评审检查项。"
    )


PROMPT_BLOCK_REGISTRY: dict[str, PromptBlock] = {
    "checkout_branch": block_checkout_branch,
    "sync_code": block_sync_code,
    "inspect_code": block_inspect_code,
    "read_documents": block_read_documents,
    "load_memory": block_load_memory,
    "read_comments": block_read_comments,
    "leave_summary": block_leave_summary,
    "reply_to_review": block_reply_to_review,
    "learn_from_accepted_correction": block_learn_from_accepted_correction,
    "learn_from_judgment_reversal": block_learn_from_judgment_reversal,
}


def get_prompt_block(name: str) -> PromptBlock | None:
    """获取指定名称的 Prompt 积木生成器。"""
    return PROMPT_BLOCK_REGISTRY.get(name)