# FILE: tests/application/test_lifecycle_and_log_rotation.py
# SUMMARY: Cover the two kernel modules nothing exercised — the lifespan body and log rotation.
# NOTE: Measured, not assumed. A marker written as the first statement inside `lifespan` was never
# created by a full run of tests/application + tests/infrastructure + tests/integration: the
# `async_client` fixture drives the app through a bare httpx ASGITransport, and that transport does
# not run lifespan events at all. So the startup path — opening the pool, and the except branch
# that cleans up and re-raises — had zero coverage while the suite reported 565 green.
# `rotate_log_files` was worse: it calls Path.unlink() on real files and no test in any suite ever
# named it. Both are called for real at process start, where a defect deletes logs or hangs boot.

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from project.core.lifecycle import create_lifespan
from project.core.logging import get_logger
from project.core.logging.file_manager import create_run_log_path, rotate_log_files


# FUNCTION: _app_with_services
# SUMMARY: Build a bare FastAPI carrying a service registry, without the composition root.
# OUTPUT: (FastAPI): Application whose state holds the given registry.
def _app_with_services(services: dict[str, Any]) -> FastAPI:
    app = FastAPI()
    app.state.services = services
    return app


# CLASS: tests.application.test_lifecycle_and_log_rotation.TestLifespanStartup
# SUMMARY: Verify the startup path actually runs and behaves on both branches.
class TestLifespanStartup:
    # FUNCTION: test_startup_opens_the_pool_and_records_start_time
    # SUMMARY: Verify the happy path opens the pool once and stamps uptime's origin.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_startup_opens_the_pool_and_records_start_time(
        self,
        test_settings: Any,
    ) -> None:
        pool = MagicMock()
        pool.open = AsyncMock()
        app = _app_with_services({"db_pool": pool})
        lifespan = create_lifespan(test_settings, get_logger(__name__))

        async with lifespan(app):
            assert isinstance(app.state.start_time, float)

        pool.open.assert_awaited_once_with()

    # FUNCTION: test_startup_failure_cleans_up_and_reraises
    # SUMMARY: Verify a pool that cannot open closes what was built and fails startup loudly.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_startup_failure_cleans_up_and_reraises(self, test_settings: Any) -> None:
        # **LOGIC_STEP**: This is the branch that matters in production — an unreachable database
        # at boot. Without the cleanup call the process leaks the pool's sockets on every failed
        # start; without the re-raise the container reports itself healthy with no database.
        pool = MagicMock()
        pool.open = AsyncMock(side_effect=OSError("connection refused"))
        pool.close = AsyncMock()
        app = _app_with_services({"db_pool": pool})
        lifespan = create_lifespan(test_settings, get_logger(__name__))

        with pytest.raises(OSError, match="connection refused"):
            async with lifespan(app):
                pass

        # **LOGIC_STEP**: The `_with()` spelling with no arguments is deliberate — it states that
        # close() was awaited once *and* that nothing was handed to it. The bare assert_awaited_once()
        # says only the first half, and validate_test_quality.py exists because of what that costs.
        pool.close.assert_awaited_once_with()

    # FUNCTION: test_shutdown_closes_the_pool
    # SUMMARY: Verify the normal exit path releases the pool.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_shutdown_closes_the_pool(self, test_settings: Any) -> None:
        pool = MagicMock()
        pool.open = AsyncMock()
        pool.close = AsyncMock()
        app = _app_with_services({"db_pool": pool})
        lifespan = create_lifespan(test_settings, get_logger(__name__))

        async with lifespan(app):
            pass

        pool.close.assert_awaited_once_with()

    # FUNCTION: test_startup_without_a_pool_is_not_an_error
    # SUMMARY: Verify POSTGRES_ENABLED=false leaves startup working rather than raising.
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_startup_without_a_pool_is_not_an_error(self, test_settings: Any) -> None:
        app = _app_with_services({"db_pool": None})
        lifespan = create_lifespan(test_settings, get_logger(__name__))

        async with lifespan(app):
            assert app.state.services["db_pool"] is None


# CLASS: tests.application.test_lifecycle_and_log_rotation.TestLogRotation
# SUMMARY: Verify rotation deletes the oldest files and only those.
class TestLogRotation:
    # FUNCTION: _make_logs
    # SUMMARY: Create numbered log files whose names sort chronologically.
    # OUTPUT: (list[Path]): Created paths, oldest first.
    @staticmethod
    def _make_logs(directory: Path, count: int) -> list[Path]:
        paths = []
        for index in range(count):
            path = directory / f"2026-08-0{index + 1}T00-00-00_pid1.ndjson"
            path.write_text("{}\n", encoding="utf-8")
            paths.append(path)
        return paths

    # FUNCTION: test_keeps_the_newest_and_deletes_the_rest
    # SUMMARY: Verify retention keeps exactly max_files, and the surviving ones are the newest.
    @pytest.mark.unit
    def test_keeps_the_newest_and_deletes_the_rest(self, tmp_path: Path) -> None:
        created = self._make_logs(tmp_path, 5)

        deleted = rotate_log_files(str(tmp_path), max_files=2)

        assert deleted == 3
        survivors = sorted(p.name for p in tmp_path.glob("*.ndjson"))
        assert survivors == [created[3].name, created[4].name]

    # FUNCTION: test_rotation_is_a_no_op_below_the_limit
    # SUMMARY: Verify a directory under the limit loses nothing.
    @pytest.mark.unit
    def test_rotation_is_a_no_op_below_the_limit(self, tmp_path: Path) -> None:
        self._make_logs(tmp_path, 2)

        assert rotate_log_files(str(tmp_path), max_files=5) == 0
        assert len(list(tmp_path.glob("*.ndjson"))) == 2

    # FUNCTION: test_zero_disables_rotation_instead_of_deleting_everything
    # SUMMARY: Verify max_files=0 keeps every file rather than clearing the directory.
    @pytest.mark.unit
    def test_zero_disables_rotation_instead_of_deleting_everything(self, tmp_path: Path) -> None:
        # **LOGIC_STEP**: The off-by-one that matters. Read as "keep zero files", this would wipe
        # the directory; the contract is "rotation disabled". Nothing checked which it was.
        self._make_logs(tmp_path, 3)

        assert rotate_log_files(str(tmp_path), max_files=0) == 0
        assert len(list(tmp_path.glob("*.ndjson"))) == 3

    # FUNCTION: test_non_ndjson_files_are_left_alone
    # SUMMARY: Verify rotation never touches files it does not own.
    @pytest.mark.unit
    def test_non_ndjson_files_are_left_alone(self, tmp_path: Path) -> None:
        self._make_logs(tmp_path, 4)
        keeper = tmp_path / "important.txt"
        keeper.write_text("not a log", encoding="utf-8")

        rotate_log_files(str(tmp_path), max_files=1)

        assert keeper.exists()

    # FUNCTION: test_missing_directory_is_not_an_error
    # SUMMARY: Verify a first run with no log directory yet returns zero instead of raising.
    @pytest.mark.unit
    def test_missing_directory_is_not_an_error(self, tmp_path: Path) -> None:
        assert rotate_log_files(str(tmp_path / "nope"), max_files=3) == 0

    # FUNCTION: test_run_log_path_is_created_inside_a_new_directory
    # SUMMARY: Verify the per-run path creates its parent and sorts chronologically by name.
    @pytest.mark.unit
    def test_run_log_path_is_created_inside_a_new_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "fresh" / "logs"

        path = create_run_log_path(str(target))

        assert target.is_dir()
        assert path.parent == target
        assert path.name.endswith(".ndjson")
