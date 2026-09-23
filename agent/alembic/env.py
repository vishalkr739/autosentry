from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from autosentry_agent.config import get_secrets_provider
from autosentry_agent.db.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Read the database URL from SecretsProvider (Task 3's abstraction) only
# when nobody has already set sqlalchemy.url explicitly, i.e. it is still
# alembic.ini's literal placeholder (see alembic.ini:89). A caller such as
# the integration test may call config.set_main_option("sqlalchemy.url",
# ...) before this module runs; that explicit value must win over
# DATABASE_URL, otherwise a developer with DATABASE_URL pointing at their
# dev database would have it silently migrated instead of the test's
# intended database.
_PLACEHOLDER_URL = "driver://user:pass@localhost/dbname"
_config_url = config.get_main_option("sqlalchemy.url")
if not _config_url or _config_url == _PLACEHOLDER_URL:
    config.set_main_option("sqlalchemy.url", get_secrets_provider().get_secret("DATABASE_URL"))

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
