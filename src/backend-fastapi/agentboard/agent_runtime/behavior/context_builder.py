"""执行上下文构建器（Task 6：ExecutionContextBuilder）。

在调用 PromptBuilder 之前，结构化收集与本工作项相关的全量业务上下文：
- 当前 WorkItem / Proposal / Story / Task
- 父级层级（Hierarchy：Proposal -> Epic -> Story -> Task）
- 关联文档（Linked Documents & Project Documents，受控预算）
- 历史评论与讨论（Comments & Review Feedback）
- 历史纠错经验（Relevant Learnings）
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from ..contract import ExecutionCommand, WorkType


class ContextDocument(BaseModel):
    id: int | None = None
    title: str
    type: str = "document"
    content_snippet: str = ""


class ContextComment(BaseModel):
    id: int | None = None
    author: str = ""
    content: str
    created_at: str = ""


class LearningContext(BaseModel):
    id: int | None = None
    category: str = ""
    summary: str
    lesson: str


class ExecutionContext(BaseModel):
    """结构化聚合的执行上下文。"""
    execution_id: str
    work_type: WorkType
    entity_type: str
    entity_id: int
    work_item: dict[str, Any] = Field(default_factory=dict)
    hierarchy: dict[str, Any] = Field(default_factory=dict)
    documents: list[ContextDocument] = Field(default_factory=list)
    comments: list[ContextComment] = Field(default_factory=list)
    learnings: list[LearningContext] = Field(default_factory=list)
    raw_context_summary: str = ""


class ExecutionContextBuilder:
    """上下文组装器。"""

    def __init__(self, retriever: Any = None):
        self.retriever = retriever

    def build(
        self,
        command: ExecutionCommand,
        client: Any = None,
        db: Any = None,
        max_doc_len: int = 4000,
        max_comments: int = 10,
    ) -> ExecutionContext:
        """从 DB / HTTP client 或 command.context 中提取结构化上下文。"""
        raw_ctx = command.context or {}
        work_item = dict(raw_ctx)

        # 1. 提取层级与对象信息
        hierarchy = {}
        if "proposal" in raw_ctx:
            hierarchy["proposal"] = raw_ctx["proposal"]
        if "epic" in raw_ctx:
            hierarchy["epic"] = raw_ctx["epic"]
        if "story" in raw_ctx:
            hierarchy["story"] = raw_ctx["story"]
        if "task" in raw_ctx:
            hierarchy["task"] = raw_ctx["task"]

        # 2. 提取评论列表
        comments_list: list[ContextComment] = []
        raw_comments = raw_ctx.get("comments") or []
        for c in raw_comments[-max_comments:]:
            if isinstance(c, dict):
                comments_list.append(
                    ContextComment(
                        id=c.get("id"),
                        author=str(c.get("author_username") or c.get("author") or "User"),
                        content=str(c.get("content") or ""),
                        created_at=str(c.get("created_at") or ""),
                    )
                )

        # 3. 提取关联文档（截断过长内容）
        docs_list: list[ContextDocument] = []
        raw_docs = raw_ctx.get("documents") or []
        for d in raw_docs:
            if isinstance(d, dict):
                cnt = str(d.get("content") or "")
                if len(cnt) > max_doc_len:
                    cnt = cnt[:max_doc_len] + "\n...(已截断)"
                docs_list.append(
                    ContextDocument(
                        id=d.get("id"),
                        title=str(d.get("title") or "Untitled Document"),
                        type=str(d.get("type") or "document"),
                        content_snippet=cnt,
                    )
                )

        # 4. 检索相关项目经验（Learnings）
        learnings_list: list[LearningContext] = []
        if self.retriever is not None:
            retrieved = self.retriever.retrieve(
                project_id=work_item.get("project_id"),
                agent_id=work_item.get("agent_id"),
                work_type=command.work_type,
                title=work_item.get("title", ""),
                description=work_item.get("description", ""),
                db=db,
            )
            for item in retrieved:
                if isinstance(item, dict):
                    learnings_list.append(
                        LearningContext(
                            id=item.get("id"),
                            category=item.get("category", ""),
                            summary=item.get("summary", ""),
                            lesson=item.get("lesson", ""),
                        )
                    )
        elif "learnings" in raw_ctx:
            for item in raw_ctx["learnings"]:
                if isinstance(item, dict):
                    learnings_list.append(
                        LearningContext(
                            id=item.get("id"),
                            category=item.get("category", ""),
                            summary=item.get("summary", ""),
                            lesson=item.get("lesson", ""),
                        )
                    )

        # 5. 生成结构化 summary
        summary_parts = []
        title = work_item.get("title") or f"{command.entity_type} #{command.entity_id}"
        summary_parts.append(f"目标工作项: {title}")
        if work_item.get("description"):
            summary_parts.append(f"描述:\n{work_item['description']}")
        if work_item.get("spec"):
            summary_parts.append(f"规格要求:\n{work_item['spec']}")
        if docs_list:
            doc_summaries = "\n".join([f"- [{d.type}] {d.title}: {d.content_snippet[:200]}..." for d in docs_list])
            summary_parts.append(f"关联文档:\n{doc_summaries}")
        if comments_list:
            comment_summaries = "\n".join([f"- {c.author}: {c.content}" for c in comments_list])
            summary_parts.append(f"历史评论:\n{comment_summaries}")

        raw_summary = "\n\n".join(summary_parts)

        return ExecutionContext(
            execution_id=command.execution_id,
            work_type=command.work_type,
            entity_type=command.entity_type,
            entity_id=command.entity_id,
            work_item=work_item,
            hierarchy=hierarchy,
            documents=docs_list,
            comments=comments_list,
            learnings=learnings_list,
            raw_context_summary=raw_summary,
        )


execution_context_builder = ExecutionContextBuilder()