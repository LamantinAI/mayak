#!/bin/sh

set -e

# Container startup mode is controlled strictly via APP_STARTUP_MODE (environment only):
#   migrate-and-serve (default when unset): validate runtime settings -> migrate (when POSTGRES_ENABLED=true) -> start server
#   migrate:                                validate runtime settings -> migrate (or no-op when POSTGRES_ENABLED=false) -> exit 0
#   serve:                                  validate runtime settings -> start server without running migrations
# Positional arguments and unknown or empty mode values are rejected before touching logs, settings
# or the database, so an operator passing `serve` on the command line or expanding an empty deploy
# variable never triggers default startup migrations. Images from v1.3.2 and earlier ignore
# APP_STARTUP_MODE; adopt this entrypoint in release N-1 before shipping expand migrations in N.
if [ "$#" -gt 0 ]; then
    echo "ERROR: entrypoint.sh accepts no positional arguments (got $#); set APP_STARTUP_MODE=migrate-and-serve|migrate|serve instead." >&2
    exit 2
fi

STARTUP_MODE="${APP_STARTUP_MODE-migrate-and-serve}"
case "$STARTUP_MODE" in
    migrate-and-serve|migrate|serve)
        ;;
    *)
        echo "ERROR: invalid APP_STARTUP_MODE '$STARTUP_MODE'. Expected one of: migrate-and-serve, migrate, serve." >&2
        exit 2
        ;;
esac

# Pre-flight: ensure the logs directory exists and is writable.
# APP_LOG_DIR is the real setting (ProjectSettings.log_dir, prefix APP_). This line used to read
# LOG_DIR, a name nothing sets, and the bug hid behind matching defaults: APP_LOG_DIR defaults to
# `logs` under WORKDIR /app, which is the same path. A project that points its logs elsewhere
# would have had this check probe /app/logs forever.
LOGS_DIR="${APP_LOG_DIR:-/app/logs}"
mkdir -p "$LOGS_DIR" 2>/dev/null || true
if [ ! -w "$LOGS_DIR" ]; then
    echo "WARNING: logs directory '$LOGS_DIR' is not writable; file logging will be disabled."
fi

# The check the application runs at startup, run before anything touches the database. It used to
# come after `alembic upgrade head`, so a container refused for its settings — a wildcard CORS
# origin, a placeholder password — had already migrated the database it shares (round-4 finding
# R7). Settings only: no application is built.
echo "Checking the configuration (APP_STARTUP_MODE=$STARTUP_MODE)..."
if ! postgres_enabled=$(python -c "from project.core.config import get_settings; settings = get_settings(); settings.validate_runtime(); print(str(settings.postgres.enabled).lower())"); then
    echo "ERROR: the configuration was refused (the reason is above); nothing was migrated."
    exit 1
fi

# `serve` skips Alembic so schema DDL can run as a separate step under its own credentials while
# `/health/ready` still rejects an unmigrated or behind schema. A project that declared
# POSTGRES_ENABLED=false has no relational store, so `migrate` and `migrate-and-serve` no-op cleanly.
if [ "$STARTUP_MODE" = "serve" ]; then
    echo "APP_STARTUP_MODE=serve — skipping startup database migrations."
elif [ "$postgres_enabled" = "false" ]; then
    echo "POSTGRES_ENABLED=false — skipping database migrations."
else
    echo "Running database migrations..."
    if ! alembic upgrade head; then
        echo "ERROR: Database migration failed. Check connection settings and migration files."
        exit 1
    fi
    echo "Database migrations completed successfully."
fi

if [ "$STARTUP_MODE" = "migrate" ]; then
    echo "APP_STARTUP_MODE=migrate — completed; exiting without starting the application."
    exit 0
fi

echo "Starting the application..."
exec python -m project.launcher.main
