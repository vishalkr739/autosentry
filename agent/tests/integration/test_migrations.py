# agent/tests/integration/test_migrations.py
# Requires a running Postgres reachable at $TEST_DATABASE_URL
# (started as an ephemeral container in CI; see Task 11).
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


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
