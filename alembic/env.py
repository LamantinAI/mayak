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

# Set the database URL on the main config object.
config.set_main_option("sqlalchemy.url", str(postgres_settings.sqlalchemy_database_url))

# ATTRIBUTE: _migration_logger (logging.Logger)
# SUMMARY: Standard library logger for Alembic migration timing and lifecycle events.
_migration_logger = logging.getLogger("alembic.migration")

# ATTRIBUTE: _MIGRATION_LOCK_KEY (int)
# SUMMARY: The advisory-lock key every process that migrates this database agrees on.
# NOTE: entrypoint.sh runs `alembic upgrade head` in EVERY container, so two replicas started
# together against a database that has never been migrated both try to create `alembic_version` at
# once. Measured on 2026-09-02, twice: two parallel `docker compose run --rm app` against a fresh
# volume left one container at exit 0 and the other dead with
# `psycopg.errors.UniqueViolation: duplicate key value violates unique constraint
# "pg_type_typname_nsp_index" DETAIL: Key (typname, typnamespace)=(alembic_version, 2200)`.
# PostgreSQL rolls the loser's DDL transaction back whole, so no data is damaged and a retry
# succeeds — but docker-compose.yml deliberately ships no restart policy, so under Compose that
# replica simply stays down, and under Kubernetes it costs a crash loop and a page.
#
# The key is derived from a fixed string rather than typed as a magic number, so it is stable
# across processes, Python versions and PYTHONHASHSEED (which is why zlib.crc32 and not hash()),
# and self-documenting about who owns the lock. crc32 returns an unsigned 32-bit value, which fits
# a PostgreSQL bigint with room to spare. A project that shares one database with another service
# also using advisory locks changes this string, not the mechanism.
_MIGRATION_LOCK_KEY = zlib.crc32(b"mayak.alembic.migrations")


# FUNCTION: run_migrations_offline
# SUMMARY: Run migrations in 'offline' mode using URL without Engine creation.
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


# FUNCTION: run_migrations_online
# SUMMARY: Run migrations in 'online' mode using Engine and connection.
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
            # **LOGIC_STEP**: Serialise every migrating process on one advisory lock before any
            # DDL runs. `pg_advisory_xact_lock` blocks until the lock is free and releases it when
            # this transaction commits or rolls back — nothing to unlock by hand, and a container
            # killed mid-migration cannot leave the lock held, because the session dies with it.
            # The runner-up therefore waits rather than colliding, then finds the schema already at
            # head and applies nothing. The xact variant is deliberate: the session-level
            # `pg_advisory_lock` would need a matching unlock on every exit path, which is exactly
            # the kind of bookkeeping a crash skips.
            #
            # Taken INSIDE begin_transaction() and before run_migrations(), because the collision
            # this prevents is the creation of `alembic_version` itself, which happens on the first
            # statement Alembic issues.
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIGRATION_LOCK_KEY}
            )
            context.run_migrations()

    _elapsed_ms = round((time.perf_counter() - _start) * 1000, 1)
    _migration_logger.info("Online migrations complete in %.1f ms", _elapsed_ms)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
