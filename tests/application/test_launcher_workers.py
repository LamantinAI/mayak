# FILE: tests/application/test_launcher_workers.py
# SUMMARY: Verify SERVER_WORKERS above one starts several worker processes, each able to build the application.

from __future__ import annotations

import logging
import signal as signal_module
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI

from project.core.config import clear_settings_override, set_settings_override
from project.launcher.main import main
from tests.conftest import _FixtureSettings as FixtureSettings


# Stand-in for uvicorn's multi-process supervisor: keeps the config instead of forking workers.
class _RecordingSupervisor:
    started: list[Any] = []

    def __init__(self, config: Any, sockets: list[Any]) -> None:
        self.config = config

    def run(self) -> None:
        _RecordingSupervisor.started.append(self.config)


# SERVER_WORKERS=2 ended the process at once with exit code 3. uvicorn refuses an application
# object when it has to start several workers — each worker process imports the application itself —
# and the launcher handed it the object it had just built. Found by the bench2 measurement
# (2026-09-24): the setting was unusable, and one process hid the fact that an asyncio.Lock guarding
# a booking rule does not hold across processes — two workers let 28 of 30 double bookings through.
# The real uvicorn.run runs here; only the fork and the socket are replaced.
@pytest.mark.unit
def test_several_workers_start_and_each_can_build_the_application(
    test_settings: FixtureSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(test_settings.server, "workers", 2)
    monkeypatch.setattr(test_settings.project, "debug", False)
    _RecordingSupervisor.started.clear()
    root_logger = logging.getLogger()
    handlers_before = list(root_logger.handlers)
    level_before = root_logger.level
    sigterm_before = signal_module.getsignal(signal_module.SIGTERM)
    set_settings_override(test_settings)
    try:
        with (
            patch("project.launcher.main.get_settings", return_value=test_settings),
            patch("uvicorn.main.Multiprocess", _RecordingSupervisor),
            patch("uvicorn.config.Config.bind_socket", return_value=MagicMock()),
        ):
            try:
                main()
            except SystemExit as exc:
                pytest.fail(f"uvicorn refused to start two workers and exited with code {exc.code}")

            [config] = _RecordingSupervisor.started
            assert config.workers == 2
            # What a worker process does first: import the application from the
            # string it was given, and call it when it is a factory.
            loaded = config.load_app()
            application = loaded() if config.factory else loaded

        assert isinstance(application, FastAPI)
    finally:
        clear_settings_override()
        signal_module.signal(signal_module.SIGTERM, sigterm_before)
        # main() reconfigures the root logger through dictConfig; put back exactly
        # what this test inherited, as test_full_trace_is_announced.py does.
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            if handler not in handlers_before:
                handler.close()
        for handler in handlers_before:
            root_logger.addHandler(handler)
        root_logger.setLevel(level_before)
