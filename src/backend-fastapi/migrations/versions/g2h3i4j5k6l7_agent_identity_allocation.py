"""agent identity, task allocation, and capability profiles

Revision ID: g2h3i4j5k6l7
Revises: f1g2h3i4j5k6
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa


revision = "g2h3i4j5k6l7"
down_revision = "f1g2h3i4j5k6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("agent_registry_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("active_slot", sa.String(length=10), nullable=True, server_default="active"),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("match_reason", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_registry_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("task_id", "active_slot", name="uq_task_assignment_active_slot"),
        sa.CheckConstraint(
            "source IN ('claim','arbitration','schedule','manual','worker')",
            name="ck_task_assignment_source",
        ),
        sa.CheckConstraint(
            "status IN ('active','completed','released','cancelled')",
            name="ck_task_assignment_status",
        ),
    )
    op.create_index("ix_task_assignments_task_id", "task_assignments", ["task_id"])
    op.create_index(
        "ix_task_assignments_agent_registry_id", "task_assignments", ["agent_registry_id"]
    )
    op.create_index("ix_task_assignments_user_id", "task_assignments", ["user_id"])

    op.create_table(
        "task_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("agent_registry_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reason", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_registry_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("task_id", "agent_registry_id", name="uq_task_application_agent"),
        sa.CheckConstraint(
            "status IN ('pending','accepted','rejected','withdrawn')",
            name="ck_task_application_status",
        ),
    )
    op.create_index("ix_task_applications_task_id", "task_applications", ["task_id"])
    op.create_index(
        "ix_task_applications_agent_registry_id", "task_applications", ["agent_registry_id"]
    )
    op.create_index("ix_task_applications_user_id", "task_applications", ["user_id"])

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column(
            "needed_capabilities", sa.Text(), nullable=False, server_default="[]",
        ))
        batch_op.add_column(sa.Column("complexity", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column(
            "domain_tags", sa.Text(), nullable=False, server_default="[]",
        ))
        batch_op.add_column(sa.Column(
            "assignment_mode", sa.String(length=20), nullable=False, server_default="claim",
        ))
        batch_op.add_column(sa.Column("current_assignment_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_tasks_current_assignment_id", "task_assignments",
            ["current_assignment_id"], ["id"], ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_tasks_complexity", "complexity IS NULL OR (complexity >= 1 AND complexity <= 5)",
        )
        batch_op.create_check_constraint(
            "ck_tasks_assignment_mode", "assignment_mode IN ('claim','arbitrated')",
        )
        batch_op.create_index(
            "ix_tasks_current_assignment_id", ["current_assignment_id"], unique=False,
        )

    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.add_column(sa.Column("agent_registry_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_api_keys_agent_registry_id", "agents", ["agent_registry_id"], ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_api_keys_agent_registry_id", ["agent_registry_id"], unique=False,
        )

    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(sa.Column("agent_registry_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("assignment_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_runs_agent_registry_id", "agents", ["agent_registry_id"], ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_agent_runs_assignment_id", "task_assignments", ["assignment_id"], ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_agent_runs_agent_registry_id", ["agent_registry_id"], unique=False,
        )
        batch_op.create_index("ix_agent_runs_assignment_id", ["assignment_id"], unique=False)

    with op.batch_alter_table("task_outcome") as batch_op:
        batch_op.add_column(sa.Column("agent_registry_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("assignment_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("agent_ref", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_task_outcome_agent_registry_id", "agents", ["agent_registry_id"], ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_task_outcome_assignment_id", "task_assignments", ["assignment_id"], ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_task_outcome_agent_registry_id", ["agent_registry_id"], unique=False,
        )
        batch_op.create_index("ix_task_outcome_assignment_id", ["assignment_id"], unique=False)
        batch_op.create_index("ix_task_outcome_agent_ref", ["agent_ref"], unique=False)

    with op.batch_alter_table("agents") as batch_op:
        batch_op.alter_column(
            "capabilities", existing_type=sa.String(length=500), type_=sa.Text(),
            existing_nullable=False, existing_server_default="[]",
        )

    # Exact run snapshots can be mapped safely.
    op.execute(sa.text("""
        UPDATE agent_runs
        SET agent_registry_id = (
            SELECT agents.id FROM agents WHERE agents.agent_id = agent_runs.agent
        )
        WHERE agent_registry_id IS NULL
          AND agent IS NOT NULL
          AND (SELECT COUNT(*) FROM agents WHERE agents.agent_id = agent_runs.agent) = 1
    """))

    # User attribution is only safe when exactly one registered Agent owns it.
    op.execute(sa.text("""
        UPDATE task_outcome
        SET agent_registry_id = (
                SELECT MIN(agents.id) FROM agents WHERE agents.user_id = task_outcome.agent_id
            ),
            agent_ref = (
                SELECT MIN(agents.agent_id) FROM agents WHERE agents.user_id = task_outcome.agent_id
            )
        WHERE agent_id IS NOT NULL
          AND (SELECT COUNT(*) FROM agents WHERE agents.user_id = task_outcome.agent_id) = 1
    """))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.alter_column(
            "capabilities", existing_type=sa.Text(), type_=sa.String(length=500),
            existing_nullable=False, existing_server_default="[]",
        )

    with op.batch_alter_table("task_outcome") as batch_op:
        batch_op.drop_index("ix_task_outcome_agent_ref")
        batch_op.drop_index("ix_task_outcome_assignment_id")
        batch_op.drop_index("ix_task_outcome_agent_registry_id")
        batch_op.drop_constraint("fk_task_outcome_assignment_id", type_="foreignkey")
        batch_op.drop_constraint("fk_task_outcome_agent_registry_id", type_="foreignkey")
        batch_op.drop_column("agent_ref")
        batch_op.drop_column("assignment_id")
        batch_op.drop_column("agent_registry_id")

    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_index("ix_agent_runs_assignment_id")
        batch_op.drop_index("ix_agent_runs_agent_registry_id")
        batch_op.drop_constraint("fk_agent_runs_assignment_id", type_="foreignkey")
        batch_op.drop_constraint("fk_agent_runs_agent_registry_id", type_="foreignkey")
        batch_op.drop_column("assignment_id")
        batch_op.drop_column("agent_registry_id")

    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_index("ix_api_keys_agent_registry_id")
        batch_op.drop_constraint("fk_api_keys_agent_registry_id", type_="foreignkey")
        batch_op.drop_column("agent_registry_id")

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_index("ix_tasks_current_assignment_id")
        batch_op.drop_constraint("ck_tasks_assignment_mode", type_="check")
        batch_op.drop_constraint("ck_tasks_complexity", type_="check")
        batch_op.drop_constraint("fk_tasks_current_assignment_id", type_="foreignkey")
        batch_op.drop_column("current_assignment_id")
        batch_op.drop_column("assignment_mode")
        batch_op.drop_column("domain_tags")
        batch_op.drop_column("complexity")
        batch_op.drop_column("needed_capabilities")

    op.drop_index("ix_task_applications_user_id", table_name="task_applications")
    op.drop_index("ix_task_applications_agent_registry_id", table_name="task_applications")
    op.drop_index("ix_task_applications_task_id", table_name="task_applications")
    op.drop_table("task_applications")
    op.drop_index("ix_task_assignments_user_id", table_name="task_assignments")
    op.drop_index("ix_task_assignments_agent_registry_id", table_name="task_assignments")
    op.drop_index("ix_task_assignments_task_id", table_name="task_assignments")
    op.drop_table("task_assignments")
