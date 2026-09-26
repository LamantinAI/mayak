# FILE: alembic/env.py
# SUMMARY: Alembic migration environment configuration for PostgreSQL schema management.
# ruff: noqa: E402

import logging
import time
import zlib
from logging.config import fileConfig
import os
import sys
from sqlalchemy import engine_from_config, pool, text
from alembic import context
from dotenv import load_dotenv

# Add project root to sys.path for model access
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

# Load environment variables from .env file
load_dotenv(os.path.join(project_root, ".env"))

from project.core.config import PostgresSettings
from project.infrastructure.persistence.orm_models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# Construct the database URL through the shared runtime settings model.
postgres_settings = PostgresSettings()

# ConfigParser treats URL escapes such as %40 as interpolation markers.
config.set_main_option(
    "sqlalchemy.url", str(postgres_settings.sqlalchemy_database_url).replace("%", "%%")
)

_migration_logger = logging.getLogger("alembic.migration")

# Advisory-lock key every migrating process agrees on, so two containers started together against
# an unmigrated database serialise instead of both creating alembic_version at once. A fixed string
# through crc32 keeps the key stable across processes and PYTHONHASHSEED, unlike hash(). A project
# sharing this database with another service that also uses advisory locks changes this string,
# not the mechanism.
_MIGRATION_LOCK_KEY = zlib.crc32(b"mayak.alembic.migrations")


def run_migrations_offline() -> None:
    _migration_logger.info("Starting offline migrations")
    _start = time.perf_counter()

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_num_width=128,
    )

    with context.begin_transaction():
        context.run_migrations()

    _elapsed_ms = round((time.perf_counter() - _start) * 1000, 1)
    _migration_logger.info("Offline migrations complete in %.1f ms", _elapsed_ms)


def run_migrations_online() -> None:
    _migration_logger.info("Starting online migrations")
    _start = time.perf_counter()

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_num_width=128,
        )

        with context.begin_transaction():
            # xact lock releases itself on commit/rollback, so a killed container cannot leave it
            # held — session-level pg_advisory_lock would instead need a matching unlock on every
            # exit path. Taken before run_migrations() because that first statement creates
            # alembic_version, which is the collision this serialises.
            _lock_requested_at = time.perf_counter()
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIGRATION_LOCK_KEY}
            )
            # Logged on its own line so lock queueing time is not mistaken for DDL time.
            _waited_ms = round((time.perf_counter() - _lock_requested_at) * 1000, 1)
            if _waited_ms >= 1.0:
                _migration_logger.info(
                    "Waited %.1f ms for the migration advisory lock held by another process",
                    _waited_ms,
                )
            context.run_migrations()

    _elapsed_ms = round((time.perf_counter() - _start) * 1000, 1)
    _migration_logger.info("Online migrations complete in %.1f ms", _elapsed_ms)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
