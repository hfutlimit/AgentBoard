"""学习抽取器（Task 13：LearningExtractor）。

根据触发事件深度提炼结构化反思与可复用教训。
结构化反思框架（Self-Correction Reflection）：
1. What was missed? (遗漏点)
2. Why was it missed? (根因分析)
3. What evidence should have been checked? (核查依据)
4. Actionable rule (未来行动准则)
"""
from __future__ import annotations

import json
import re
from typing import Any
from pydantic import BaseModel, Field

from .evaluator import LearningCategory, LearningTriggerEvent


class ExtractedLesson(BaseModel):
    summary: str
    lesson: str
    category: str
    what_missed: str = ""
    why_missed: str = ""
    evidence_to_check: str = ""
    tags: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class LearningExtractor:
    """结构化深度反思提炼器。"""

    def _sanitize_context(self, text: str) -> str:
        """Sanitize context to prevent prompt injection."""
        text = text.strip()
        patterns = [
            r"ignore previous instructions",
            r"system:",
            r"<\|im_start\|>",
            r"<\|im_end\|>"
        ]
        for p in patterns:
            text = re.sub(p, "", text, flags=re.IGNORECASE)
        
        text = text[:500]
        if text:
            return f"=== CONTEXT START ===\n{text}\n=== CONTEXT END ==="
        return ""

    def extract(
        self,
        event: LearningTriggerEvent,
        invoker: Any = None,
    ) -> ExtractedLesson:
        """从学习事件中提炼出结构化经验教训与深度反思。"""
        raw_ctx = event.discussion_context.strip()
        ctx = self._sanitize_context(raw_ctx)
        cat = event.category.value

        # 提取相关技术与领域标签
        tags = [cat]
        if event.work_type:
            tags.append(str(event.work_type).lower())

        for kw in [
            "database", "sqlite", "mariadb", "migration", "index", "unique", "foreign_key",
            "async", "await", "lock", "timeout", "review", "auth", "token", "permission",
            "validation", "schema", "api", "cache", "redis", "csrf", "decimal", "float",
        ]:
            if kw in raw_ctx.lower() or kw in event.summary_hint.lower():
                tags.append(kw)

        if event.category == LearningCategory.ACCEPTED_REVIEW_FEEDBACK:
            summary = event.summary_hint or "采纳审查意见并修复缺陷"
            what_missed = f"实现时未满足的审查项: {ctx}"
            why_missed = "开发过程中未充分考虑边界条件、数据一致性或架构约束。"
            evidence_to_check = "修改代码前必须检索相关 schema 定义、调用上下文与异常处理路径。"
            lesson = (
                f"【复用经验】{summary}。\n"
                f"- 遗漏分析: {what_missed}\n"
                f"- 根因排查: {why_missed}\n"
                f"- 防范准则: 在后续类似开发中，必须在自测阶段主动核查：{evidence_to_check}"
            )
        elif event.category == LearningCategory.REVIEW_JUDGMENT_REVERSAL:
            summary = event.summary_hint or "评审误判经申诉后撤销改判"
            what_missed = f"评审时遗漏了已有代码上下文证据: {ctx}"
            why_missed = "评审仅凭局部代码或主观假设，未全局检索代码库实际调用逻辑。"
            evidence_to_check = "在驳回前必须使用代码检索工具复核全链路实现，严禁未查实即判错。"
            lesson = (
                f"【评审反思】{summary}。\n"
                f"- 误判原因: {why_missed}\n"
                f"- 必须核查: {evidence_to_check}\n"
                f"- 评审准则: 提出 Reject 时必须附带可复现证据或明确的规范依据。"
            )
        elif event.category == LearningCategory.QA_DEFECT:
            summary = event.summary_hint or "QA 阶段捕获到漏测缺陷"
            what_missed = f"开发自测遗漏的缺陷场景: {ctx}"
            why_missed = "测试用例覆盖不全，缺少针对异常输入或边界状态的验证。"
            evidence_to_check = "验收前必须编写针对性单元测试与集成测试覆盖此缺陷路径。"
            lesson = f"【质量教训】{summary}。开发阶段必须补齐自测用例：{what_missed}。"
        else:
            summary = event.summary_hint or "执行异常后成功恢复"
            what_missed = "偶发性异常或外部依赖故障"
            why_missed = "缺乏重试机制或超时熔断"
            evidence_to_check = "检查外部依赖可用性与网络重试配置"
            lesson = f"【恢复经验】{summary}。注意做好容灾与幂等重试。"

        return ExtractedLesson(
            summary=summary,
            lesson=lesson,
            category=cat,
            what_missed=what_missed,
            why_missed=why_missed,
            evidence_to_check=evidence_to_check,
            tags=list(set(tags)),
            confidence=event.confidence,
        )


learning_extractor = LearningExtractor()