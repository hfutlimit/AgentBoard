"""Add actor identity fields to RunEvent records.

Revision ID: 3ad7e2f1c4b8
Revises: 2bc6c2d30a55
Create Date: 2026-08-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3ad7e2f1c4b8"
down_revision: Union[str, None] = "2bc6c2d30a55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_run_events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "actor_user_id",
                sa.Integer(),
                sa.ForeignKey(
                    "users.id",
                    name="fk_agent_run_events_actor_user_id_users",
                    ondelete="SET NULL",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "api_key_id",
                sa.Integer(),
                sa.ForeignKey(
                    "api_keys.id",
                    name="fk_agent_run_events_api_key_id_api_keys",
                    ondelete="SET NULL",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "agent_registry_id",
                sa.Integer(),
                sa.ForeignKey(
                    "agents.id",
                    name="fk_agent_run_events_agent_registry_id_agents",
                    ondelete="SET NULL",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("worker_id", sa.String(length=64), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_agent_run_events_actor_user_id"),
            ["actor_user_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_agent_run_events_api_key_id"),
            ["api_key_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_agent_run_events_agent_registry_id"),
            ["agent_registry_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_agent_run_events_worker_id"),
            ["worker_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_run_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_run_events_worker_id"))
        batch_op.drop_index(batch_op.f("ix_agent_run_events_agent_registry_id"))
        batch_op.drop_index(batch_op.f("ix_agent_run_events_api_key_id"))
        batch_op.drop_index(batch_op.f("ix_agent_run_events_actor_user_id"))
        batch_op.drop_column("worker_id")
        batch_op.drop_column("agent_registry_id")
        batch_op.drop_column("api_key_id")
        batch_op.drop_column("actor_user_id")
