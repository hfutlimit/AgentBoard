"""执行上下文构建器（Task 6：ExecutionContextBuilder）。

在调用 PromptBuilder 之前，结构化收集与本工作项相关的全量业务上下文：

- **WorkItem 自身**：title / description / spec（来自 command.context，由 Handler 预填）
- **Hierarchy**（Proposal / Epic / Story / Task）：来自 command.context
- **关联文档（Linked Documents & Project Documents）**：按 ``behavior.preparation.read_documents`` 主动从 DB 拉
- **历史评论与讨论（Comments）**：按 ``behavior.collaboration.read_comments`` 主动从 DB 拉
- **历史纠错经验（Relevant Learnings）**：按 ``behavior.preparation.load_memory`` 主动从 DB 拉

设计原则（review 2026-08-26 修正）：

- Configuration stores **intent**, not mechanism. 用户在 Behavior Config 里勾选
  ``read_documents`` / ``load_memory`` / ``read_comments``，ContextBuilder 才是落实
  这些开关的机制。Handler 不再需要各自记得塞 documents/comments。
- 每个 section 都有显式的开/关，没有「隐式 0 默认」造成的语义模糊。
- ``client`` 参数已被 ``db`` 替代（DB 是唯一来源，HTTP client 只是 Server 自身）。
  保留作为 deprecated 入口，向后兼容。
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from .models import EffectiveBehaviorConfig
from ..contract import ExecutionCommand, WorkType

logger = logging.getLogger(__name__)


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
    """结构化聚合的执行上下文。

    Review 2026-08-26 P1 #1 修复（Learning 路径）：
    memory 三段 — Learnings（主）/ Playbook（次）/ Episodes（辅助）— 全部
    受 ``behavior.preparation.load_memory`` 控制。
    """

    execution_id: str
    work_type: WorkType
    entity_type: str
    entity_id: int
    work_item: dict[str, Any] = Field(default_factory=dict)
    hierarchy: dict[str, Any] = Field(default_factory=dict)
    documents: list[ContextDocument] = Field(default_factory=list)
    comments: list[ContextComment] = Field(default_factory=list)
    learnings: list[LearningContext] = Field(default_factory=list)
    # Review 2026-08-26 P1 #1：新增 playbook（次 memory 源）+ episodes（辅助）
    playbook: list[ContextDocument] = Field(default_factory=list)
    episodes: list[ContextDocument] = Field(default_factory=list)
    raw_context_summary: str = ""
    # 标记各 section 是否真从 DB 拉过（而不是仅从 ctx 拿）
    sources_resolved: dict[str, bool] = Field(
        default_factory=lambda: {
            "documents_from_db": False,
            "comments_from_db": False,
            "learnings_from_db": False,
            "playbook_from_db": False,
            "episodes_from_db": False,
            "documents_from_ctx": False,
            "comments_from_ctx": False,
            "learnings_from_ctx": False,
            "playbook_from_ctx": False,
            "episodes_from_ctx": False,
        }
    )


class ExecutionContextBuilder:
    """执行上下文组装器（Behavior-aware Assembler）。

    行为契约：

    1. 任何 section 都不再隐式总是打开 —— 必须 ``behavior`` 显式开启。
    2. ``db`` 是主数据源；``client`` 仅作向后兼容，传入则按 HTTP 路径拉取（暂未实现，fallback 到 db）。
    3. ``command.context`` 仍允许预填部分数据（Handler 兼容），但只要 ``db`` 可用，
       DB 数据优先（DB 是 server 端 source of truth）。
    """

    def __init__(self, retriever: Any = None):
        self.retriever = retriever

    def build(
        self,
        command: ExecutionCommand,
        behavior: EffectiveBehaviorConfig | None = None,
        client: Any = None,  # deprecated — DB 是唯一来源；保留兼容旧调用
        db: Any = None,
        max_doc_len: int = 4000,
        max_comments: int = 10,
    ) -> ExecutionContext:
        """从 DB / command.context 组装 ExecutionContext。

        Args:
            command: 统一执行命令
            behavior: 行为配置；None 表示「全开」（向后兼容 e2e preview 场景）
            client: deprecated，保留兼容
            db: SQLAlchemy Session
            max_doc_len: 单文档 content 截断长度
            max_comments: 评论保留条数（取最近 N 条）
        """
        raw_ctx = command.context or {}
        work_item = dict(raw_ctx)

        # ----- 默认 behavior -----
        # 不传 behavior 时保持全开（兼容 e2e / preview 场景）；
        # 真实 runtime 调用应总传 EffectiveBehaviorConfig
        if behavior is None:
            from .models import CollaborationBehavior, PreparationBehavior
            behavior = EffectiveBehaviorConfig(
                preparation=PreparationBehavior(
                    read_documents=True, load_memory=True, inspect_code=True
                ),
                collaboration=CollaborationBehavior(read_comments=True),
            )

        # ----- 1. Hierarchy（始终由 Handler 预填在 ctx） -----
        hierarchy: dict[str, Any] = {}
        for key in ("proposal", "epic", "story", "task"):
            if key in raw_ctx:
                hierarchy[key] = raw_ctx[key]

        # ----- 2. Comments -----
        comments_list, comments_sources = self._resolve_comments(
            entity_type=command.entity_type,
            entity_id=command.entity_id,
            work_item=work_item,
            behavior=behavior,
            raw_ctx=raw_ctx,
            db=db,
            max_comments=max_comments,
        )

        # ----- 3. Documents -----
        docs_list, docs_sources = self._resolve_documents(
            entity_type=command.entity_type,
            entity_id=command.entity_id,
            work_item=work_item,
            behavior=behavior,
            raw_ctx=raw_ctx,
            db=db,
            max_doc_len=max_doc_len,
        )

        # ----- 4. Learnings (主 memory 源) -----
        learnings_list, learnings_sources = self._resolve_learnings(
            entity_type=command.entity_type,
            entity_id=command.entity_id,
            work_item=work_item,
            behavior=behavior,
            raw_ctx=raw_ctx,
            db=db,
        )

        # ----- 4b. Playbook + Episodes (次/辅助 memory 源, Review 2026-08-26 P1 #1) -----
        playbook_list, episodes_list, memory_sources = self._resolve_playbook_and_episodes(
            entity_type=command.entity_type,
            entity_id=command.entity_id,
            work_item=work_item,
            behavior=behavior,
            raw_ctx=raw_ctx,
            db=db,
        )

        # ----- 5. Structured summary -----
        summary_parts: list[str] = []
        title = work_item.get("title") or f"{command.entity_type} #{command.entity_id}"
        summary_parts.append(f"目标工作项: {title}")
        if work_item.get("description"):
            summary_parts.append(f"描述:\n{work_item['description']}")
        if work_item.get("spec"):
            summary_parts.append(f"规格要求:\n{work_item['spec']}")
        if docs_list:
            doc_summaries = "\n".join(
                f"- [{d.type}] {d.title}: {d.content_snippet[:200]}..." for d in docs_list
            )
            summary_parts.append(f"关联文档:\n{doc_summaries}")
        if comments_list:
            comment_summaries = "\n".join(
                f"- {c.author}: {c.content}" for c in comments_list
            )
            summary_parts.append(f"历史评论:\n{comment_summaries}")
        if learnings_list:
            learn_summaries = "\n".join(
                f"- [{l.category}] {l.summary}" for l in learnings_list
            )
            summary_parts.append(f"相关项目经验:\n{learn_summaries}")
        if playbook_list:
            playbook_summaries = "\n".join(
                f"- [playbook] {p.title}: {p.content_snippet[:200]}..." for p in playbook_list
            )
            summary_parts.append(f"项目实践:\n{playbook_summaries}")
        if episodes_list:
            ep_summaries = "\n".join(
                f"- [episode] {e.title}: {e.content_snippet[:200]}..." for e in episodes_list
            )
            summary_parts.append(f"相似历史案例:\n{ep_summaries}")

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
            playbook=playbook_list,
            episodes=episodes_list,
            raw_context_summary=raw_summary,
            sources_resolved={
                **docs_sources,
                **comments_sources,
                **learnings_sources,
                **memory_sources,
            },
        )

    # ----------------------------------------------------------------
    # 私有 section resolver
    # ----------------------------------------------------------------

    def _resolve_comments(
        self,
        *,
        entity_type: str,
        entity_id: int,
        work_item: dict,
        behavior: EffectiveBehaviorConfig,
        raw_ctx: dict,
        db: Any,
        max_comments: int,
    ) -> tuple[list[ContextComment], dict[str, bool]]:
        """解析评论：按 behavior.collaboration.read_comments 控制；DB 优先。"""
        sources = {"comments_from_db": False, "comments_from_ctx": False}

        if not behavior.collaboration.read_comments:
            return [], sources

        # 1. 优先从 DB 拉
        if db is not None and entity_id:
            try:
                from ...features.work_items.models import Comment
                from sqlalchemy import select, or_

                # Comment 同时支持 task_id / story_id / epic_id 三个维度
                comments: list[ContextComment] = []
                if entity_type == "task":
                    stmt = select(Comment).where(Comment.task_id == entity_id)
                elif entity_type == "story":
                    stmt = select(Comment).where(Comment.story_id == entity_id)
                elif entity_type == "epic":
                    stmt = select(Comment).where(Comment.epic_id == entity_id)
                else:
                    # 其它类型（proposal 等）暂不直查 Comment
                    stmt = None
                if stmt is not None:
                    stmt = stmt.order_by(Comment.created_at.desc()).limit(max_comments)
                    records = list(db.scalars(stmt).all())
                    for r in records:
                        comments.append(
                            ContextComment(
                                id=r.id,
                                author=r.author or "User",
                                content=r.content or "",
                                created_at=r.created_at.isoformat() if r.created_at else "",
                            )
                        )
                if comments:
                    sources["comments_from_db"] = True
                    return comments, sources
            except Exception as e:
                logger.warning("ContextBuilder: 从 DB 拉评论失败，将尝试 ctx 兜底: %s", e)

        # 2. 兜底：ctx 里已预填
        raw_comments = raw_ctx.get("comments") or []
        if raw_comments:
            comments: list[ContextComment] = []
            for c in raw_comments[-max_comments:]:
                if isinstance(c, dict):
                    comments.append(
                        ContextComment(
                            id=c.get("id"),
                            author=str(c.get("author_username") or c.get("author") or "User"),
                            content=str(c.get("content") or ""),
                            created_at=str(c.get("created_at") or ""),
                        )
                    )
            if comments:
                sources["comments_from_ctx"] = True
                return comments, sources

        return [], sources

    def _resolve_documents(
        self,
        *,
        entity_type: str,
        entity_id: int,
        work_item: dict,
        behavior: EffectiveBehaviorConfig,
        raw_ctx: dict,
        db: Any,
        max_doc_len: int,
    ) -> tuple[list[ContextDocument], dict[str, bool]]:
        """解析关联文档：按 behavior.preparation.read_documents + document_sources 控制；DB 优先。"""
        sources = {"documents_from_db": False, "documents_from_ctx": False}

        if not behavior.preparation.read_documents:
            return [], sources

        # 决定要拉哪些 source
        source_types = self._enabled_document_sources(behavior)

        # 1. 优先从 DB 拉
        if db is not None and entity_id:
            try:
                docs = self._fetch_documents_from_db(
                    db=db,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    source_types=source_types,
                    max_doc_len=max_doc_len,
                )
                if docs:
                    sources["documents_from_db"] = True
                    return docs, sources
            except Exception as e:
                logger.warning("ContextBuilder: 从 DB 拉文档失败，将尝试 ctx 兜底: %s", e)

        # 2. 兜底：ctx 里已预填
        raw_docs = raw_ctx.get("documents") or []
        if raw_docs:
            docs: list[ContextDocument] = []
            for d in raw_docs:
                if isinstance(d, dict):
                    cnt = str(d.get("content") or "")
                    if len(cnt) > max_doc_len:
                        cnt = cnt[:max_doc_len] + "\n...(已截断)"
                    docs.append(
                        ContextDocument(
                            id=d.get("id"),
                            title=str(d.get("title") or "Untitled Document"),
                            type=str(d.get("type") or "document"),
                            content_snippet=cnt,
                        )
                    )
            if docs:
                sources["documents_from_ctx"] = True
                return docs, sources

        return [], sources

    def _fetch_documents_from_db(
        self,
        *,
        db: Any,
        entity_type: str,
        entity_id: int,
        source_types: set[str],
        max_doc_len: int,
    ) -> list[ContextDocument]:
        """从 DB 拉关联文档 + 项目级文档。"""
        if not source_types:
            return []

        from sqlalchemy import select
        from ...features.documents.models import Document

        project_id = self._resolve_project_id(db, entity_type, entity_id)
        if project_id is None:
            return []

        want_linked = "linked_documents" in source_types
        want_project = "project_documents" in source_types

        docs: list[ContextDocument] = []
        seen_ids: set[int] = set()

        if want_linked:
            # 关联文档：epic_id / story_id 维度
            if entity_type in ("task", "story", "epic"):
                # 先从 work_item / ctx 拿到 epic_id / story_id
                epic_id: int | None = None
                story_id: int | None = None
                if entity_type == "task":
                    from ...features.work_items.models import Task
                    t = db.get(Task, entity_id)
                    if t:
                        story_id = t.story_id
                elif entity_type == "story":
                    story_id = entity_id

                conds = []
                if story_id is not None:
                    conds.append(Document.story_id == story_id)
                if entity_type == "epic":
                    conds.append(Document.epic_id == entity_id)
                if conds:
                    stmt = select(Document).where(Document.project_id == project_id)
                    from sqlalchemy import or_
                    stmt = stmt.where(or_(*conds))
                    stmt = stmt.order_by(Document.updated_at.desc()).limit(5)
                    for d in db.scalars(stmt).all():
                        if d.id in seen_ids:
                            continue
                        seen_ids.add(d.id)
                        cnt = d.content or ""
                        if len(cnt) > max_doc_len:
                            cnt = cnt[:max_doc_len] + "\n...(已截断)"
                        docs.append(
                            ContextDocument(
                                id=d.id, title=d.title, type=d.type, content_snippet=cnt
                            )
                        )

        if want_project:
            # 项目级全局文档：仅 type in (knowledge, plan) 且 story_id IS NULL
            stmt = (
                select(Document)
                .where(Document.project_id == project_id)
                .where(Document.story_id.is_(None))
                .where(Document.epic_id.is_(None))
                .where(Document.type.in_(["knowledge", "plan"]))
                .order_by(Document.updated_at.desc())
                .limit(3)
            )
            for d in db.scalars(stmt).all():
                if d.id in seen_ids:
                    continue
                seen_ids.add(d.id)
                cnt = d.content or ""
                if len(cnt) > max_doc_len:
                    cnt = cnt[:max_doc_len] + "\n...(已截断)"
                docs.append(
                    ContextDocument(
                        id=d.id, title=d.title, type=d.type, content_snippet=cnt
                    )
                )

        return docs

    def _enabled_document_sources(self, behavior: EffectiveBehaviorConfig) -> set[str]:
        """根据 behavior.document_sources 决定实际要拉哪些 source type。

        空列表表示「显式清空」，不拉任何 source（保留 behavior.document_sources == [] 的语义）。
        """
        srcs = behavior.document_sources or []
        if not srcs:
            # document_sources 是空 list → 用户显式禁用了所有文档源
            return set()
        return {s.type for s in srcs if isinstance(s.type, str)}

    def _resolve_project_id(self, db: Any, entity_type: str, entity_id: int) -> int | None:
        """根据 entity_type + entity_id 推 project_id。"""
        try:
            if entity_type == "task":
                from ...features.work_items.models import Task
                t = db.get(Task, entity_id)
                return t.project_id if t else None
            if entity_type == "story":
                # Story 表无 project_id 字段，需经 epic_id → Epic.project_id 间接拿
                from ...features.projects.models import Story, Epic
                s = db.get(Story, entity_id)
                if not s:
                    return None
                e = db.get(Epic, s.epic_id)
                return e.project_id if e else None
            if entity_type == "epic":
                from ...features.projects.models import Epic
                e = db.get(Epic, entity_id)
                return e.project_id if e else None
            if entity_type == "proposal":
                from ...features.proposals.models import Proposal
                p = db.get(Proposal, entity_id)
                return p.project_id if p else None
        except Exception as e:
            logger.warning("ContextBuilder: 解析 project_id 失败 (%s #%s): %s",
                           entity_type, entity_id, e)
        return None

    def _resolve_learnings(
        self,
        *,
        entity_type: str,
        entity_id: int,
        work_item: dict,
        behavior: EffectiveBehaviorConfig,
        raw_ctx: dict,
        db: Any,
    ) -> tuple[list[LearningContext], dict[str, bool]]:
        """解析项目经验：按 behavior.preparation.load_memory 控制；DB 优先。

        Review 2026-08-26 P1 #1 修复（Learning 路径）：
        此方法现在负责**单一权威**的 memory retrieval —— 旧 StoryHandler 自行
        调 ``_recall_episodes`` /api/learning/recall 已废弃。新流程：

            behavior.preparation.load_memory
                ↓ True
            LearningRetriever（Correction Learning）  ← 主 source
                ↓
            ProjectPlaybook（项目约定）              ← 次 source
                ↓
            Episode RAG（历史案例）                   ← 辅助 source

        Handler 不再各自 hardcode memory 拉取，全部走 ContextBuilder。
        行为开关一处定义，全 runtime 生效。
        """
        sources = {"learnings_from_db": False, "learnings_from_ctx": False}

        if not behavior.preparation.load_memory:
            return [], sources

        # 1. 优先用 retriever（DB 路径）—— 主 source：新 Correction Learning
        if self.retriever is not None and db is not None:
            try:
                retrieved = self.retriever.retrieve(
                    project_id=work_item.get("project_id"),
                    agent_id=work_item.get("agent_id"),
                    work_type=work_item.get("work_type"),
                    title=work_item.get("title", ""),
                    description=work_item.get("description", ""),
                    db=db,
                )
                if retrieved:
                    sources["learnings_from_db"] = True
                    return [
                        LearningContext(
                            id=item.get("id"),
                            category=item.get("category", ""),
                            summary=item.get("summary", ""),
                            lesson=item.get("lesson", ""),
                        )
                        for item in retrieved
                        if isinstance(item, dict)
                    ], sources
            except Exception as e:
                logger.warning("ContextBuilder: retriever 失败，将尝试 ctx 兜底: %s", e)

        # 2. 兜底：ctx 里已预填（legacy / preview 场景）
        items = raw_ctx.get("learnings") or []
        if items:
            out: list[LearningContext] = []
            for item in items:
                if isinstance(item, dict):
                    out.append(
                        LearningContext(
                            id=item.get("id"),
                            category=item.get("category", ""),
                            summary=item.get("summary", ""),
                            lesson=item.get("lesson", ""),
                        )
                    )
            if out:
                sources["learnings_from_ctx"] = True
                return out, sources

        return [], sources

    def _resolve_playbook_and_episodes(
        self,
        *,
        entity_type: str,
        entity_id: int,
        work_item: dict,
        behavior: EffectiveBehaviorConfig,
        raw_ctx: dict,
        db: Any,
    ) -> tuple[list[ContextDocument], list[ContextDocument], dict[str, bool]]:
        """拉取项目 Playbook + 历史 Episode 作为次/辅助 memory 源。

        Review 2026-08-26 P1 #1 修复（Learning 路径）：
        - Project Playbook → ``execution_context.playbook``（次 source）
        - 历史 Episode  → ``execution_context.episodes`` （辅助 source，lowest priority）

        都受 ``behavior.preparation.load_memory`` 控制（同一开关；不引入新开关
        避免用户配置爆炸）。

        Episode 仅在 project_id 可解析 + DB 可达时拉；其它情况返回空 list。
        """
        sources = {
            "playbook_from_db": False,
            "playbook_from_ctx": False,
            "episodes_from_db": False,
            "episodes_from_ctx": False,
        }

        if not behavior.preparation.load_memory:
            return [], [], sources

        playbook: list[ContextDocument] = []
        episodes: list[ContextDocument] = []

        # 1. Project Playbook（DB 拉 / 兜底 ctx）
        playbook = self._fetch_playbook(
            db=db,
            work_item=work_item,
            raw_ctx=raw_ctx,
            sources=sources,
        )
        # 2. 历史 Episode（DB 拉 / 兜底 ctx）—— 复用旧的 recall_episodes 逻辑
        episodes = self._fetch_similar_episodes(
            db=db,
            work_item=work_item,
            raw_ctx=raw_ctx,
            sources=sources,
        )

        return playbook, episodes, sources

    def _fetch_playbook(
        self, *, db: Any, work_item: dict, raw_ctx: dict, sources: dict[str, bool],
    ) -> list[ContextDocument]:
        """拉项目 Playbook。"""
        # 兜底：ctx 里已预填
        items = raw_ctx.get("playbook") or []
        if items:
            out: list[ContextDocument] = []
            for it in items:
                if isinstance(it, dict):
                    out.append(
                        ContextDocument(
                            id=it.get("id"),
                            title=str(it.get("title") or "Project Playbook"),
                            type="playbook",
                            content_snippet=str(it.get("summary") or it.get("content") or "")[:2000],
                        )
                    )
            if out:
                sources["playbook_from_ctx"] = True
                return out

        # DB 拉
        project_id = work_item.get("project_id")
        if not project_id or db is None:
            return []
        try:
            from ...features.learning.memory import get_playbook
            rows = get_playbook(db, project_id=int(project_id))
            if rows:
                sources["playbook_from_db"] = True
                return [
                    ContextDocument(
                        id=r.get("id") if isinstance(r, dict) else None,
                        title=str((r.get("title") if isinstance(r, dict) else r.title) or "Project Playbook"),
                        type="playbook",
                        content_snippet=str((r.get("summary") if isinstance(r, dict) else r.summary) or "")[:2000],
                    )
                    for r in rows
                ]
        except Exception as e:
            logger.warning("ContextBuilder: 拉 Project Playbook 失败: %s", e)
        return []

    def _fetch_similar_episodes(
        self, *, db: Any, work_item: dict, raw_ctx: dict, sources: dict[str, bool],
    ) -> list[ContextDocument]:
        """拉 Similar Past Episodes（旧 Episode RAG，作为 auxiliary memory）。"""
        # 兜底：ctx 里已预填
        items = raw_ctx.get("episodes") or raw_ctx.get("recalled") or []
        if items:
            out: list[ContextDocument] = []
            for ep in items:
                if isinstance(ep, dict):
                    out.append(
                        ContextDocument(
                            id=ep.get("id"),
                            title=str(ep.get("title") or "Similar Episode"),
                            type="episode",
                            content_snippet=str(ep.get("summary") or "")[:2000],
                        )
                    )
            if out:
                sources["episodes_from_ctx"] = True
                return out

        project_id = work_item.get("project_id")
        if not project_id or db is None:
            return []
        try:
            from ...features.learning.memory import recall_episodes
            rows = recall_episodes(
                db,
                project_id=int(project_id),
                spec=" ".join([
                    str(work_item.get("title") or ""),
                    str(work_item.get("description") or "")[:800],
                ]).strip()[:2000],
                top_k=5,
            )
            if rows:
                sources["episodes_from_db"] = True
                return [
                    ContextDocument(
                        id=ep.get("episode_id") or ep.get("id"),
                        title=str(ep.get("title") or f"Task #{ep.get('task_id', '')}"),
                        type="episode",
                        content_snippet=str(ep.get("summary") or "")[:2000],
                    )
                    for ep in rows
                ]
        except Exception as e:
            logger.warning("ContextBuilder: 拉 Similar Episodes 失败: %s", e)
        return []


execution_context_builder = ExecutionContextBuilder()
