"""学习检索器（Task 16：LearningRetriever）。

根据项目、当前任务类型、工作项标题与描述检索最相关的历史纠错教训。
检索打分模型：
Score = Base(1.0)
      + 2.0 (WorkType 匹配)
      + 1.5 (Agent 专属匹配)
      + 1.0 * Tag/Keyword 命中数
      * Confidence
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...features.learning.models import Learning


class LearningRetriever:
    """经验检索器。"""

    def retrieve(
        self,
        project_id: int | None = None,
        agent_id: int | None = None,
        work_type: Any = None,
        title: str = "",
        description: str = "",
        db: Session | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """检索 top-k 最相关经验。"""
        if db is None or project_id is None:
            return []

        wt_str = str(work_type).lower() if work_type else ""
        if "." in wt_str:
            wt_str = wt_str.split(".")[-1]

        stmt = select(Learning).where(Learning.project_id == project_id)
        records = list(db.scalars(stmt).all())
        if not records:
            return []

        search_corpus = f"{title} {description}".lower()
        words = set(re.findall(r"\w+", search_corpus))

        scored_items: list[tuple[float, Learning]] = []
        for rec in records:
            score = 1.0
            # 1. WorkType 匹配加分
            if rec.work_type and wt_str and rec.work_type.lower() == wt_str:
                score += 2.0
            # 2. Agent 匹配加分
            if agent_id is not None and rec.agent_id == agent_id:
                score += 1.5
            # 3. 标签/关键词命中加分
            try:
                tags = json.loads(rec.tags_json) if rec.tags_json else []
            except Exception:
                tags = []
            for tag in tags:
                if str(tag).lower() in words:
                    score += 1.0

            # 4. 置信度乘数
            final_score = score * (rec.confidence or 1.0)
            scored_items.append((final_score, rec))

        # 按分数倒序排序
        scored_items.sort(key=lambda x: x[0], reverse=True)
        top_items = scored_items[: max(1, min(limit, 10))]

        results: list[dict[str, Any]] = []
        for s, rec in top_items:
            results.append({
                "id": rec.id,
                "project_id": rec.project_id,
                "agent_id": rec.agent_id,
                "work_type": rec.work_type,
                "category": rec.category,
                "summary": rec.summary,
                "lesson": rec.lesson,
                "confidence": rec.confidence,
                "score": s,
            })
        return results


learning_retriever = LearningRetriever()