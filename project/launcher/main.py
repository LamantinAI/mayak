# FILE: project/launcher/main.py
# SUMMARY: The main executable file for launching FastAPI application.

import os
import signal
import sys
from types import FrameType
from typing import Optional

import uvicorn
from fastapi import FastAPI
from pydantic import ValidationError

from project.core.composition_root import CompositionRoot
from project.core.config import get_settings
from project.core.lifecycle import create_lifespan
from project.core.logging import get_logger, setup_logging
from project.domain.exceptions import ProjectError

# SUMMARY: Signals a process manager sends to ask for shutdown, and which must not be fatal here.
# SIGINT is absent on purpose: its default already raises KeyboardInterrupt, which the branch below
# catches, so it never had this problem.
_TERMINATION_SIGNALS: tuple[int, ...] = (signal.SIGTERM,)

# SUMMARY: One-slot mailbox the handler writes into, read after uvicorn returns.
# A list rather than a module-level int so the handler needs no `global`.
_RECEIVED_SIGNALS: list[int] = []


# SUMMARY: Make a termination signal survivable so shutdown work after uvicorn.run still runs.
def _install_termination_handler() -> None:
    # SUMMARY: Record the signal and return, which is what keeps the process alive.
    def _remember(signum: int, _frame: Optional[FrameType]) -> None:
        _RECEIVED_SIGNALS.append(signum)

    for termination_signal in _TERMINATION_SIGNALS:
        signal.signal(termination_signal, _remember)


# SUMMARY: Hand SIGTERM back to the operating system once the shutdown work is done being protected.
# Without this the handler outlives its purpose. It is installed so the code after
# uvicorn.run — flushing handlers, rendering the trace summary into the log file — is not killed
# mid-write, but leaving it in place means every later SIGTERM is swallowed too, and a process
# stuck in that trailing phase answers only to SIGKILL. Reproduced: three SIGTERMs two seconds
# apart during the trailing phase, all recorded, process alive after each. An operator's second
# Ctrl-C-equivalent has to work, so the protection is scoped to the window that needs it.
def _restore_termination_default() -> None:
    for termination_signal in _TERMINATION_SIGNALS:
        signal.signal(termination_signal, signal.SIG_DFL)


# SUMMARY: Where uvicorn imports the application from — in this process with one worker, in each
# worker process with several.
# An import string, not the application object. With SERVER_WORKERS above one uvicorn starts
# separate processes that each import the application, and it refuses an object outright: the
# process ended at startup with exit code 3, so the setting was unusable. Found by the bench2
# measurement (2026-09-24), where one process had also hidden that an asyncio.Lock guarding a
# booking rule does not hold across processes — tests/application/test_launcher_workers.py.
APPLICATION_FACTORY = "project.launcher.main:create_app"


# SUMMARY: Build the FastAPI application with its lifespan; what every worker process calls.
# Logging is not configured here: uvicorn applies the log config main() passes it in every
# worker before calling this, and settings were validated once, in main(), before any worker exists.
# With ENABLE_FULL_TRACE and several workers that config gives each worker its own FileHandler on
# the one run file main() created; the trace summary is still rendered once, by this process, after
# the workers exit. Checked 2026-09-24 with two workers: one file, lines from all three processes,
# no line torn, summary at the top. Spans do not cross processes, so each worker's traces stand alone.
def create_app() -> FastAPI:
    settings = get_settings()
    lifespan = create_lifespan(settings, get_logger(__name__))
    return CompositionRoot().build_application(lifespan=lifespan)


