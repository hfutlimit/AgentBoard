"""proposal auto ticket, cancellation, and auto request resolution

Revision ID: c3d4e5f6g7h8
Revises: b1c2d3e4f5a6, b2c3d4e5f6g7
Create Date: 2026-08-27
"""
from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6g7h8"
down_revision = ("b1c2d3e4f5a6", "b2c3d4e5f6g7")
branch_labels = None
depends_on = None

_PROPOSAL_STATUS_WITH_CANCELLED = (
    "status IN ('draft','pending','queued','analyzing','awaiting','answered',"
    "'converged','story_created','ticket_preparing','ticket_created','failed','cancelled')"
)
_PROPOSAL_STATUS_OLD = (
    "status IN ('draft','pending','queued','analyzing','awaiting','answered',"
    "'converged','story_created','ticket_preparing','ticket_created','failed')"
)
_REQUEST_TYPE_WITH_AUTO = "type IN ('auto','bug','epic','story','task')"
_REQUEST_TYPE_OLD = "type IN ('bug','epic','story','task')"


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table("proposals") as batch:
            batch.add_column(sa.Column(
                "auto_create_ticket", sa.Boolean(), nullable=False,
                server_default=sa.false(),
            ))
            batch.drop_constraint("ck_proposals_status", type_="check")
            batch.create_check_constraint(
                "ck_proposals_status", _PROPOSAL_STATUS_WITH_CANCELLED,
            )
        with op.batch_alter_table("proposal_ticket_requests") as batch:
            batch.add_column(sa.Column(
                "resolved_type", sa.String(length=20), nullable=False,
                server_default="",
            ))
            batch.drop_constraint("ck_ticket_req_type", type_="check")
            batch.create_check_constraint(
                "ck_ticket_req_type", _REQUEST_TYPE_WITH_AUTO,
            )
    else:
        op.add_column("proposals", sa.Column(
            "auto_create_ticket", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ))
        op.drop_constraint("ck_proposals_status", "proposals", type_="check")
        op.create_check_constraint(
            "ck_proposals_status", "proposals", _PROPOSAL_STATUS_WITH_CANCELLED,
        )
        op.add_column("proposal_ticket_requests", sa.Column(
            "resolved_type", sa.String(length=20), nullable=False,
            server_default="",
        ))
        op.drop_constraint(
            "ck_ticket_req_type", "proposal_ticket_requests", type_="check",
        )
        op.create_check_constraint(
            "ck_ticket_req_type", "proposal_ticket_requests", _REQUEST_TYPE_WITH_AUTO,
        )


def downgrade() -> None:
    # 旧版 schema 无法表示这两种值，先转回可表示的安全状态。
    op.execute("UPDATE proposals SET status='pending' WHERE status='cancelled'")
    op.execute("DELETE FROM proposal_ticket_requests WHERE type='auto'")
    if _is_sqlite():
        with op.batch_alter_table("proposal_ticket_requests") as batch:
            batch.drop_constraint("ck_ticket_req_type", type_="check")
            batch.create_check_constraint("ck_ticket_req_type", _REQUEST_TYPE_OLD)
            batch.drop_column("resolved_type")
        with op.batch_alter_table("proposals") as batch:
            batch.drop_constraint("ck_proposals_status", type_="check")
            batch.create_check_constraint("ck_proposals_status", _PROPOSAL_STATUS_OLD)
            batch.drop_column("auto_create_ticket")
    else:
        op.drop_constraint(
            "ck_ticket_req_type", "proposal_ticket_requests", type_="check",
        )
        op.create_check_constraint(
            "ck_ticket_req_type", "proposal_ticket_requests", _REQUEST_TYPE_OLD,
        )
        op.drop_column("proposal_ticket_requests", "resolved_type")
        op.drop_constraint("ck_proposals_status", "proposals", type_="check")
        op.create_check_constraint(
            "ck_proposals_status", "proposals", _PROPOSAL_STATUS_OLD,
        )
        op.drop_column("proposals", "auto_create_ticket")
