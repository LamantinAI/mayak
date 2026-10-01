# FILE: project/core/config_builders.py
# SUMMARY: Shared DSN builder helpers reused by runtime settings and database tooling.

from ipaddress import AddressValueError, IPv6Address
from urllib.parse import quote

from pydantic import PostgresDsn


def _uri_host(host: str) -> str:
    # Wrap a single raw IPv6 address, leaving bracketed or multi-host authorities unchanged.
    try:
        IPv6Address(host)
    except AddressValueError:
        return host
    return f"[{host}]"


def build_postgres_dsn(
    user: str,
    password: str,
    host: str,
    port: int,
    database: str,
) -> PostgresDsn:
    # URI userinfo uses percent-encoding: form-style '+' would change a literal space.
    encoded_user = quote(user, safe="")
    encoded_password = quote(password, safe="")
    encoded_database = quote(database, safe="")
    return PostgresDsn(
        f"postgresql://{encoded_user}:{encoded_password}@{_uri_host(host)}:{port}/{encoded_database}"
    )


def build_sqlalchemy_postgres_dsn(
    user: str,
    password: str,
    host: str,
    port: int,
    database: str,
) -> PostgresDsn:
    encoded_user = quote(user, safe="")
    encoded_password = quote(password, safe="")
    encoded_database = quote(database, safe="")
    # SQLAlchemy does not unquote a database path; decoded query options reach psycopg instead.
    return PostgresDsn(
        f"postgresql+psycopg://{encoded_user}:{encoded_password}@{_uri_host(host)}:{port}/"
        f"?dbname={encoded_database}"
    )
