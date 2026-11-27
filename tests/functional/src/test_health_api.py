# FILE: tests/functional/src/test_health_api.py
# SUMMARY: The kernel over HTTP in the built image: it serves, and it finds its migrated database ready.
# A project keeps this when the reference vertical goes: without it, `make test-e2e` would send no
# request at all through the image, its entrypoint and the migrations that entrypoint runs.

import os
import subprocess

import pytest

from utils.helpers import SendRequest

pytestmark = pytest.mark.e2e


async def test_the_image_serves_and_reports_its_database_mode(
    make_get_request: SendRequest,
) -> None:
    live = await make_get_request("/health/")
    ready = await make_get_request("/health/ready")

    assert (live["status"], ready["status"]) == (200, 200)
    expected_database = "disabled" if os.environ.get("POSTGRES_ENABLED") == "false" else "healthy"
    assert ready["body"]["checks"]["database"]["status"] == expected_database


def test_the_image_entrypoint_enforces_startup_modes() -> None:
    positional = subprocess.run(
        ["/app/entrypoint.sh", "serve"],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert positional.returncode == 2
    assert "no positional arguments" in positional.stderr

    bad_mode = subprocess.run(
        ["/app/entrypoint.sh"],
        cwd="/app",
        env=os.environ | {"APP_STARTUP_MODE": "migarte"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert bad_mode.returncode == 2
    assert "invalid APP_STARTUP_MODE" in bad_mode.stderr

    migrate_env = os.environ | {
        "APP_STARTUP_MODE": "migrate",
        "SERVER_CORS_ORIGINS": '["http://test-app:8000"]',
    }
    migrate_only = subprocess.run(
        ["/app/entrypoint.sh"],
        cwd="/app",
        env=migrate_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert migrate_only.returncode == 0, migrate_only.stderr or migrate_only.stdout
    assert "APP_STARTUP_MODE=migrate — completed" in migrate_only.stdout
