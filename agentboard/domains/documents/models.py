"""Document 与 DocumentComment 实体（Epic 15：项目文档维护）。

独立于 Task 的 spec 字段，承载 memory / plan / knowledge / design 四类文档，
具备 draft → in-review → approved / cancelled 的评审工作流，
并通过评论支撑多成员 / 多 Agent 互相 review。

遵循 OpenSpec 增量式约束：不修改既有表结构，新增实体与端点。
存储双后端兼容（SQLite / MariaDB），迁移由 Alembic 管理。
"""
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..common.models import Base, utc_now


class DocumentType(StrEnum):
    MEMORY = "memory"
    PLAN = "plan"
    KNOWLEDGE = "knowledge"
    DESIGN = "design"


class DocumentStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    CANCELLED = "cancelled"


ALL_DOCUMENT_TYPES = [
    DocumentType.MEMORY, DocumentType.PLAN,
    DocumentType.KNOWLEDGE, DocumentType.DESIGN,
]
ALL_DOCUMENT_STATUSES = [
    DocumentStatus.DRAFT, DocumentStatus.IN_REVIEW,
    DocumentStatus.APPROVED, DocumentStatus.CANCELLED,
]


# 文档评审状态机（service.py 集中引用，参照 Task TRANSITIONS 模式）
DOCUMENT_TRANSITIONS = {
    DocumentStatus.DRAFT: {DocumentStatus.IN_REVIEW},
    DocumentStatus.IN_REVIEW: {
        DocumentStatus.APPROVED, DocumentStatus.CANCELLED, DocumentStatus.DRAFT,
    },
    DocumentStatus.APPROVED: {DocumentStatus.DRAFT},
    DocumentStatus.CANCELLED: set(),  # 终态，需重新编辑需先回到 draft
}


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "type IN ('memory','plan','knowledge','design')",
            name="ck_documents_type",
        ),
        CheckConstraint(
            "status IN ('draft','in_review','approved','cancelled')",
            name="ck_documents_status",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    epic_id: Mapped[int | None] = mapped_column(
        ForeignKey("epics.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    story_id: Mapped[int | None] = mapped_column(
        ForeignKey("stories.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(20), default=DocumentType.PLAN)
    status: Mapped[str] = mapped_column(String(20), default=DocumentStatus.DRAFT)
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class DocumentComment(Base):
    __tablename__ = "document_comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    author: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
