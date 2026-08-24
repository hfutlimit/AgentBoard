"""Proposal → Ticket 异步转化（Epic 96 扩展，2026-08-08 文档 #59）

Revision ID: w3x4y5z6a7b8
Revises: r8s9t0u1v2w3

1. proposals 新增 ticket_type / ticket_id（通用工单回填）；
2. ck_proposals_status 扩为 11 值（新增 pending / ticket_preparing / ticket_created）；
3. 新建 proposal_ticket_requests 表（(proposal_id, type) 唯一，幂等防重放）。

双后端兼容：SQLite 改列/约束用 batch_alter_table（重建表），MariaDB 直接 alter。
"""
from alembic import op
import sqlalchemy as sa

revision = "w3x4y5z6a7b8"
down_revision = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None

_STATUS_CHECK = (
    "status IN ('draft','pending','queued','analyzing','awaiting','answered',"
    "'converged','story_created','ticket_preparing','ticket_created','failed')"
)


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table("proposals") as batch:
            batch.add_column(sa.Column("ticket_type", sa.String(length=20),
                                       nullable=True, server_default=""))
            batch.add_column(sa.Column("ticket_id", sa.Integer(), nullable=True))
            batch.create_index("ix_proposals_ticket_id", ["ticket_id"])
            batch.drop_constraint("ck_proposals_status", type_="check")
            batch.create_check_constraint(
                "ck_proposals_status", _STATUS_CHECK,
            )
    else:
        op.add_column("proposals", sa.Column("ticket_type", sa.String(length=20),
                                             nullable=True, server_default=""))
        op.add_column("proposals", sa.Column("ticket_id", sa.Integer(), nullable=True))
        op.create_index("ix_proposals_ticket_id", "proposals", ["ticket_id"])
        op.drop_constraint("ck_proposals_status", "proposals", type_="check")
        op.create_check_constraint("ck_proposals_status", "proposals", _STATUS_CHECK)

    op.create_table(
        "proposal_ticket_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("parent_epic_id", sa.Integer(), nullable=True),
        sa.Column("parent_story_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=True,
                  server_default="pending"),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_epic_id"], ["epics.id"],
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_story_id"], ["stories.id"],
                                ondelete="SET NULL"),
        sa.CheckConstraint("type IN ('bug','epic','story','task')",
                           name="ck_ticket_req_type"),
        sa.CheckConstraint("status IN ('done','failed','pending','processing')",
                           name="ck_ticket_req_status"),
        sa.UniqueConstraint("proposal_id", "type",
                            name="uq_ticket_req_proposal_type"),
        sa.Index("ix_proposal_ticket_requests_proposal_id", "proposal_id"),
        sa.Index("ix_proposal_ticket_requests_status", "status"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("proposal_ticket_requests")

    if _is_sqlite():
        with op.batch_alter_table("proposals") as batch:
            batch.drop_index("ix_proposals_ticket_id")
            batch.drop_column("ticket_id")
            batch.drop_column("ticket_type")
            batch.drop_constraint("ck_proposals_status", type_="check")
            batch.create_check_constraint(
                "ck_proposals_status",
                "status IN ('draft','queued','analyzing','awaiting','answered',"
                "'converged','story_created','failed')",
            )
    else:
        op.drop_index("ix_proposals_ticket_id", table_name="proposals")
        op.drop_column("proposals", "ticket_id")
        op.drop_column("proposals", "ticket_type")
        op.drop_constraint("ck_proposals_status", "proposals", type_="check")
        op.create_check_constraint(
            "ck_proposals_status", "proposals",
            "status IN ('draft','queued','analyzing','awaiting','answered',"
            "'converged','story_created','failed')",
        )
