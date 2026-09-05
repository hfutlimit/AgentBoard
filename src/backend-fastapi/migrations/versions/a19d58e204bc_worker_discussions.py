"""Worker-owned review discussion and identity-targeted replies."""
from alembic import op
import sqlalchemy as sa

revision = "a19d58e204bc"
down_revision = "f72b8c06d941"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("worker_discussions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("source_work_id", sa.Integer(), nullable=False),
        sa.Column("review_kind", sa.String(24), nullable=False),
        sa.Column("subject", sa.String(24), nullable=False),
        sa.Column("review_round", sa.Integer(), nullable=False),
        sa.Column("owner_agent", sa.String(100), nullable=False),
        sa.Column("reviewer_agent", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("active_slot", sa.String(10), nullable=True),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("max_rounds", sa.Integer(), nullable=False),
        sa.Column("messages", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("task_id", "active_slot", name="uq_worker_discussion_active_task"))
    op.create_index("ix_worker_discussions_project_id", "worker_discussions", ["project_id"])
    op.create_index("ix_worker_discussions_task_id", "worker_discussions", ["task_id"])
    with op.batch_alter_table("worker_work") as batch:
        batch.add_column(sa.Column("discussion_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("target_agent", sa.String(100), nullable=True))
        batch.create_foreign_key("fk_worker_work_discussion", "worker_discussions", ["discussion_id"], ["id"])


def downgrade():
    with op.batch_alter_table("worker_work") as batch:
        batch.drop_constraint("fk_worker_work_discussion", type_="foreignkey")
        batch.drop_column("target_agent")
        batch.drop_column("discussion_id")
    op.drop_table("worker_discussions")
