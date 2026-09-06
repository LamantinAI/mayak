# FILE: project/launcher/main.py
# SUMMARY: The main executable file for launching FastAPI application.

import os
import signal
import sys
from types import FrameType
from typing import Optional

import uvicorn
from pydantic import ValidationError

from project.core.composition_root import CompositionRoot
from project.core.config import get_settings
from project.core.lifecycle import create_lifespan
from project.core.logging import get_logger, setup_logging
from project.domain.exceptions import ProjectError

# ATTRIBUTE: _TERMINATION_SIGNALS (tuple[int, ...])
# SUMMARY: Signals a process manager sends to ask for shutdown, and which must not be fatal here.
# SIGINT is absent on purpose: its default already raises KeyboardInterrupt, which the branch below
# catches, so it never had this problem.
_TERMINATION_SIGNALS: tuple[int, ...] = (signal.SIGTERM,)

# ATTRIBUTE: _RECEIVED_SIGNALS (list[int])
# SUMMARY: One-slot mailbox the handler writes into, read after uvicorn returns.
# A list rather than a module-level int so the handler needs no `global`.
_RECEIVED_SIGNALS: list[int] = []


# FUNCTION: _install_termination_handler
# SUMMARY: Make a termination signal survivable so shutdown work after uvicorn.run still runs.
def _install_termination_handler() -> None:
    # FUNCTION: _remember
    # SUMMARY: Record the signal and return, which is what keeps the process alive.
    def _remember(signum: int, _frame: Optional[FrameType]) -> None:
        _RECEIVED_SIGNALS.append(signum)

    for termination_signal in _TERMINATION_SIGNALS:
        signal.signal(termination_signal, _remember)


# FUNCTION: _restore_termination_default
# SUMMARY: Hand SIGTERM back to the operating system once the shutdown work is done being protected.
# NOTE: Without this the handler outlives its purpose. It is installed so the code after
# uvicorn.run — flushing handlers, rendering the trace summary into the log file — is not killed
# mid-write, but leaving it in place means every later SIGTERM is swallowed too, and a process
# stuck in that trailing phase answers only to SIGKILL. Reproduced: three SIGTERMs two seconds
# apart during the trailing phase, all recorded, process alive after each. An operator's second
# Ctrl-C-equivalent has to work, so the protection is scoped to the window that needs it.
def _restore_termination_default() -> None:
    for termination_signal in _TERMINATION_SIGNALS:
        signal.signal(termination_signal, signal.SIG_DFL)


# FUNCTION: main
# SUMMARY: The application's entry point. Initializes and starts FastAPI server.
# RAISES: ProjectError: In case of critical errors during startup.
def main() -> None:
    # **LOGIC_STEP**: Initialize application environment and logging.
    # We initialize logging in a safe bootstrap mode first, then reconfigure after validated settings load.
    log_config = setup_logging(level="INFO")

    logger = get_logger(__name__)

    log_file_path: str | None = None

    with logger.span("application_lifecycle") as span_ctx:
        logger.log_system_event(event_name="application_starting", category="lifecycle")

        try:
            # **LOGIC_STEP**: Validate configuration at startup.
            with logger.span("validate_configuration") as vc_span:
                try:
                    settings = get_settings()
                    settings.validate_runtime()

                    # **LOGIC_STEP**: Enable file-based logging when full-trace observability
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

                    # **LOGIC_STEP**: Pick log level — DEBUG when either debug ergonomics
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
                        # **LOGIC_STEP**: Said out loud, once, where an operator reading the boot
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

            # **LOGIC_STEP**: Log environment information for debugging and monitoring.
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

            # **LOGIC_STEP**: Create lifespan manager for async resources.
            lifespan = create_lifespan(settings, logger)

            # **LOGIC_STEP**: Build FastAPI application using composition root pattern.
            composition_root = CompositionRoot()
            # Integrate lifespan into application creation
            app = composition_root.build_application(lifespan=lifespan)

            # **LOGIC_STEP**: Store log file path for middleware (debug trace summary).
            app.state.log_file_path = log_file_path

            # **LOGIC_STEP**: Start FastAPI server with uvicorn.
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

            # **LOGIC_STEP**: Installed BEFORE uvicorn.run, and that ordering is the whole trick.
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
                app,
                host=settings.server.host,
                port=settings.server.port,
                workers=settings.server.workers if not settings.project.debug else 1,
                reload=False,  # Explicitly disabled to ensure stability with app instance
                log_level="debug" if settings.project.debug else "info",
                # Disabled in favour of the semantic request.summary from AILoggingMiddleware —
                # a substitution that only holds while that summary is actually emitted, which
                # tests/application/test_request_summary_outcome.py::TestTheRequestSpanBeginsItsOwnTrace
                # now enforces. It did not hold for a long time, and the log went quiet.
                access_log=False,
                log_config=log_config,  # Using the pre-generated config
                timeout_graceful_shutdown=30,
            )

            # **LOGIC_STEP**: Reached only because the handler above swallowed the re-raised
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
            # **LOGIC_STEP**: Handle domain-specific errors with proper logging and propagation.
            logger.log_error(
                error_type="application_startup_failed",
                message="A critical error occurred during application startup",
                exception=exc,
                exc_info=True,
            )
            span_ctx.output = {"status": "failed", "reason": str(exc)}
            raise
        except KeyboardInterrupt:
            # **LOGIC_STEP**: Gracefully handle user-initiated shutdown.
            logger.log_system_event(
                event_name="application_stopped",
                category="lifecycle",
                new_value={"reason": "KeyboardInterrupt"},
            )
            span_ctx.output = {"status": "stopped", "reason": "KeyboardInterrupt"}
        finally:
            # **LOGIC_STEP**: Give SIGTERM back to the operating system. Everything the handler was
            # protecting — the shutdown event, this span's close, the trace summary below — is
            # either done or about to be, and a process that ignores every SIGTERM from here on is
            # a process an operator can only SIGKILL.
            _restore_termination_default()

            # **LOGIC_STEP**: Ensure proper cleanup and logging of application shutdown.
            logger.log_system_event(
                event_name="application_shutdown_complete", category="lifecycle"
            )

    # **LOGIC_STEP**: Prepend LLM-friendly trace summary to the log file.
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

            # **LOGIC_STEP**: The return value is the other half of the same signal. The
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
            # **LOGIC_STEP**: Shutdown must finish regardless, but a silently missing trace
            # summary is exactly the kind of absence an agent later mistakes for "nothing
            # happened". Say so on stderr; the handlers are already closed by this point.
            sys.stderr.write(f"[shutdown] trace summary not written: {error!r}\n")


# FUNCTION: run_application
# SUMMARY: Entry point wrapper that handles application execution and error scenarios.
# RAISES: SystemExit: For various error conditions.
def run_application() -> None:
    # **LOGIC_STEP**: Execute main function.
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
