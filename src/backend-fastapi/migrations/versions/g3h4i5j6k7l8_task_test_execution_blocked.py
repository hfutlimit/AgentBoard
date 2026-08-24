"""add Test Execution task type & blocked task status

Extends the tasks table CHECK constraints so tasks may use the new
item type ``test_execution`` and the new ``blocked`` status. The ``blocked``
status is intentionally task-scoped (it is not added to the epics/stories
status constraints), matching the feature request.

SQLite (where the app enables PRAGMA foreign_keys=ON, which blocks
ALTER TABLE DROP CONSTRAINT for CHECK constraints and where named CHECK
constraints cannot be dropped reliably) is handled with a table
rebuild (copy-and-move) on a fresh foreign_keys=OFF connection.
MariaDB/other dialects use direct DROP/CREATE CONSTRAINT.

Revision ID: g3h4i5j6k7l8
Revises: f2a3b4c5d6e7
"""
import os
import sqlite3

from alembic import op

revision = "g3h4i5j6k7l8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None

_TYPE_CK = "type IN ('task','bug','test_execution')"
_STATUS_CK = (
    "status IN ('backlog','todo','in_progress','in_review','verifying','done','blocked')"
)
_PRIORITY_CK = "priority IN ('highest','high','medium','low','lowest')"
_TYPE_CK_OLD = "type IN ('task','bug')"
_STATUS_CK_OLD = "status IN ('backlog','todo','in_progress','in_review','verifying','done')"

_TASKS_DDL = (
    'CREATE TABLE tasks_new (\n'
    '  id INTEGER NOT NULL,\n'
    '  project_id INTEGER NOT NULL,\n'
    '  story_id INTEGER,\n'
    '  type VARCHAR(10) NOT NULL,\n'
    '  title VARCHAR(300) NOT NULL,\n'
    '  status VARCHAR(20) NOT NULL,\n'
    '  description TEXT NOT NULL,\n'
    '  spec TEXT NOT NULL,\n'
    '  source_spec_id INTEGER,\n'
    '  created_at DATETIME NOT NULL,\n'
    '  updated_at DATETIME NOT NULL,\n'
    '  priority VARCHAR(10) DEFAULT \'medium\' NOT NULL,\n'
    '  sprint_id INTEGER,\n'
    '  assignee_id INTEGER REFERENCES users(id),\n'
    '  due_date DATE,\n'
    '  labels TEXT DEFAULT \'[]\' NOT NULL,\n'
    '  estimate FLOAT,\n'
    '  PRIMARY KEY (id),\n'
    '  CONSTRAINT ck_tasks_type CHECK (__TYPE_CK__),\n'
    '  CONSTRAINT ck_tasks_priority CHECK (__PRIORITY_CK__),\n'
    '  CONSTRAINT ck_tasks_status CHECK (__STATUS_CK__),\n'
    '  CONSTRAINT fk_tasks_source_spec_id_tasks FOREIGN KEY(source_spec_id) REFERENCES tasks (id) ON DELETE SET NULL,\n'
    '  FOREIGN KEY(project_id) REFERENCES projects (id),\n'
    '  FOREIGN KEY(story_id) REFERENCES stories (id)\n'
    ')'
)

_TASKS_INDEXES = [
    'CREATE INDEX ix_tasks_sprint_id ON tasks (sprint_id)',
    'CREATE INDEX ix_tasks_project_id ON tasks (project_id)',
    'CREATE INDEX ix_tasks_story_id ON tasks (story_id)',
    'CREATE INDEX ix_tasks_source_spec_id ON tasks (source_spec_id)',
    'CREATE INDEX ix_tasks_project_status ON tasks (project_id, status)',
    'CREATE INDEX ix_tasks_project_priority ON tasks (project_id, priority)',
    'CREATE INDEX ix_tasks_status ON tasks (status)',
]


def _sqlite_rebuild(op, type_ck, status_ck) -> None:
    bind = op.get_bind()
    db_path = bind.engine.url.database
    if not db_path or db_path == ":memory:":
        raise RuntimeError("SQLite rebuild requires a file-backed database")
    if not os.path.isabs(db_path):
        db_path = os.path.join(os.getcwd(), db_path)
    ddl = _TASKS_DDL.replace("__TYPE_CK__", type_ck).replace(
        "__PRIORITY_CK__", _PRIORITY_CK
    ).replace("__STATUS_CK__", status_ck)
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute(ddl)
        try:
            con.execute("INSERT INTO tasks_new SELECT * FROM tasks")
            con.execute("DROP TABLE tasks")
            con.execute("ALTER TABLE tasks_new RENAME TO tasks")
            for idx in _TASKS_INDEXES:
                con.execute(idx)
        except Exception:
            # Roll back the partial rebuild so the original table is untouched.
            con.execute("DROP TABLE IF EXISTS tasks_new")
            con.commit()
            raise
        con.commit()
    finally:
        con.close()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _sqlite_rebuild(op, _TYPE_CK, _STATUS_CK)
    else:
        op.drop_constraint("ck_tasks_type", "tasks", type_="check")
        op.create_check_constraint("ck_tasks_type", "tasks", _TYPE_CK)
        op.drop_constraint("ck_tasks_status", "tasks", type_="check")
        op.create_check_constraint("ck_tasks_status", "tasks", _STATUS_CK)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _sqlite_rebuild(op, _TYPE_CK_OLD, _STATUS_CK_OLD)
    else:
        op.drop_constraint("ck_tasks_status", "tasks", type_="check")
        op.create_check_constraint("ck_tasks_status", "tasks", _STATUS_CK_OLD)
        op.drop_constraint("ck_tasks_type", "tasks", type_="check")
        op.create_check_constraint("ck_tasks_type", "tasks", _TYPE_CK_OLD)
