# FILE: project/core/config_builders.py
# SUMMARY: Shared DSN builder helpers reused by runtime settings and database tooling.

from urllib.parse import quote_plus

from pydantic import PostgresDsn


# SUMMARY: Build a PostgreSQL DSN with safe credential escaping for libpq-compatible runtime contexts.
def build_postgres_dsn(
    user: str,
    password: str,
    host: str,
    port: int,
    database: str,
) -> PostgresDsn:
    # URL-encode user and password to handle special characters safely.
    encoded_user = quote_plus(user)
    encoded_password = quote_plus(password)
    # Construct PostgreSQL connection URL from component parts.
    return PostgresDsn(f"postgresql://{encoded_user}:{encoded_password}@{host}:{port}/{database}")


# SUMMARY: Build a PostgreSQL DSN for SQLAlchemy using the psycopg v3 dialect.
def build_sqlalchemy_postgres_dsn(
    user: str,
    password: str,
    host: str,
    port: int,
    database: str,
) -> PostgresDsn:
    # Reuse safe credential escaping while selecting the psycopg v3 SQLAlchemy dialect.
    encoded_user = quote_plus(user)
    encoded_password = quote_plus(password)
    return PostgresDsn(
        f"postgresql+psycopg://{encoded_user}:{encoded_password}@{host}:{port}/{database}"
    )
