"""Allow 'superseded' status on task_assignments for admin force-clear.

Revision ID: b3c4d5e6f7g8
Revises: c4d5e6f7g8h9
Create Date: 2026-09-07

2026-09-07 (#1716): admin force-clear of a task's
``current_assignment_id`` FK (see
``service.admin_clear_task_assignment``) needs to mark the underlying
``TaskAssignment`` row in a way that is audit-distinct from the
existing values:

- ``active``   — assignment in flight
- ``completed`` — agent finished normally
- ``released``  — finalizer ran (e.g. set_status done/blocked) and
                 released the active slot
- ``cancelled`` — operator cancelled before completion

We add ``superseded`` for the new case where an admin explicitly
overrides the FK so a stuck task can be re-routed. Without a new value
the audit trail cannot tell a normal operator cancel from an admin
un-stick, which matters when reconstructing "why did the assignment end
without the task moving to a terminal state".
"""
from alembic import op


revision = "b3c4d5e6f7g8"
down_revision = "c4d5e6f7g8h9"


def upgrade() -> None:
    with op.batch_alter_table("task_assignments") as batch_op:
        batch_op.drop_constraint("ck_task_assignment_status", type_="check")
        batch_op.create_check_constraint(
            "ck_task_assignment_status",
            "status IN ('active','completed','released','cancelled','superseded')",
        )


def downgrade() -> None:
    with op.batch_alter_table("task_assignments") as batch_op:
        batch_op.drop_constraint("ck_task_assignment_status", type_="check")
        batch_op.create_check_constraint(
            "ck_task_assignment_status",
            "status IN ('active','completed','released','cancelled')",
        )
