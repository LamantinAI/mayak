# FILE: tests/application/test_postgres_dsn_round_trip.py
# SUMMARY: DSN components must reach libpq and the SQLAlchemy driver unchanged, before any connection.

from typing import Any

import pytest
from alembic.config import Config
from psycopg.conninfo import conninfo_to_dict
from psycopg.adapt import AdaptersMap
from sqlalchemy import engine_from_config, event, pool
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
from sqlalchemy.engine import make_url

from project.core.config_builders import build_postgres_dsn, build_sqlalchemy_postgres_dsn


@pytest.mark.parametrize(
    "user,password,host,database",
    [
        ("svc_user", "plain", "db.internal", "app"),
        ("user", "word with space", "localhost", "app"),
        ("team user", "plain", "localhost", "app"),
        ("user", "plain", "localhost", "app?x=1"),
        ("user", "plain", "::1", "app"),
        ("user+literal", "p@ss:w/rd%+", "[::1]", "app?x=1&#literal+%"),
        ("юзер", "пароль с пробелом", "2001:db8::1", "база данных?x=1"),
        ("user", "plain", "127.0.0.1", "literal%3Fdb"),
        ("user", "plain", "localhost", "literal+db"),
        ("user", "plain", "localhost", "normal db"),
        ("user", "plain", "localhost", "."),
        ("user", "plain", "localhost", ".."),
    ],
)
class TestPostgresDsnRoundTrip:
    @pytest.mark.unit
    def test_libpq_receives_the_original_components(
        self, user: str, password: str, host: str, database: str
    ) -> None:
        dsn = build_postgres_dsn(user, password, host, 6543, database)

        assert conninfo_to_dict(str(dsn)) == {
            "user": user,
            "password": password,
            "host": host.removeprefix("[").removesuffix("]"),
            "port": "6543",
            "dbname": database,
        }

    @pytest.mark.unit
    def test_sqlalchemy_passes_the_original_components_to_psycopg(
        self, user: str, password: str, host: str, database: str
    ) -> None:
        dsn = build_sqlalchemy_postgres_dsn(user, password, host, 6543, database)
        args, kwargs = PGDialect_psycopg().create_connect_args(make_url(str(dsn)))

        assert args == []
        assert kwargs == {
            "user": user,
            "password": password,
            "host": host.removeprefix("[").removesuffix("]"),
            "port": 6543,
            "dbname": database,
        }


class _ConnectionCaptured(RuntimeError):
    pass


@pytest.mark.unit
@pytest.mark.parametrize(
    "host,decoded", [("[::1],localhost", "::1,localhost"), ("localhost,[::1]", "localhost,::1")]
)
def test_existing_multihost_authorities_are_not_wrapped_as_one_ipv6_literal(
    host: str, decoded: str
) -> None:
    dsn = build_postgres_dsn("user", "plain", host, 6543, "app")

    assert conninfo_to_dict(str(dsn)) == {
        "user": "user",
        "password": "plain",
        "dbname": "app",
        "host": decoded,
        "port": ",6543",
    }


@pytest.mark.unit
def test_alembic_configparser_and_engine_preserve_all_connection_components() -> None:
    dsn = build_sqlalchemy_postgres_dsn("team user", "p@ss word%+", "::1", 6543, "app?x=1 &more#%+")
    config = Config()
    config.set_main_option("sqlalchemy.url", str(dsn).replace("%", "%%"))
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    received: dict[str, Any] = {}

    def capture(_dialect: Any, _record: Any, _args: Any, kwargs: dict[str, Any]) -> None:
        received.update(kwargs)
        # Stop before DBAPI connect: this test needs the actual engine arguments, not a server.
        raise _ConnectionCaptured

    event.listen(engine, "do_connect", capture)
    try:
        with pytest.raises(_ConnectionCaptured):
            engine.connect()
    finally:
        engine.dispose()

    assert isinstance(received.pop("context"), AdaptersMap)
    assert received == {
        "user": "team user",
        "password": "p@ss word%+",
        "host": "::1",
        "port": 6543,
        "dbname": "app?x=1 &more#%+",
    }
