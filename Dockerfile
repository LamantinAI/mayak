# ============ Build stage ============
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock* ./

# Dependencies only, WITHOUT the project itself
RUN uv sync --frozen --no-install-project --no-dev

COPY . .

# ============ Runtime base stage ============
FROM python:3.13-slim-bookworm AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/appuser

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
    && groupadd -r appuser \
    && useradd -r -g appuser -d /home/appuser -m -s /sbin/nologin appuser \
    && chown -R appuser:appuser /home/appuser

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

# 2026-08-24: this used to be one `COPY --from=builder /app .`, which pulled the builder's whole
# checkout — tests/, scripts/, ai_context/, ai_query/, docs/, .agents/, .claude/, README.md, the
# lot — into the image that ships to production. Dev dependencies were never installed
# (`uv sync --frozen --no-install-project --no-dev` above), so this was dead weight and surface
# area, not a live vulnerability, but a production image has no business carrying a validator
# suite it never runs. Replaced with an explicit allowlist: only what `project.launcher.main` and
# `alembic upgrade head` actually touch at runtime, verified 2026-08-24 by
# `grep -rn 'from scripts\|import scripts\|from ai_context\|from ai_query' project/ alembic/
# entrypoint.sh` (no hits) and by reading alembic/env.py (stdlib, sqlalchemy, alembic, dotenv,
# project.* only).
#
# Two entries here look like development files and are not — dropping either breaks the app on
# startup, not at build time, so the failure shows up far from this line:
#   - .env.sample: project/core/config_runtime.py reads it at import time to collect the
#     placeholder credentials it refuses to boot on. No file, no guard.
#   - pyproject.toml: project/core/config_runtime.py reads APP_VERSION from it via tomllib,
#     deliberately not importlib.metadata, because `uv sync --no-install-project` above means the
#     project is never installed as a distribution in this image — metadata lookup would raise
#     PackageNotFoundError here while working fine on a dev machine. Removing this file does not
#     error either; it silently falls back to the "0.0.0+unknown" sentinel.
# Do not fold either back into a wildcard exclusion without re-reading
# project/core/config_runtime.py first.
COPY --from=builder --chown=appuser:appuser /app/project /app/project
COPY --from=builder --chown=appuser:appuser /app/alembic /app/alembic
COPY --from=builder --chown=appuser:appuser /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=appuser:appuser /app/entrypoint.sh /app/entrypoint.sh
COPY --from=builder --chown=appuser:appuser /app/pyproject.toml /app/pyproject.toml
COPY --from=builder --chown=appuser:appuser /app/.env.sample /app/.env.sample

ENV PATH="/home/appuser/.local/bin:/app/.venv/bin:$PATH"

# Create logs/ dir AFTER COPY so it is not overwritten by the build context.
# Must be owned by appuser since the app runs as non-root.
RUN mkdir -p /app/logs \
    && chown appuser:appuser /app/logs \
    && chmod +x entrypoint.sh


# ============ Functional test stage ============
FROM runtime-base AS functional-tests

# runtime-base no longer carries tests/ (see the copy list above), so this stage pulls it back in
# from the builder, on top of the runtime set it already inherited. This is the one place tests/
# is allowed back into an image — DO NOT "fix" the trimmed copy above by adding tests/ to
# .dockerignore instead: .dockerignore filters the build context before any stage sees it, so
# that would take tests/ away from this COPY too and break `make test-e2e` and the CI
# functional-tests job (tests/functional/docker-compose.yml builds this exact stage).
COPY --from=builder --chown=appuser:appuser /app/tests /app/tests

# 2026-08-24: added after `make test-e2e` failed collection with `ModuleNotFoundError: No module
# named 'scripts'`. tests/functional/src/test_migrations_match_models.py imports
# scripts.validate_migrations (the ADR-006 migration gate, run here with a real database because
# it is the one place that has one), which imports ai_context.rendering and
# ai_context.validator_contract — both stdlib-only, nothing further to chase. Same reasoning as
# the tests/ COPY above: both were in .dockerignore, which cut them from the build context
# entirely, so there was nothing in `builder` for a selective COPY to pull from until they were
# removed from that list. ai_query/ has no reader in tests/ (checked, see .dockerignore) and stays
# excluded — do not copy it "to be safe".
COPY --from=builder --chown=appuser:appuser /app/scripts /app/scripts
COPY --from=builder --chown=appuser:appuser /app/ai_context /app/ai_context

RUN /app/.venv/bin/python -m ensurepip \
    && /app/.venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir -r /app/tests/functional/requirements.txt

USER appuser

ENTRYPOINT ["/bin/sh"]


# ============ Runtime app stage ============
FROM runtime-base AS app

USER appuser

# Curl is installed in runtime-base; /health/ is mounted by the kernel
# router_registration.include_application_routers and returns 200 once
# the FastAPI app is up. Compose-level healthcheck mirrors this command.
# The shell form expands SERVER_PORT at container runtime, so a project that moves the listener
# does not have to remember to edit the probe as well.
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${SERVER_PORT:-8000}/health/" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
