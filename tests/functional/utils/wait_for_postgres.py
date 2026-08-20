# FILE: tests/functional/utils/wait_for_postgres.py
# SUMMARY: Utility to wait for PostgreSQL to become ready.
# NOTE: Relaxed functional-test helper module. Do not copy its patterns into project/** production code.

import backoff
import psycopg

from utils.helpers import PostgresSettings


# FUNCTION: is_postgres_ready
# SUMMARY: Checks if PostgreSQL is ready to accept connections.
# INPUT: settings (PostgresSettings): Settings object containing database connection info.
# OUTPUT: (bool): True if PostgreSQL is ready, False otherwise.
def is_postgres_ready(settings: PostgresSettings) -> bool:
    # **LOGIC_STEP**: Attempt to connect and execute a simple query. The password is a
    # pydantic SecretStr, whose str() is a row of asterisks — passing it straight to psycopg
    # made every connection fail authentication until the suite gained its first real test.
    try:
        with psycopg.connect(
            host=settings.host,
            port=settings.port,
            user=settings.user,
            password=settings.password.get_secret_value(),
            dbname=settings.db,
            connect_timeout=5,
        ) as conn:
            # **LOGIC_STEP**: Execute 'SELECT 1' to verify connection.
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception as e:
        # **LOGIC_STEP**: Log failure and return False.
        print(f"PostgreSQL not ready yet: {e}")
        return False


# FUNCTION: wait_for_postgres
# SUMMARY: Waits for PostgreSQL readiness with exponential backoff.
# INPUT: settings (PostgresSettings): Settings object containing database connection info.
# RAISES: Exception: If PostgreSQL connection fails after retries.
@backoff.on_exception(
    backoff.expo,
    Exception,
    max_time=60,
    max_tries=20,
    jitter=None,
)
def wait_for_postgres(settings: PostgresSettings) -> None:
    # **LOGIC_STEP**: Check if PostgreSQL is ready.
    if not is_postgres_ready(settings):
        # **LOGIC_STEP**: Raise exception to trigger backoff retry.
        raise Exception("PostgreSQL connection failed")


if __name__ == "__main__":
    # **LOGIC_STEP**: `utils.helpers`, not `helpers` — see the note in wait_for_api.py.
    postgres_settings = PostgresSettings()
    print(f"Waiting for PostgreSQL at {postgres_settings.host}:{postgres_settings.port}...")
    wait_for_postgres(postgres_settings)
    print("PostgreSQL is ready!")
