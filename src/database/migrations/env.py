import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import every module defining a model so Base.metadata is complete.
# Each new feature adds its import here.
import src.parameters.persistence.models  # noqa: F401
from src.config.settings import get_settings
from src.database.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# The URL is passed straight to the engine, NEVER through
# config.set_main_option(). That writes into ConfigParser, which performs
# `%` interpolation — so a Supabase password containing a percent-encoded
# character (`%40` for @, `%2F` for /) raises
# `ValueError: invalid interpolation syntax`. Local creds like `pikol:pikol`
# never trigger it, so this would pass in dev and in CI and then break the
# first production migration — the exact laptop-against-.env.supabase.prod
# flow spec §9.4 prescribes.
DATABASE_URL = get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        url=DATABASE_URL,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
