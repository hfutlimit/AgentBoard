"""学习抽取器（Task 13：LearningExtractor）。

根据触发事件提炼结构化的可复用教训。
包含规则解析器与 LLM 提取降级方案，保证非阻塞。
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
    tags: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class LearningExtractor:
    """结构化教训提炼器。"""

    def extract(self, event: LearningTriggerEvent) -> ExtractedLesson:
        """从学习事件中提炼出结构化经验教训。"""
        # 默认启发式提取规则（Heuristic Fallback）
        ctx = event.discussion_context.strip()
        cat = event.category.value

        # 提取标签
        tags = [cat]
        if event.work_type:
            tags.append(str(event.work_type).lower())

        for kw in ["database", "sqlite", "migration", "async", "lock", "timeout", "review", "auth", "schema"]:
            if kw in ctx.lower() or kw in event.summary_hint.lower():
                tags.append(kw)

        if event.category == LearningCategory.ACCEPTED_REVIEW_FEEDBACK:
            summary = event.summary_hint or "接受审查意见并完善实现"
            lesson = (
                f"在后续同类任务中，必须提前预防审查指出的缺陷：{ctx[:300]}"
                if len(ctx) > 10
                else "交付前先自测审查项，确保边界条件与异常处理完备。"
            )
        elif event.category == LearningCategory.REVIEW_JUDGMENT_REVERSAL:
            summary = event.summary_hint or "评审误判经申诉后撤销并改判通过"
            lesson = (
                f"评审审查时需充分核实开发者提供的代码与证据，避免主观误判：{ctx[:300]}"
                if len(ctx) > 10
                else "评审驳回前必须在本地复核可验证事实，勿轻易假设未做检查。"
            )
        elif event.category == LearningCategory.QA_DEFECT:
            summary = event.summary_hint or "QA 阶段捕获到漏测缺陷"
            lesson = f"开发阶段应补齐针对性测试用例，覆盖：{ctx[:300]}"
        else:
            summary = event.summary_hint or "执行异常后成功恢复"
            lesson = f"注意防范偶发性失败与环境依赖：{ctx[:300]}"

        return ExtractedLesson(
            summary=summary,
            lesson=lesson,
            category=cat,
            tags=list(set(tags)),
            confidence=event.confidence,
        )


learning_extractor = LearningExtractor()