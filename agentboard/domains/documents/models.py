"""Document 与 DocumentComment 实体（Epic 15：项目文档维护）。

独立于 Task 的 spec 字段，承载 memory / plan / knowledge / design 四类文档，
具备 draft → in-review → approved / cancelled 的评审工作流，
并通过评论支撑多成员 / 多 Agent 互相 review。

Epic 139 新增：DocumentRevision（不可变快照）+ 当前指针
current_revision_id / current_revision_number。每次内容/标题保存形成新 revision，
客户端在 save 时提交 expected_revision_number 做乐观锁，并发冲突 → 409。

遵循 OpenSpec 增量式约束：不修改既有表结构，新增实体与端点。
存储双后端兼容（SQLite / MariaDB），迁移由 Alembic 管理。
"""
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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


class DocumentFolder(Base):
    """文档文件夹（Epic 15 增强：项目文档支持文件夹 / 子文件夹）。

    与 Document 分离建模（而非在 documents 上自引用），避免触碰
    ``type IN ('memory','plan','knowledge','design')`` 的既有 CheckConstraint。

    - ``parent_id``：自引用上级文件夹，NULL = 顶层文件夹。
    - 删除文件夹时，其直接子文档与子文件夹上提至父文件夹（service 层处理），
      因此子项在数据库层均不会因文件夹删除而级联丢失。
    """

    __tablename__ = "document_folders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_folders.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


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
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_folders.id", ondelete="SET NULL"), nullable=True, index=True,
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


# ---------------------------------------------------------------------------
# Epic 139：不可变 Revision + 乐观锁（expected_revision_number → 409）
# ---------------------------------------------------------------------------

class DocumentRevision(Base):
    """文档正文的一次不可变快照。

    - 同一 document_id 下 revision_number 单调递增（联合唯一）；
    - 不可修改、不可物理删除（保留历史全链路）；
    - restore 行为不是改写历史，而是把旧版 content 复制为**新** revision。
    """

    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision_number", name="uq_document_revisions_doc_revnum"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 可空 author_id（用户不存在 / 系统恢复等场景；写时复制 Document.author_id 当时值）
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # 修改人/恢复人展示名（独立于 User 表；user 删后仍能展示）
    author: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 必填（提交时强制 ≤500 字）
    change_note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # 标记是否由 restore 操作产生（便于 UI 区分常规保存与回滚）
    is_restore: Mapped[bool] = mapped_column(default=False, nullable=False)
    # 若是 restore 产生的，记录被恢复的源 revision_number（可空）
    restored_from_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
