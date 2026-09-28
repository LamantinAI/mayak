#!/bin/sh

set -e

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
echo "Checking the configuration..."
if ! python -c "from project.core.config import get_settings; get_settings().validate_runtime()"; then
    echo "ERROR: the configuration was refused (the reason is above); nothing was migrated."
    exit 1
fi

# A project that declared POSTGRES_ENABLED=false has no relational store, so there is nothing
# to migrate. Without this branch the container exits 1 before the application is ever reached.
if [ "${POSTGRES_ENABLED:-true}" = "false" ]; then
    echo "POSTGRES_ENABLED=false — skipping database migrations."
else
    echo "Running database migrations..."
    if ! alembic upgrade head; then
        echo "ERROR: Database migration failed. Check connection settings and migration files."
        exit 1
    fi
    echo "Database migrations completed successfully."
fi

echo "Starting the application..."
exec python -m project.launcher.main
