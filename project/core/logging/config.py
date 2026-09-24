# FILE: project/core/logging/config.py
# SUMMARY: Logging configuration compatible with Uvicorn and standard logging dictConfig.

import logging
import logging.config
import os
import sys
from typing import Any


# ==================== CONFIGURATION ====================


def get_logging_config(
    level: str = "INFO",
    log_file_path: str | None = None,
) -> dict[str, Any]:
    # NDJSON is not a mode this template can be asked to leave. Everything that
    # reads these logs — the trace formatter, `make logs`, the agent's own log queries — parses
    # one JSON object per line, so a plain-text branch would produce output nothing here can read.
    formatter_cls = "project.core.logging.formatters.NDJSONFormatter"

    # Build handler list — always console, optionally file.
    handlers: dict[str, Any] = {
        "console": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "filters": ["caller_override"],
        },
    }
    active_handlers = ["console"]

    if log_file_path:
        handlers["file"] = {
            "formatter": "default",
            "class": "logging.FileHandler",
            "filename": log_file_path,
            "encoding": "utf-8",
            "filters": ["caller_override"],
        }
        active_handlers.append("file")

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "caller_override": {
                "()": "project.core.logging.logger_types.CallerOverrideFilter",
            },
        },
        "formatters": {
            "default": {
                "()": formatter_cls,
            },
        },
        "handlers": handlers,
        "loggers": {
            # Our application logger
            "project": {
                "handlers": active_handlers,
                "level": level,
                "propagate": False,
            },
            # Intercept Uvicorn root logger
            "uvicorn": {
                "handlers": active_handlers,
                "level": "INFO",
                "propagate": False,
            },
            # Server errors - also in JSON
            "uvicorn.error": {
                "level": "INFO",
                "handlers": active_handlers,
                "propagate": False,
            },
            # Uvicorn's access log is silenced at ERROR because the semantic
            # middleware emits one request.summary per request, with the same facts plus trace_id,
            # span counts and token totals. Leaving both on doubles every request line and gives
            # the agent two records to reconcile.
            # This sentence was false for a long time and nobody noticed, because nothing checked
            # it: the request span was not a trace root, so no summary was emitted for any request
            # and silencing the access log left production with no per-request line at all. It is
            # true again, and now guarded — see
            # tests/application/test_request_summary_outcome.py::TestTheRequestSpanBeginsItsOwnTrace.
            "uvicorn.access": {
                "handlers": active_handlers,
                "level": "ERROR",
                "propagate": False,
            },
        },
        "root": {
            "handlers": active_handlers,
            "level": level,
        },
    }


# level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
# Returns the logging configuration dict (useful for passing to uvicorn).
def setup_logging(
    *,
    level: str | None = None,
    force: bool = False,
    log_file_path: str | None = None,
) -> dict:
    # Determine log level from parameter or environment.
    level_name: str = level or os.getenv("LOG_LEVEL", "DEBUG") or "DEBUG"
    # Get logging configuration dict.
    log_config = get_logging_config(level=level_name, log_file_path=log_file_path)

    # Apply configuration using dictConfig.
    if force:
        # Clear existing handlers before applying new config
        root_logger = logging.getLogger()
        root_logger.handlers.clear()

    logging.config.dictConfig(log_config)

    # Enable warning capture.
    logging.captureWarnings(True)

    # Filter noisy libraries.
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("grpc._cython.cygrpc").setLevel(logging.WARNING)
    logging.getLogger("filelock").setLevel(logging.WARNING)

    # Log configuration event.
    try:
        from project.core.logging.logger import get_logger

        logger = get_logger(__name__)
        logger.log_system_event(
            event_name="logging_configured",
            category="configuration",
            new_value={
                "mode": "ndjson",
                "level": level_name.upper(),
            },
        )
    except Exception as error:
        # A logging failure must not stop startup, but it must not be invisible
        # either — this is the one place where the semantic logger itself may be unusable, so the
        # note goes straight to stderr.
        sys.stderr.write(f"[logging] configuration notice suppressed: {error!r}\n")

    return log_config
