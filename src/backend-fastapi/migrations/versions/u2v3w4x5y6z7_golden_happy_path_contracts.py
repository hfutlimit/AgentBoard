"""golden happy path contracts: target epic, executor metadata, deferred reason

Revision ID: u2v3w4x5y6z7
Revises: t1u2v3w4x5y6
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa


revision = "u2v3w4x5y6z7"
down_revision = "t1u2v3w4x5y6"
branch_labels = None
depends_on = None

_REQUEST_TYPE_NEW = "type IN ('auto','auto_story','bug','epic','story','task')"
_REQUEST_TYPE_OLD = "type IN ('auto','bug','epic','story','task')"


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.add_column(sa.Column("target_epic_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_proposals_target_epic", "epics", ["target_epic_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_proposals_target_epic_id", ["target_epic_id"])

    with op.batch_alter_table("agent_instances") as batch:
        batch.add_column(sa.Column("executor_type", sa.String(40), nullable=True))
        batch.create_index("ix_agent_instances_executor_type", ["executor_type"])

    # 双后端兼容的 best-effort 回填；无法推导的行保留 NULL，调度端 fail closed。
    op.execute(sa.text(
        "UPDATE agent_instances SET executor_type='codex' "
        "WHERE executor_type IS NULL AND lower(cli_command) LIKE '%codex%'"
    ))
    op.execute(sa.text(
        "UPDATE agent_instances SET executor_type='workbuddy' "
        "WHERE executor_type IS NULL AND lower(cli_command) LIKE '%workbuddy%'"
    ))
    op.execute(sa.text(
        "UPDATE agent_instances SET executor_type='minimax' "
        "WHERE executor_type IS NULL AND lower(cli_command) LIKE '%minimax%'"
    ))

    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("assignment_deferred_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("assignment_deferred_at", sa.DateTime(), nullable=True))

    if _is_sqlite():
        with op.batch_alter_table("proposal_ticket_requests") as batch:
            batch.drop_constraint("ck_ticket_req_type", type_="check")
            batch.create_check_constraint("ck_ticket_req_type", _REQUEST_TYPE_NEW)
    else:
        op.drop_constraint(
            "ck_ticket_req_type", "proposal_ticket_requests", type_="check",
        )
        op.create_check_constraint(
            "ck_ticket_req_type", "proposal_ticket_requests", _REQUEST_TYPE_NEW,
        )


def downgrade() -> None:
    op.execute("DELETE FROM proposal_ticket_requests WHERE type='auto_story'")
    if _is_sqlite():
        with op.batch_alter_table("proposal_ticket_requests") as batch:
            batch.drop_constraint("ck_ticket_req_type", type_="check")
            batch.create_check_constraint("ck_ticket_req_type", _REQUEST_TYPE_OLD)
    else:
        op.drop_constraint(
            "ck_ticket_req_type", "proposal_ticket_requests", type_="check",
        )
        op.create_check_constraint(
            "ck_ticket_req_type", "proposal_ticket_requests", _REQUEST_TYPE_OLD,
        )

    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("assignment_deferred_at")
        batch.drop_column("assignment_deferred_reason")
    with op.batch_alter_table("agent_instances") as batch:
        batch.drop_index("ix_agent_instances_executor_type")
        batch.drop_column("executor_type")
    with op.batch_alter_table("proposals") as batch:
        batch.drop_index("ix_proposals_target_epic_id")
        batch.drop_constraint("fk_proposals_target_epic", type_="foreignkey")
        batch.drop_column("target_epic_id")
