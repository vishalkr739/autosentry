# agent/tests/integration/test_migrations.py
# Requires a running Postgres reachable at $TEST_DATABASE_URL
# (started as an ephemeral container in CI; see Task 11).
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


@pytest.fixture
def database_url() -> str:
    return os.environ["TEST_DATABASE_URL"]


def test_migrations_apply_cleanly_to_fresh_database(database_url):
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert tables == {
        "tenants",
        "schema_mapping_versions",
        "policy_config",
        "investigation_history",
        "alembic_version",
    }

    for table in ("schema_mapping_versions", "policy_config", "investigation_history"):
        columns = {c["name"]: c for c in inspector.get_columns(table)}
        assert columns["tenant_id"]["nullable"] is False
        index_columns = {
            col
            for idx in inspector.get_indexes(table)
            for col in idx["column_names"]
        }
        assert "tenant_id" in index_columns


def test_migrated_columns_match_the_models(database_url):
    """The models and the migrations must describe the same tables: every
    model column exists with the same nullability, and timestamps agree on
    being timezone-aware, so a model change without its migration fails."""
    from sqlalchemy import DateTime

    from autosentry_agent.db.models import Base

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    inspector = inspect(create_engine(database_url))

    for table in Base.metadata.sorted_tables:
        migrated = {c["name"]: c for c in inspector.get_columns(table.name)}
        for column in table.columns:
            assert column.name in migrated, f"{table.name}.{column.name} has no migration"
            db_column = migrated[column.name]
            assert db_column["nullable"] == column.nullable, f"{table.name}.{column.name} nullability"
            if isinstance(column.type, DateTime):
                assert getattr(db_column["type"], "timezone", False) == column.type.timezone, (
                    f"{table.name}.{column.name}: model timezone={column.type.timezone}, database differs"
                )


def test_timezone_migration_keeps_existing_values_as_utc(database_url):
    """A naive value written before 0002 reads back as the same instant in UTC after it."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0001")
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO tenants (tenant_id, institution_name, workspace_graph_name, enablement_state, created_at) "
            "VALUES ('t1', 'Bank', 'G', 'disabled', '2026-10-05 12:00:00')"
        ))
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        utc = conn.execute(text(
            "SELECT to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') FROM tenants WHERE tenant_id = 't1'"
        )).scalar_one()
    assert utc == "2026-10-05 12:00:00"
    command.downgrade(cfg, "base")