# SUMMARY: The application's entry point. Initializes and starts FastAPI server.
# RAISES: ProjectError: In case of critical errors during startup.
def main() -> None:
    # Initialize application environment and logging.
    # We initialize logging in a safe bootstrap mode first, then reconfigure after validated settings load.
    log_config = setup_logging(level="INFO")

    logger = get_logger(__name__)

    log_file_path: str | None = None

    with logger.span("application_lifecycle") as span_ctx:
        logger.log_system_event(event_name="application_starting", category="lifecycle")

        try:
            # Validate configuration at startup.
            with logger.span("validate_configuration") as vc_span:
                try:
                    settings = get_settings()
                    settings.validate_runtime()

                    # Enable file-based logging when full-trace observability
                    #                 is requested; this is orthogonal to APP_DEBUG so that
                    #                 eval / pre-prod stands can collect full pipeline traces
                    #                 without flipping debug ergonomics (reload, verbose stdout).
                    if settings.observability.full_trace_enabled:
                        from project.core.logging.file_manager import (
                            create_run_log_path,
                            rotate_log_files,
                        )

                        log_file = create_run_log_path(settings.project.log_dir)
                        rotate_log_files(
                            settings.project.log_dir,
                            settings.project.log_max_files,
                        )
                        log_file_path = str(log_file)

                    # Pick log level — DEBUG when either debug ergonomics
                    #                 or full-trace is on, so DEBUG span events reach the file.
                    effective_level = (
                        "DEBUG"
                        if (settings.project.debug or settings.observability.full_trace_enabled)
                        else "INFO"
                    )
                    log_config = setup_logging(
                        level=effective_level,
                        force=True,
                        log_file_path=log_file_path,
                    )
                    vc_span.output = {
                        "debug": settings.project.debug,
                        "full_trace": settings.observability.full_trace_enabled,
                        "model": settings.llm.model,
                    }
                    if settings.observability.full_trace_enabled:
                        # Said out loud, once, where an operator reading the boot
                        # of a service will meet it. The flag records prompts and completions
                        # verbatim; the scrubber on that path removes credential shapes and knows
                        # nothing about a name, an address or an email a user typed. Nothing else
                        # announced this, so a project turned it on to debug an agent and left it
                        # on — which is how a customer's email address ended up in a log file.
                        logger.log_warning(
                            warning_type="full_trace_records_content",
                            message=(
                                "ENABLE_FULL_TRACE is on: prompts and completions are written to "
                                "the log verbatim, and only credential shapes are scrubbed. Turn "
                                "it off before this serves real users, or accept that their text "
                                "is on disk" + (f" in {log_file_path}." if log_file_path else ".")
                            ),
                            affected_component="observability",
                        )
                except ValidationError as e:
                    raise ProjectError(f"Configuration validation failed: {e}") from e
                except ValueError as e:
                    raise ProjectError(f"Configuration validation failed: {e}") from e

            # Log environment information for debugging and monitoring.
            env_info: dict[str, object] = {
                "python_version": sys.version,
                "working_directory": os.getcwd(),
                "debug_mode": settings.project.debug,
            }
            if log_file_path:
                env_info["log_file"] = log_file_path
            logger.log_system_event(
                event_name="environment_info",
                category="system",
                new_value=env_info,
            )

            # Start FastAPI server with uvicorn. The application itself is built by
            # create_app(), which uvicorn calls — see APPLICATION_FACTORY for why not here.
            logger.log_system_event(
                event_name="server_starting",
                category="lifecycle",
                new_value={
                    "host": settings.server.host,
                    "port": settings.server.port,
                    "workers": settings.server.workers,
                    "debug": settings.project.debug,
                },
            )

            # Installed BEFORE uvicorn.run, and that ordering is the whole trick.
            # uvicorn snapshots the current handlers, installs its own for the duration of the
            # serve loop, then restores the snapshot and re-raises the signal it caught
            # (uvicorn/server.py, capture_signals). Python's stock disposition for SIGTERM is
            # SIG_DFL, so that re-raise killed the process where it stood: everything below
            # uvicorn.run — the shutdown event, the lifecycle span's own close, and the trace
            # summary written into the log file — simply never ran, and the process died with 143.
            # Measured: SIGTERM left the log with no summary, SIGINT wrote one, because SIGINT's
            # default raises a catchable KeyboardInterrupt instead. Installing any Python handler
            # makes the re-raised SIGTERM survivable and the two signals behave alike. In Docker
            # the app is PID 1, where the kernel drops an unhandled signal, so this was invisible
            # there and only bit outside a container.
            _install_termination_handler()

            # START SERVER
            # Uvicorn now owns the event loop and signal handling.
            uvicorn.run(
                APPLICATION_FACTORY,
                factory=True,
                host=settings.server.host,
                port=settings.server.port,
                workers=settings.server.workers if not settings.project.debug else 1,
                reload=False,
                log_level="debug" if settings.project.debug else "info",
                # Disabled in favour of the semantic request.summary from AILoggingMiddleware —
                # a substitution that only holds while that summary is actually emitted, which
                # tests/application/test_request_summary_outcome.py::TestTheRequestSpanBeginsItsOwnTrace
                # now enforces. It did not hold for a long time, and the log went quiet.
                access_log=False,
                log_config=log_config,  # Using the pre-generated config
                timeout_graceful_shutdown=30,
            )

            # Reached only because the handler above swallowed the re-raised
            # signal. Report it the same way the KeyboardInterrupt branch does, so the lifecycle
            # summary says why the process stopped rather than looking like a plain return.
            if _RECEIVED_SIGNALS:
                stopped_by = signal.Signals(_RECEIVED_SIGNALS[-1]).name
                logger.log_system_event(
                    event_name="application_stopped",
                    category="lifecycle",
                    new_value={"reason": stopped_by},
                )
                span_ctx.output = {"status": "stopped", "reason": stopped_by}

        except ProjectError as exc:
            # Handle domain-specific errors with proper logging and propagation.
            logger.log_error(
                error_type="application_startup_failed",
                message="A critical error occurred during application startup",
                exception=exc,
                exc_info=True,
            )
            span_ctx.output = {"status": "failed", "reason": str(exc)}
            raise
        except KeyboardInterrupt:
            # Gracefully handle user-initiated shutdown.
            logger.log_system_event(
                event_name="application_stopped",
                category="lifecycle",
                new_value={"reason": "KeyboardInterrupt"},
            )
            span_ctx.output = {"status": "stopped", "reason": "KeyboardInterrupt"}
        finally:
            # Give SIGTERM back to the operating system. Everything the handler was
            # protecting — the shutdown event, this span's close, the trace summary below — is
            # either done or about to be, and a process that ignores every SIGTERM from here on is
            # a process an operator can only SIGKILL.
            _restore_termination_default()

            # Ensure proper cleanup and logging of application shutdown.
            logger.log_system_event(
                event_name="application_shutdown_complete", category="lifecycle"
            )

    # Prepend LLM-friendly trace summary to the log file.
    # This runs AFTER the application_lifecycle span closes (all events flushed).
    if log_file_path:
        try:
            import logging as _logging

            for handler in _logging.root.handlers[:]:
                handler.flush()
                handler.close()

            from project.core.logging.trace_formatter import (
                prepend_trace_summary,
            )

            # The return value is the other half of the same signal. The
            # function answers False when it parsed the log and found no trace to render —
            # no exception, nothing on stderr, and a log file that simply lacks the summary.
            # That is the absence the except branch below already warns about, arriving by
            # the other path, and it is what a renderer broken against the emitter looks
            # like from here.
            if not prepend_trace_summary(log_file_path):
                sys.stderr.write(
                    f"[shutdown] trace summary not written: no renderable trace in "
                    f"{log_file_path}\n"
                )
        except Exception as error:
            # Shutdown must finish regardless, but a silently missing trace
            # summary is exactly the kind of absence an agent later mistakes for "nothing
            # happened". Say so on stderr; the handlers are already closed by this point.
            sys.stderr.write(f"[shutdown] trace summary not written: {error!r}\n")


# SUMMARY: Entry point wrapper that handles application execution and error scenarios.
# RAISES: SystemExit: For various error conditions.
def run_application() -> None:
    # Execute main function.
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
    except ValidationError as e:
        try:
            get_logger(__name__).log_error(error_type="configuration_error", message=str(e))
        except Exception:
            sys.stderr.write(f"Configuration Error: {e}\n")
        sys.exit(1)
    except Exception as e:
        try:
            get_logger(__name__).log_error(
                error_type="application_error", message=str(e), exception=e
            )
        except Exception:
            sys.stderr.write(f"Application Error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    run_application()
