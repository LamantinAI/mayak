# FILE: tests/application/test_entrypoint_checks_the_configuration_first.py
# SUMMARY: The container refuses a bad configuration before it migrates anything.

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


# entrypoint.sh ran `alembic upgrade head` before the application checked its settings, so a
# container refused for them had already migrated the database it shares (round-4 finding R7,
# probed on a live stand). A fake `alembic` leaves a mark if it runs at all.
@pytest.mark.unit
def test_a_refused_configuration_migrates_nothing(tmp_path: Path) -> None:
    ran = tmp_path / "alembic-ran"
    fake = tmp_path / "bin" / "alembic"
    fake.parent.mkdir()
    fake.write_text(f"#!/bin/sh\ntouch {ran}\n", encoding="utf-8")
    fake.chmod(0o755)
    env = {
        "PATH": f"{fake.parent}:{Path(sys.executable).parent}:/usr/bin:/bin",
        "PYTHONPATH": str(_REPO_ROOT),
        "APP_LOG_DIR": str(tmp_path / "logs"),
        "APP_DEBUG": "false",
        "POSTGRES_ENABLED": "true",
        "POSTGRES_PASSWORD": "not-a-placeholder",
        "OPENAI_COMPATIBLE_API_KEY": "not-a-placeholder",
        "SERVER_CORS_ORIGINS": '["*"]',
    }

    result = subprocess.run(
        ["sh", str(_REPO_ROOT / "entrypoint.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert (result.returncode, ran.exists()) == (1, False)
    assert "SERVER_CORS_ORIGINS must not contain" in result.stderr
