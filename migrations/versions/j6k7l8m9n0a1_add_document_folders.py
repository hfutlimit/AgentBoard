"""add document_folders & documents.folder_id (Epic 15 增强：文档文件夹/子文件夹)

Revision ID: j6k7l8m9n0a1
Revises: i5j6k7l8m9n0

为项目文档引入文件夹组织能力：

- 新建 ``document_folders`` 表（project_id + parent_id 自引用 + name），
  支持任意层级子文件夹。
- ``documents`` 增加可空 ``folder_id`` 列（FK → document_folders.id，
  ON DELETE SET NULL），NULL = 位于根目录。

删除文件夹的「子项上提」语义在 service 层实现（文档/子文件夹的 folder_id
改指被删文件夹的父级），因此数据库层不依赖级联删除子项。

双后端兼容（SQLite / MariaDB）：SQLite 的 ALTER TABLE ADD COLUMN 支持带
REFERENCES 的可空列，无需 batch 表重建；先建表再加列以满足外键引用顺序。
"""
from alembic import op
import sqlalchemy as sa

revision = "j6k7l8m9n0a1"
down_revision = "i5j6k7l8m9n0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "document_folders" not in tables:
        op.create_table(
            "document_folders",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("parent_id", sa.Integer(), nullable=True),
            sa.Column("name", sa.String(length=300), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["parent_id"], ["document_folders.id"], ondelete="CASCADE",
            ),
            sa.Index("ix_document_folders_project_id", "project_id"),
            sa.Index("ix_document_folders_parent_id", "parent_id"),
            sa.PrimaryKeyConstraint("id"),
        )

    columns = {column["name"] for column in inspector.get_columns("documents")}
    if "folder_id" not in columns:
        # SQLite 的 ALTER TABLE ADD COLUMN 原生支持带 REFERENCES 的可空列
        # （含 ON DELETE SET NULL），而 op.create_foreign_key 在 SQLite 下
        # 会抛 NotImplementedError。MariaDB / PostgreSQL 走标准 add_column + FK。
        if op.get_bind().dialect.name == "sqlite":
            op.execute(
                "ALTER TABLE documents ADD COLUMN folder_id INTEGER "
                "REFERENCES document_folders (id) ON DELETE SET NULL"
            )
        else:
            op.add_column(
                "documents",
                sa.Column("folder_id", sa.Integer(), nullable=True),
            )
            op.create_foreign_key(
                "fk_documents_folder_id_document_folders",
                "documents", "document_folders",
                ["folder_id"], ["id"], ondelete="SET NULL",
            )
        indexes = {index["name"] for index in inspector.get_indexes("documents")}
        if "ix_documents_folder_id" not in indexes:
            op.create_index("ix_documents_folder_id", "documents", ["folder_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("documents")}
    if "ix_documents_folder_id" in indexes:
        op.drop_index("ix_documents_folder_id", table_name="documents")
    columns = {column["name"] for column in inspector.get_columns("documents")}
    if "folder_id" in columns:
        op.drop_constraint("fk_documents_folder_id_document_folders", "documents", type_="foreignkey")
        op.drop_column("documents", "folder_id")
    op.drop_table("document_folders")
