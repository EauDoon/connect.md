from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import get_settings
from app.models import Base
from app.services.database_roles import (
    MIGRATOR_DATABASE_ROLE,
    require_database_role_sync,
)

config = context.config
settings = get_settings()
settings.require_database_role_configuration(MIGRATOR_DATABASE_ROLE)
database_url = settings.database_url
config.set_main_option("sqlalchemy.url", database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _prepare_postgresql_version_table(connection: Connection) -> None:
    """Make the Alembic marker wide enough for the published revisions."""
    if connection.dialect.name != "postgresql":
        return

    # Alembic's default version table uses VARCHAR(32), while this project's
    # immutable revision IDs are longer. The first statement handles a fresh
    # database and the second widens a table created by an older environment.
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.alembic_version (
                version_num VARCHAR(255) NOT NULL,
                CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
            )
            """
        )
    )
    connection.execute(
        text(
            "ALTER TABLE IF EXISTS public.alembic_version "
            "ALTER COLUMN version_num TYPE VARCHAR(255)"
        )
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        require_database_role_sync(connection, MIGRATOR_DATABASE_ROLE, require_schema_owner=True)
        if connection.dialect.name == "postgresql":
            connection.execute(text("SET LOCAL search_path TO public"))
        if not context.get_context().opts.get("dont_mutate", False):
            _prepare_postgresql_version_table(connection)
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
