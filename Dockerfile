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
COPY --from=builder --chown=appuser:appuser /app .

ENV PATH="/home/appuser/.local/bin:/app/.venv/bin:$PATH"

# Create logs/ dir AFTER COPY so it is not overwritten by the build context.
# Must be owned by appuser since the app runs as non-root.
RUN mkdir -p /app/logs \
    && chown appuser:appuser /app/logs \
    && chmod +x entrypoint.sh


# ============ Functional test stage ============
FROM runtime-base AS functional-tests

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
