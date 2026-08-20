# FILE: tests/application/test_launcher_shutdown.py
# SUMMARY: Verify SIGTERM leaves the launcher's post-uvicorn shutdown work enough time to finish.

from __future__ import annotations

import ast
import signal as signal_module
from pathlib import Path

import pytest

from project.launcher.main import (
    _RECEIVED_SIGNALS,
    _install_termination_handler,
    _restore_termination_default,
)

# ATTRIBUTE: LAUNCHER_SOURCE (Path)
# SUMMARY: The launcher module, read as text for the structural assertion at the end of this file.
LAUNCHER_SOURCE = Path(__file__).resolve().parents[2] / "project" / "launcher" / "main.py"


# CLASS: tests.application.test_launcher_shutdown.TestTerminationSignalIsSurvivable
# SUMMARY: Verify SIGTERM lets the shutdown work after uvicorn.run run, instead of killing the process.
# NOTE: The trace summary is written after `uvicorn.run(...)` returns. uvicorn restores the signal
# handlers it found and then re-raises the signal it caught, and Python's stock disposition for
# SIGTERM is SIG_DFL — so the process died where it stood and the summary was never written.
# Measured before the fix: SIGTERM gave exit 143 and a log file with no summary, SIGINT gave exit 0
# and a summary, because SIGINT's default raises a catchable KeyboardInterrupt. In Docker the app is
# PID 1, where the kernel drops an unhandled signal, so this only ever bit outside a container.
class TestTerminationSignalIsSurvivable:
    # FUNCTION: test_the_handler_replaces_the_fatal_default
    # SUMMARY: Verify SIGTERM is no longer left at SIG_DFL once the launcher has installed its handler.
    @pytest.mark.unit
    def test_the_handler_replaces_the_fatal_default(self) -> None:
        previous = signal_module.getsignal(signal_module.SIGTERM)
        try:
            _install_termination_handler()

            installed = signal_module.getsignal(signal_module.SIGTERM)
            assert installed is not signal_module.SIG_DFL
            assert callable(installed)
        finally:
            signal_module.signal(signal_module.SIGTERM, previous)

    # FUNCTION: test_a_raised_signal_is_recorded_and_does_not_end_the_process
    # SUMMARY: Verify the handler returns normally, which is what keeps the shutdown path alive.
    @pytest.mark.unit
    def test_a_raised_signal_is_recorded_and_does_not_end_the_process(self) -> None:
        # **LOGIC_STEP**: `raise_signal` is exactly what uvicorn does on its way out
        # (uvicorn/server.py, capture_signals). If the handler did not swallow it, this test
        # process would terminate here rather than reach the assertion.
        previous = signal_module.getsignal(signal_module.SIGTERM)
        before = len(_RECEIVED_SIGNALS)
        try:
            _install_termination_handler()
            signal_module.raise_signal(signal_module.SIGTERM)

            assert _RECEIVED_SIGNALS[before:] == [signal_module.SIGTERM]
        finally:
            del _RECEIVED_SIGNALS[before:]
            signal_module.signal(signal_module.SIGTERM, previous)

    # FUNCTION: test_the_protection_is_handed_back_when_the_window_closes
    # SUMMARY: Verify SIGTERM becomes fatal again, so the process never stops answering to it.
    @pytest.mark.unit
    def test_the_protection_is_handed_back_when_the_window_closes(self) -> None:
        # **LOGIC_STEP**: The handler exists to protect the shutdown work after uvicorn.run from
        # being killed mid-write, and nothing beyond that. Left installed it swallows every later
        # SIGTERM too — reproduced with three of them two seconds apart, all recorded, the process
        # alive after each and killable only with SIGKILL. An operator's second attempt has to work.
        previous = signal_module.getsignal(signal_module.SIGTERM)
        try:
            _install_termination_handler()
            _restore_termination_default()

            assert signal_module.getsignal(signal_module.SIGTERM) is signal_module.SIG_DFL
        finally:
            signal_module.signal(signal_module.SIGTERM, previous)

    # FUNCTION: test_the_launcher_restores_the_default_on_every_exit_path
    # SUMMARY: Verify the restore sits in a finally, not on the happy path only.
    @pytest.mark.unit
    def test_the_launcher_restores_the_default_on_every_exit_path(self) -> None:
        # **LOGIC_STEP**: A startup failure raises out of main() through the same block. If the
        # restore were reachable only after a clean uvicorn return, a crashed process would be the
        # one left ignoring SIGTERM — the worst case for the property this guards.
        source = LAUNCHER_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        in_finally = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            for statement in node.finalbody
            for node2 in ast.walk(statement)
            if isinstance(node2, ast.Call)
            and isinstance(node2.func, ast.Name)
            and node2.func.id == "_restore_termination_default"
        ]

        assert in_finally, "_restore_termination_default is not called from a finally block"
