# FILE: tests/application/test_entrypoint_checks_the_configuration_first.py
# SUMMARY: Container entrypoint contract: env-only APP_STARTUP_MODE, pre-DB validation, and migrate/serve routing.

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _prepare_entrypoint_sandbox(
    tmp_path: Path,
    *,
    mode: str | None = None,
    postgres_enabled: str = "true",
    cors_origins: str = '["http://127.0.0.1:8000"]',
    postgres_password: str = "not-a-placeholder",
) -> tuple[dict[str, str], Path, Path]:
    alembic_mark = tmp_path / "alembic-ran"
    server_mark = tmp_path / "server-ran"

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    alembic = bin_dir / "alembic"
    alembic.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {alembic_mark}\n", encoding="utf-8")
    alembic.chmod(0o755)

    # Shadow project.launcher.main ahead of _REPO_ROOT on PYTHONPATH while leaving project.core.config
    # untouched through __path__ delegation on the `project` namespace shim.
    shim_pkg = tmp_path / "shim" / "project"
    (shim_pkg / "launcher").mkdir(parents=True)
    (shim_pkg / "__init__.py").write_text(
        f"__path__.append({str(_REPO_ROOT / 'project')!r})\n",
        encoding="utf-8",
    )
    (shim_pkg / "launcher" / "__init__.py").write_text("", encoding="utf-8")
    (shim_pkg / "launcher" / "main.py").write_text(
        f"from pathlib import Path\nPath({str(server_mark)!r}).write_text('started', encoding='utf-8')\n",
        encoding="utf-8",
    )

    env = {
        "PATH": f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin",
        "PYTHONPATH": f"{tmp_path / 'shim'}:{_REPO_ROOT}",
        "APP_LOG_DIR": str(tmp_path / "logs"),
        "AGENT_PROMPTS_DIR": str(_REPO_ROOT / "project" / "prompts"),
        "APP_DEBUG": "false",
        "POSTGRES_ENABLED": postgres_enabled,
        "POSTGRES_PASSWORD": postgres_password,
        "OPENAI_COMPATIBLE_API_KEY": "not-a-placeholder",
        "SERVER_CORS_ORIGINS": cors_origins,
    }
    if mode is not None:
        env["APP_STARTUP_MODE"] = mode
    return env, alembic_mark, server_mark


# entrypoint.sh ran `alembic upgrade head` before the application checked its settings, so a
# container refused for them had already migrated the database it shares (round-4 finding R7,
# probed on a live stand). Every startup mode must refuse bad settings before touching Alembic or
# starting the server.
@pytest.mark.unit
@pytest.mark.parametrize("mode", [None, "migrate-and-serve", "migrate", "serve"])
def test_a_refused_configuration_migrates_and_serves_nothing(
    tmp_path: Path, mode: str | None
) -> None:
    env, alembic_mark, server_mark = _prepare_entrypoint_sandbox(
        tmp_path, mode=mode, cors_origins='["*"]'
    )

    result = subprocess.run(
        ["sh", str(_REPO_ROOT / "entrypoint.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert (result.returncode, alembic_mark.exists(), server_mark.exists()) == (1, False, False)
    assert "SERVER_CORS_ORIGINS must not contain" in result.stderr


@pytest.mark.unit
@pytest.mark.parametrize("bad_mode", ["", " ", "migarte", "up", "migrate_and_serve"])
def test_invalid_startup_mode_is_rejected_before_settings_or_database(
    tmp_path: Path, bad_mode: str
) -> None:
    env, alembic_mark, server_mark = _prepare_entrypoint_sandbox(
        tmp_path, mode=bad_mode, cors_origins='["*"]'
    )

    result = subprocess.run(
        ["sh", str(_REPO_ROOT / "entrypoint.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert (result.returncode, alembic_mark.exists(), server_mark.exists()) == (2, False, False)
    assert "invalid APP_STARTUP_MODE" in result.stderr
    assert "Checking the configuration" not in result.stdout


@pytest.mark.unit
@pytest.mark.parametrize("args", [["serve"], ["migrate"], ["migrate-and-serve", "extra"]])
def test_positional_arguments_are_rejected_before_settings_or_database(
    tmp_path: Path, args: list[str]
) -> None:
    env, alembic_mark, server_mark = _prepare_entrypoint_sandbox(tmp_path)

    result = subprocess.run(
        ["sh", str(_REPO_ROOT / "entrypoint.sh"), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert (result.returncode, alembic_mark.exists(), server_mark.exists()) == (2, False, False)
    assert "no positional arguments" in result.stderr
    assert "Checking the configuration" not in result.stdout


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mode", "postgres_enabled", "expects_alembic", "expects_server"),
    [
        (None, "true", True, True),
        (None, "false", False, True),
        ("migrate-and-serve", "true", True, True),
        ("migrate-and-serve", "false", False, True),
        ("migrate", "true", True, False),
        ("migrate", "false", False, False),
        ("serve", "true", False, True),
        ("serve", "false", False, True),
    ],
)
def test_startup_mode_routes_migration_and_server_execution(
    tmp_path: Path,
    mode: str | None,
    postgres_enabled: str,
    expects_alembic: bool,
    expects_server: bool,
) -> None:
    env, alembic_mark, server_mark = _prepare_entrypoint_sandbox(
        tmp_path, mode=mode, postgres_enabled=postgres_enabled
    )

    result = subprocess.run(
        ["sh", str(_REPO_ROOT / "entrypoint.sh")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert alembic_mark.exists() is expects_alembic
    if expects_alembic:
        assert alembic_mark.read_text(encoding="utf-8").strip() == "upgrade head"
    assert server_mark.exists() is expects_server


@pytest.mark.unit
def test_compose_and_sample_pass_startup_mode_to_the_container() -> None:
    compose = (_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    sample = (_REPO_ROOT / ".env.sample").read_text(encoding="utf-8")

    assert "APP_STARTUP_MODE: ${APP_STARTUP_MODE-migrate-and-serve}" in compose
    assert "APP_STARTUP_MODE=migrate-and-serve" in sample
