"""Upgrade/downgrade preserves preexisting work; no production database access."""
import importlib.util
from pathlib import Path
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_discussion_migration_preserves_existing_work(tmp_path):
    path = Path(__file__).resolve().parents[2] / 'src/backend-fastapi/migrations/versions/a19d58e204bc_worker_discussions.py'
    spec = importlib.util.spec_from_file_location('discussion_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f'sqlite:///{tmp_path / "migration.db"}')
    with engine.begin() as connection:
        for table in ('projects', 'tasks'):
            connection.exec_driver_sql(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)')
        connection.exec_driver_sql('CREATE TABLE worker_work (id INTEGER PRIMARY KEY, result TEXT)')
        connection.exec_driver_sql("INSERT INTO worker_work VALUES (1, 'retained evidence')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.exec_driver_sql('SELECT result, discussion_id, target_agent FROM worker_work').one() == ('retained evidence', None, None)
        assert 'worker_discussions' in sa.inspect(connection).get_table_names()
        assert 'subject' in {c['name'] for c in sa.inspect(connection).get_columns('worker_discussions')}
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.exec_driver_sql('SELECT result FROM worker_work').scalar() == 'retained evidence'
        assert 'worker_discussions' not in sa.inspect(connection).get_table_names()
    engine.dispose()
