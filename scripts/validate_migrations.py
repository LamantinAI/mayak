#!/usr/bin/env python3
# FILE: validate_migrations.py
# Quality gate that verifies Alembic migrations are up to date with the SQLAlchemy metadata.

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any, Sequence

from validation_support.rendering import render_json
from validation_support.validator_contract import build_validator_issue_payload


# Absolute repository root used as the working directory for Alembic commands.
ROOT_DIR = Path(__file__).resolve().parent.parent

# Explicit Alembic configuration file path passed to every CLI invocation.
ALEMBIC_INI_PATH = ROOT_DIR / "alembic.ini"


# Stable rule_id → playbook mapping for migration validator failures.
_MIGRATIONS_RULE_PLAYBOOKS: dict[str, dict[str, object]] = {
    "migrations.head_drift": {
        "meaning": (
            "Alembic check reports that the SQLAlchemy metadata is out of sync with the latest "
            "revision. Schema changes in the ORM are not yet captured by an Alembic revision."
        ),
        "suggested_fix": (
            "Run `make autogenerate-migration MSG=describe_change` to scaffold a new revision, "
            "review the generated upgrade/downgrade ops, and commit the file."
        ),
        "read_first": [
            "alembic/versions/",
            "project/infrastructure/persistence/orm_models.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_migrations.py",
        "likely_fix_shape": (
            "Add the missing Alembic revision capturing the SQLAlchemy metadata change."
        ),
        "next_checks": [
            "uv run python scripts/validate_migrations.py",
            "make quality-gates",
        ],
        "stop_widening_condition": ("Stop once `alembic check` reports no detected changes."),
    },
    "migrations.upgrade_failure": {
        "meaning": (
            "Alembic upgrade head failed against the local database. A revision's upgrade op is "
            "broken, or the schema it expects to find is not the schema that is there."
        ),
        "suggested_fix": (
            "Read the traceback for the revision that failed, fix that revision's upgrade op, "
            "and rerun the validator. If the failure is `Can't locate revision identified by`, "
            "the database is ahead of this branch rather than broken — see "
            "migrations.foreign_revision, which reports that case on its own."
        ),
        "read_first": [
            "alembic/versions/",
            "alembic.ini",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_migrations.py",
        "likely_fix_shape": (
            "Repair the failing revision. Recreating the database is not a fix here: on a "
            "database more than one checkout can reach, it drops the schema those branches "
            "migrated, and this rule's own cause survives it anyway."
        ),
        "next_checks": [
            "uv run python scripts/validate_migrations.py",
        ],
        "stop_widening_condition": (
            "Stop once `alembic upgrade head` succeeds against the local database."
        ),
    },
    "migrations.foreign_revision": {
        "meaning": (
            "The database is stamped with a revision this branch's alembic/versions/ does not "
            "contain. Something else migrated it — most often another worktree of this "
            "repository, on a branch that has a revision this one has not merged yet. Alembic "
            "cannot compute a path from a revision it has never seen, so `upgrade head` fails "
            "with `Can't locate revision identified by` and says nothing about why."
        ),
        "suggested_fix": (
            "Catch up rather than roll back. `git fetch` and merge the branch that owns the "
            "revision named in the message, so your own alembic/versions/ contains it. If this "
            "checkout needs a database nobody else is using, `make db-up-worktree` gives it one "
            "on its own port and volume. To move this database back instead, `alembic downgrade "
            "<a revision both branches share>` — and only when nothing else is using it."
        ),
        "read_first": [
            "alembic/versions/",
            ".env",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_migrations.py",
        "likely_fix_shape": (
            "Merge the branch that owns the stamped revision, or give this worktree its own "
            "database. Recreating the shared one destroys the schema another branch migrated."
        ),
        "next_checks": [
            "uv run python scripts/validate_migrations.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop once the stamped revision is one this branch's alembic/versions/ contains."
        ),
    },
    "migrations.database_unreachable": {
        "meaning": (
            "Alembic could not connect to the configured database. Validator skipped without "
            "actually verifying migrations — common in dev/CI environments without Postgres. "
            "Non-fatal locally by design (see database_skip_is_allowed); the message this rule "
            "carries opens with a 'MIGRATIONS NOT VERIFIED' banner precisely because that design "
            "choice must not read as a pass — see _NOT_VERIFIED_BANNER in validate_migrations.py "
            "for what silently reading as one already cost."
        ),
        "suggested_fix": (
            "Start a database for this checkout alone (`make db-up-worktree`) and rerun, or "
            "accept the skip — `migrations.database_unreachable` is non-fatal by design."
        ),
        "read_first": [
            "docker-compose.yml",
            ".env.sample",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_migrations.py",
        "likely_fix_shape": (
            "Provision a reachable Postgres or accept the skip status as informational."
        ),
        "next_checks": [
            "uv run python scripts/validate_migrations.py",
        ],
        "stop_widening_condition": ("Stop once Postgres is reachable OR the skip is acknowledged."),
    },
    "migrations.database_disabled": {
        "meaning": (
            "This project set POSTGRES_ENABLED=false, so it declares no relational store and "
            "the migration gate has nothing to verify. Informational, never blocking."
        ),
        "suggested_fix": (
            "Nothing to fix. If this project does need PostgreSQL, set POSTGRES_ENABLED=true "
            "in .env and rerun."
        ),
        "read_first": [
            ".env",
            "AGENTS.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_migrations.py",
        "likely_fix_shape": "No change expected; flip POSTGRES_ENABLED only if the project really uses a database.",
        "next_checks": [
            "uv run python scripts/validate_migrations.py",
        ],
        "stop_widening_condition": (
            "Stop immediately — this status is informational and blocks nothing."
        ),
    },
    "migrations.database_required": {
        "meaning": (
            "Alembic could not connect while this run had to verify migrations: under `make "
            "verify`, with --require-database, or with CI=true. MIGRATIONS_ALLOW_SKIP=1 relaxes "
            "only CI; under the other two it changes nothing. Skipping here would let a broken "
            "revision chain reach main with a green pipeline."
        ),
        "suggested_fix": (
            "For this checkout, `make db-up-worktree` (after `make init-project` without a .env) "
            "and rerun. For a CI job, add a Postgres service to it; for a target set explicitly "
            "through POSTGRES_*, start or fix that database."
        ),
        "read_first": [".env", "Makefile", ".github/workflows/ci.yml"],
        "smallest_command_to_rerun": "uv run python scripts/validate_migrations.py --require-database",
        "likely_fix_shape": (
            "Start the database this run points at: `make db-up-worktree` for this checkout; "
            "for CI, add or repair the job's Postgres service and its POSTGRES_* environment."
        ),
        "next_checks": [
            "uv run python scripts/validate_migrations.py --require-database",
        ],
        "stop_widening_condition": (
            "Stop once the validator actually runs `alembic upgrade head` and `alembic check`."
        ),
    },
    "migrations.multiple_heads": {
        "meaning": (
            "alembic/versions/ holds more than one head: two revisions share a down_revision, "
            "so `alembic upgrade head` has no single target and refuses to run. Read from the "
            "files alone — this check needs no database and runs before the skip an "
            "unreachable one would trigger."
        ),
        "suggested_fix": (
            "Point the newer revision's down_revision at the other head, or run "
            "`uv run alembic -c alembic.ini merge heads -m describe_merge` to add a merge "
            "revision."
        ),
        "read_first": [
            "alembic/versions/",
        ],
        "smallest_command_to_rerun": "uv run alembic -c alembic.ini heads",
        "likely_fix_shape": ("Rewrite one down_revision so the revisions form a single chain."),
        "next_checks": [
            "uv run alembic -c alembic.ini heads",
            "uv run python scripts/validate_migrations.py",
        ],
        "stop_widening_condition": ("Stop once `alembic heads` prints exactly one revision."),
    },
    "migrations.broken_revision_graph": {
        "meaning": (
            "Alembic cannot walk alembic/versions/: a down_revision names a revision that does "
            "not exist, or a revision file fails to import. Read from the files alone, without "
            "a database."
        ),
        "suggested_fix": (
            "Fix the down_revision or the import error the message names; "
            "`uv run alembic -c alembic.ini history` shows the chain Alembic can see."
        ),
        "read_first": [
            "alembic/versions/",
        ],
        "smallest_command_to_rerun": "uv run alembic -c alembic.ini history",
        "likely_fix_shape": ("Correct one revision id so every down_revision resolves."),
        "next_checks": [
            "uv run alembic -c alembic.ini history",
            "uv run python scripts/validate_migrations.py",
        ],
        "stop_widening_condition": (
            "Stop once `alembic history` lists every revision without a warning."
        ),
    },
}


# Environment variable that re-permits the database skip inside CI for deliberate exceptions.
_SKIP_OPT_OUT_ENV = "MIGRATIONS_ALLOW_SKIP"

# Opens the unreachable-database skip message so it cannot be mistaken for a passing step.
# This skip is deliberately non-fatal locally (database_skip_is_allowed) — the defect this
# banner fixes is not the skip itself but its old wording, "Database is not reachable — migration
# validation skipped.", which read like every other line among the ~20 steps `make quality-gates`
# prints. Measured on 2026-09-07, on both projects two agents built from this template: a
# migration that dropped a column instead of renaming it cleared every local gate,
# this one included, because nothing in its output stood out as unverified rather than passing.
# See collect_migration_issues() for where this is used, and get_migrations_rule_playbook's entry
# for migrations.database_unreachable for the reasoning in full — one fact, read from here.
_NOT_VERIFIED_BANNER = "MIGRATIONS NOT VERIFIED"


# Report whether this project uses a relational store at all.
# Returns: False when POSTGRES_ENABLED is explicitly disabled in the environment or .env.
def postgres_is_enabled(root_dir: Path | None = None) -> bool:
    base = ROOT_DIR if root_dir is None else root_dir
    value = os.environ.get("POSTGRES_ENABLED")
    if value is None:
        try:
            from dotenv import dotenv_values
        except ImportError:
            pass
        else:
            for path in (base / ".env", base / ".env.sample"):
                value = dotenv_values(path).get("POSTGRES_ENABLED")
                if value is not None:
                    break
    return (value or "true").strip().lower() not in {"0", "false", "no", "off"}


# Report whether an unreachable database may downgrade this gate to an informational skip.
# Returns: False inside CI, where a silent skip would hide a broken revision chain.
def database_skip_is_allowed() -> bool:
    if os.environ.get("MAYAK_REQUIRE_MIGRATION_DB") == "1":
        return False
    # Locally a developer without Postgres should not be blocked. In CI the
    # skip is the whole problem: no job provisioned a database, so the gate never ran and a
    # second alembic head or a dangling down_revision would sail through green.
    if os.environ.get(_SKIP_OPT_OUT_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return True
    return os.environ.get("CI", "").strip().lower() not in {"1", "true", "yes"}


_REQUIRED_REASON = "PostgreSQL is not reachable and this run must verify migrations."


# Name the one move that can make a strict run pass where it runs. Never the skip switch: until
# 1.2.1 this advised MIGRATIONS_ALLOW_SKIP=1, which `make verify` and --require-database ignore
# (database_skip_is_allowed), so every agent that followed it failed the same way again.
def _in_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}


def _has_explicit_postgres_env() -> bool:
    from project.core.config_settings_core import _POSTGRES_TARGET_FIELDS

    return any(
        os.environ.get(f"POSTGRES_{field.upper()}") is not None for field in _POSTGRES_TARGET_FIELDS
    )


# Render a settings validation failure without ever echoing raw input values (such as passwords).
def _format_settings_error(error: ValueError, *, in_ci: bool) -> str:
    from pydantic import ValidationError

    if isinstance(error, ValidationError):
        fields = sorted(
            {".".join(str(part) for part in item.get("loc", ())) for item in error.errors()} - {""}
        )
        detail = f" ({', '.join(fields)})" if fields else ""
        if in_ci or _has_explicit_postgres_env():
            return (
                f"Invalid PostgreSQL configuration{detail}. "
                "The target is set explicitly (POSTGRES_* in the environment or CI): "
                "fix that configuration for this job. The worktree database is not used here."
            )
        return (
            f"Invalid PostgreSQL configuration{detail}: "
            "fix .env (or run make init-project), then make db-up-worktree, then rerun."
        )
    return str(error)


def _load_postgres_settings(root_dir: Path) -> tuple[Any | None, str | None]:
    from project.core.config import PostgresSettings

    try:
        return PostgresSettings(_env_file=root_dir / ".env"), None  # type: ignore[call-arg]
    except ValueError as error:
        return None, _format_settings_error(error, in_ci=_in_ci())


# Decide whether the effective PostgresSettings describe a target worth probing. Any explicit
# POSTGRES_* env/file/worktree target field qualifies (including pure env-only targets that omit
# POSTGRES_USER and rely on defaults), as does CI=true without .env where an already-listening CI
# database may use the default connection parameters.
def _has_probeable_database_target(settings: Any, *, in_ci: bool) -> bool:
    from project.core.config_settings_core import _POSTGRES_TARGET_FIELDS

    if not settings.enabled:
        return False
    return bool(settings.model_fields_set & _POSTGRES_TARGET_FIELDS) or in_ci


def _database_required_action_in(root_dir: Path) -> str:
    from project.core.config_settings_core import (
        _LOOPBACK_DATABASE_HOSTS,
        _POSTGRES_TARGET_FIELDS,
    )

    in_ci = _in_ci()
    settings, config_error = _load_postgres_settings(root_dir)
    if config_error is not None or settings is None:
        return config_error or "Invalid PostgreSQL configuration."
    has_target = bool(settings.model_fields_set & _POSTGRES_TARGET_FIELDS)
    if in_ci or settings.connection_source == "explicit settings":
        return (
            "The target is set explicitly (POSTGRES_* in the environment or CI): start or fix "
            "that database for this job. The worktree database is not used here."
        )
    if has_target and settings.host not in _LOOPBACK_DATABASE_HOSTS:
        return (
            "The target is set explicitly (non-loopback POSTGRES_HOST in .env): start or fix "
            "that database. The worktree database is not used here."
        )
    if not has_target or not (root_dir / ".env").is_file():
        return "No connection settings: make init-project, then make db-up-worktree, then rerun."
    return "Start this checkout's own database: make db-up-worktree, then rerun."


def _database_required_action() -> str:
    return _database_required_action_in(ROOT_DIR)


# Immutable descriptor for a single Alembic validation step.
@dataclass(frozen=True)
class MigrationCommand:
    # Human-readable command label shown in progress output.
    name: str

    # Fully expanded command argv executed without a shell.
    argv: tuple[str, ...]


# Structured result of a failed migration validation step, suitable for JSON output and playbook lookup.
@dataclass(slots=True)
class MigrationIssue:
    # Stable rule identifier consumable by doctor_ai_context.py --rule.
    rule_id: str

    # Name of the failed Alembic step (upgrade-head, check, ...).
    command_name: str

    # Human-readable description of the failure.
    message: str

    # Subprocess exit code. Zero indicates a non-failure status (skip).
    returncode: int

    # 'error' (blocks gate), 'warning' (visible but non-blocking), or 'info' (purely informational).
    severity: str = "error"

    # Captured stderr output from the failed Alembic subprocess (truncated). None when not applicable or no stderr was emitted.
    stderr: str | None = None


# Return a copy of the playbook for a migrations rule_id, or None if unknown.
def get_migrations_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _MIGRATIONS_RULE_PLAYBOOKS.get(rule_id)
    if playbook is None:
        return None
    return dict(playbook)


# Check whether the PostgreSQL database is reachable using psycopg.
# Returns: True when the database accepts connections, False otherwise.
def _is_database_reachable_in(root_dir: Path) -> bool:
    settings, config_error = _load_postgres_settings(root_dir)
    if config_error is not None or settings is None:
        return False
    if not _has_probeable_database_target(settings, in_ci=_in_ci()):
        return False

    # Lightweight read-only TCP+auth check via psycopg; runs no SQL or DDL.
    try:
        import psycopg  # noqa: WPS433

        with psycopg.connect(str(settings.database_url), connect_timeout=3):
            return True
    except Exception:
        return False


def _is_database_reachable() -> bool:
    return _is_database_reachable_in(ROOT_DIR)


# Build Alembic's view of a revision directory, or None when there is no such directory.
# script_location: Directory holding the revision files.
# Returns: An alembic ScriptDirectory, typed loosely because alembic is imported lazily.
# One construction shared by every offline reader below, so the heads, the revision ids and
# any future reader cannot end up walking two differently-configured views of the same directory.
def _script_directory_at(
    script_location: Path,
    *,
    alembic_ini: Path | None = None,
) -> Any:
    # ScriptDirectory reads alembic/versions/ and nothing else — env.py is not
    # executed, so no connection is attempted and no .env is needed. That is what lets this run
    # ahead of the reachability skip.
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    # The repository's alembic.ini is read so a project that adds
    # `version_locations` there is walked the same way the CLI walks it; only script_location is
    # overridden, because the ini spells it relative to the working directory and this gate does
    # not chdir.
    # No script directory means no revisions, not a broken graph — a project
    # that declared POSTGRES_ENABLED=false and deleted alembic/ has nothing here to check, and
    # the database_disabled skip below is the answer it should get.
    if not script_location.is_dir():
        return None
    ini_path = ALEMBIC_INI_PATH if alembic_ini is None else alembic_ini
    config = Config(str(ini_path)) if ini_path.is_file() else Config()
    config.set_main_option("script_location", str(script_location))
    return ScriptDirectory.from_config(config)


# Read the heads of the revision graph from the migration files alone, without a database.
# script_location: The Alembic script directory; the repository's unless a test
# points at a temporary one.
# Returns: Every head revision id. One is healthy; two is a fork.
# Exception: Whatever Alembic raises when the files do not form a graph it can walk — a
# down_revision naming no revision surfaces as KeyError, a revision file that fails to
# import as its own error. _revision_graph_issue reports either without narrowing.
def _revision_heads(script_location: Path = ROOT_DIR / "alembic") -> list[str]:
    script = _script_directory_at(script_location)
    return [] if script is None else list(script.get_heads())


def _revision_heads_in(root_dir: Path) -> list[str]:
    ini_path = root_dir / "alembic.ini"
    script = _script_directory_at(
        root_dir / "alembic",
        alembic_ini=ini_path if ini_path.is_file() else None,
    )
    return [] if script is None else list(script.get_heads())


# Every revision id this branch's migration files define, read without a database.
# script_location: The Alembic script directory; the repository's unless a test
# points at a temporary one.
# Returns: Revision ids reachable from any head down to base, empty when there are none.
# Heads answer "does this branch have one chain"; this answers "does this branch know that
# revision at all", which is the question a stamped database asks.
def _revision_ids(script_location: Path = ROOT_DIR / "alembic") -> set[str]:
    script = _script_directory_at(script_location)
    if script is None:
        return set()
    return {revision.revision for revision in script.walk_revisions()}


# Read what the database says it has been migrated to, or nothing when it cannot say.
# Returns: Contents of alembic_version.version_num; empty for a database that is
# unreachable, never migrated, or answering anything this cannot read.
# Every failure reads as "nothing stamped", which is the quiet direction on purpose: a
# connection that dies between the reachability probe and this read must not be reported as a
# foreign revision. The real failure then surfaces where it always did, in `alembic upgrade`.
def _stamped_revision_ids() -> set[str]:
    settings, config_error = _load_postgres_settings(ROOT_DIR)
    if config_error is not None or settings is None:
        return set()
    if not _has_probeable_database_target(settings, in_ci=_in_ci()):
        return set()
    try:
        import psycopg

        with psycopg.connect(str(settings.database_url), connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version_num FROM alembic_version")
                return {str(row[0]) for row in cursor.fetchall()}
    except Exception:
        return set()


# Report a database stamped with a revision this branch does not have.
# Returns: An error-severity issue, or None when the two agree.
def _foreign_revision_issue() -> MigrationIssue | None:
    stamped = _stamped_revision_ids()
    if not stamped:
        return None
    try:
        known = _revision_ids()
    except Exception:
        # An unwalkable graph is already reported as migrations.broken_revision_
        # graph; reporting it a second time under this rule would name the wrong cause.
        return None
    if not known:
        # No revisions here at all — a checkout whose alembic/versions/ is empty or
        # gone. Every stamped id is then "foreign" by arithmetic and by nothing else, and saying
        # another branch migrated this database would be a guess about the wrong repository. The
        # upgrade that follows reports the real problem, which is local.
        return None
    foreign = sorted(stamped - known)
    if not foreign:
        return None
    return MigrationIssue(
        rule_id="migrations.foreign_revision",
        command_name="current",
        message=(
            f"the database is stamped with {', '.join(foreign)}, which this branch's "
            "alembic/versions/ does not contain. Something else migrated it — most often "
            "another worktree of this repository. `alembic upgrade head` would fail here with "
            "`Can't locate revision identified by`, which says nothing about why."
        ),
        returncode=1,
    )


# Report a fork or an unwalkable revision graph, or None when the files form one chain.
# Returns: An error-severity issue, or None.
def _revision_graph_issue_from_heads(get_heads: Callable[[], list[str]]) -> MigrationIssue | None:
    try:
        heads = get_heads()
    except Exception as exc:
        return MigrationIssue(
            rule_id="migrations.broken_revision_graph",
            command_name="heads",
            message=(
                f"alembic/versions/ cannot be walked: {type(exc).__name__}: {exc}. "
                "A down_revision names a revision that does not exist, or a revision file "
                "does not import."
            ),
            returncode=1,
        )
    if len(heads) > 1:
        return MigrationIssue(
            rule_id="migrations.multiple_heads",
            command_name="heads",
            message=(
                f"alembic/versions/ has {len(heads)} heads ({', '.join(sorted(heads))}); "
                "`alembic upgrade head` needs exactly one. Point the newer down_revision at "
                "the other head, or add a merge revision."
            ),
            returncode=1,
        )
    return None


def _revision_graph_issue() -> MigrationIssue | None:
    return _revision_graph_issue_from_heads(_revision_heads)


def _revision_graph_issue_in(root_dir: Path) -> MigrationIssue | None:
    return _revision_graph_issue_from_heads(lambda: _revision_heads_in(root_dir))


# Build the ordered Alembic commands required to validate migration completeness.
# Returns: Upgrade and metadata-check commands executed by the gate.
def _build_commands() -> list[MigrationCommand]:
    alembic_prefix = (
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(ALEMBIC_INI_PATH),
    )
    return [
        MigrationCommand(
            name="upgrade-head",
            argv=(*alembic_prefix, "upgrade", "head"),
        ),
        MigrationCommand(
            name="check",
            argv=(*alembic_prefix, "check"),
        ),
    ]


# Print a stable progress line for the current migration validation step.
def _emit(message: str) -> None:
    sys.stdout.write(f"[validate_migrations] {message}\n")
    sys.stdout.flush()


# Map an Alembic command name to the corresponding rule_id when it fails.
def _command_to_rule_id(command_name: str) -> str:
    if command_name == "check":
        return "migrations.head_drift"
    if command_name == "upgrade-head":
        return "migrations.upgrade_failure"
    return "migrations.upgrade_failure"


# Build stable remediation guidance for a failed migration validation step.
# Returns: Ordered remediation hints to print after the failure.
def _remediation_messages(command_name: str) -> list[str]:
    if command_name == "check":
        return [
            "SQLAlchemy metadata and Alembic revisions appear out of sync.",
            "If you changed schema metadata, run `make autogenerate-migration MSG=describe_change` and review the generated revision.",
        ]
    if command_name == "upgrade-head":
        return [
            "Failed to upgrade the local database to the latest revision.",
            "Fix the broken migration or local database state before rerunning this validator.",
        ]
    if command_name == "heads":
        return [
            "The revision files under alembic/versions/ do not form a single chain.",
            "Run `uv run alembic -c alembic.ini heads` and `history`; no database is needed.",
        ]
    if command_name == "current":
        return [
            "This database was migrated by something whose revisions this branch does not have.",
            "Merge the branch that owns the revision named above, or give this worktree its own "
            "database with `make db-up-worktree`. Recreating the shared one destroys the schema "
            "another branch migrated.",
        ]
    if command_name == "precheck":
        return [
            "Rerun this gate alone: uv run python scripts/validate_migrations.py --require-database",
            "Playbook: uv run python scripts/doctor_ai_context.py --rule migrations.database_required",
        ]
    return [
        "Inspect the failing Alembic command output and correct the migration state before retrying."
    ]


# Maximum characters of captured Alembic stderr to retain on a MigrationIssue (tail-truncated).
_STDERR_TAIL_LIMIT = 1000


# Execute one Alembic validation step and fail fast on non-zero exit codes.
# subprocess.CalledProcessError: When the Alembic subprocess exits with a non-zero status. exc.stderr carries Alembic's stderr output (text mode).
def _run_command(command: MigrationCommand) -> None:
    _emit(f"running {command.name}: {' '.join(command.argv)}")
    subprocess.run(
        command.argv,
        cwd=ROOT_DIR,
        check=True,
        capture_output=True,
        text=True,
    )


# Format a blocking MigrationIssue into the exact diagnostic lines emitted by this gate and by
# verify_change's early preflight, keeping one wording for both.
def format_migration_issue_lines(issue: MigrationIssue) -> list[str]:
    if issue.rule_id == "migrations.database_required":
        lines = [f"{issue.rule_id}: {_REQUIRED_REASON}"]
        detail = issue.message.removeprefix(_REQUIRED_REASON).strip()
        if detail:
            lines.append(detail)
    else:
        lines = [f"{issue.command_name} failed: {issue.message}"]
    lines.extend(_remediation_messages(issue.command_name))
    return lines


# Read-only preflight shared by verify_change (before expensive stages) and collect_migration_issues.
# Walks the offline Alembic graph first, honours POSTGRES_ENABLED=false without connecting, and
# probes the effective PostgreSQL target without running any SQL, DDL, or migrations.
def collect_migration_precheck_issues(
    root_dir: Path | None = None,
    require_database: bool | None = None,
) -> list[MigrationIssue]:
    target_root = ROOT_DIR if root_dir is None else root_dir
    use_default_root = target_root.resolve() == ROOT_DIR.resolve()
    # The one part of this gate that needs no database runs first, ahead of both
    # skips below. A second head and a dangling down_revision are defects of the files in
    # alembic/versions/, readable without connecting — running this after the skips would let a
    # checkout without Postgres pass both green, although the comment on database_skip_is_allowed
    # names exactly these two as what the CI backstop exists for. Ahead of the
    # POSTGRES_ENABLED=false skip too: a project that uses no database today still ships its
    # revision files, and a fork in them is still a fork.
    graph_issue = (
        _revision_graph_issue() if use_default_root else _revision_graph_issue_in(target_root)
    )
    if graph_issue is not None:
        return [graph_issue]
    # A project that declared it needs no relational store has nothing for this
    # gate to verify. This is deliberately NOT the MIGRATIONS_ALLOW_SKIP path: that env var is
    # the emergency hatch for "the database is temporarily unreachable", and letting it also
    # cover "the database is not part of this project" would make one switch hide two very
    # different situations — the second permanently.
    if not (postgres_is_enabled() if use_default_root else postgres_is_enabled(target_root)):
        return [
            MigrationIssue(
                rule_id="migrations.database_disabled",
                command_name="precheck",
                message=(
                    "POSTGRES_ENABLED=false — this project declares no relational store, so "
                    "there are no migrations to verify."
                ),
                returncode=0,
                severity="info",
            )
        ]

    if require_database is None:
        require_database = not database_skip_is_allowed()
    reachable = (
        _is_database_reachable() if use_default_root else _is_database_reachable_in(target_root)
    )
    if not reachable:
        if require_database:
            action = (
                _database_required_action()
                if use_default_root
                else _database_required_action_in(target_root)
            )
            return [
                MigrationIssue(
                    rule_id="migrations.database_required",
                    command_name="precheck",
                    message=f"{_REQUIRED_REASON} {action}",
                    returncode=1,
                    severity="error",
                )
            ]
        return [
            MigrationIssue(
                rule_id="migrations.database_unreachable",
                command_name="precheck",
                message=(
                    f"{_NOT_VERIFIED_BANNER} — no database reachable, so `alembic upgrade head` "
                    "and `alembic check` did not run. Start one for this checkout: "
                    "`make db-up-worktree`. `make test-e2e` runs this same check against the "
                    "functional stack's database, where it cannot skip."
                ),
                returncode=0,
                severity="info",
            )
        ]
    return []


# Run the full migration check sequence and return a list of structured issues.
# Returns: Issues found. Empty list means the gate passed.
def collect_migration_issues(
    root_dir: Path,
    require_database: bool | None = None,
) -> list[MigrationIssue]:
    precheck_issues = collect_migration_precheck_issues(root_dir, require_database=require_database)
    if precheck_issues:
        return precheck_issues
    # Asked before alembic runs, because alembic's own answer to this is
    # `Can't locate revision identified by <id>` — true, useless, and the reason the advice
    # people reached for was to recreate the database. Reported instead of the upgrade failure
    # it would otherwise become, since the two want opposite moves.
    foreign_issue = _foreign_revision_issue()
    if foreign_issue is not None:
        return [foreign_issue]
    try:
        for command in _build_commands():
            _run_command(command)
    except subprocess.CalledProcessError as exc:
        failed_command = next(
            (command for command in _build_commands() if tuple(command.argv) == tuple(exc.cmd)),
            None,
        )
        failed_name = failed_command.name if failed_command is not None else "unknown"
        # Capture Alembic stderr (text mode) so JSON consumers see the
        # actionable error, not just an opaque exit code. Truncate to the tail to
        # keep MigrationIssue payloads bounded.
        raw_stderr = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
        if len(raw_stderr) > _STDERR_TAIL_LIMIT:
            stderr_tail = raw_stderr[-_STDERR_TAIL_LIMIT:]
        else:
            stderr_tail = raw_stderr
        message = f"{failed_name} failed with exit code {exc.returncode}"
        if stderr_tail:
            message = f"{message}; alembic stderr (tail): {stderr_tail}"
        return [
            MigrationIssue(
                rule_id=_command_to_rule_id(failed_name),
                command_name=failed_name,
                message=message,
                returncode=exc.returncode or 1,
                stderr=stderr_tail or None,
            )
        ]
    return []


# Convert a MigrationIssue to a JSON-serializable payload with playbook hints.
def _issue_to_payload(issue: MigrationIssue) -> dict[str, object]:
    # MigrationIssue has no native file/line — command_name is the closest
    # identifying location. Map it into the canon's `file` field with a neutral `line=1`
    # while keeping command_name/returncode/stderr/severity in the payload (asdict) unchanged.
    playbook = get_migrations_rule_playbook(issue.rule_id)
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category="migrations",
        file=issue.command_name,
        line=1,
        message=issue.message,
        severity=issue.severity,
        playbook=playbook,
        extra=asdict(issue),
    )


# Run the migration validation quality gate and return a process exit code.
# Returns: Zero when migrations are valid or skipped; non-zero on detected drift/failure.
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Alembic migrations against current SQLAlchemy metadata."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable validation results.",
    )
    parser.add_argument(
        "--require-database",
        action="store_true",
        help=(
            "Fail instead of skipping when the database is unreachable. Implied when CI=true, "
            f"where {_SKIP_OPT_OUT_ENV}=1 restores the skip; nothing overrides this flag."
        ),
    )
    # Default to an empty argv when called directly from tests so that
    # pytest's own argv does not leak into argparse.
    args = parser.parse_args([] if argv is None else argv)

    require_database = args.require_database or not database_skip_is_allowed()
    issues = collect_migration_issues(ROOT_DIR, require_database=require_database)
    # Treat any all-non-error batch as a skip (info/warning issues do not block).
    # See `docs/agent_rules.md` "Validator authoring conventions" for the severity contract.
    skipped = bool(issues) and all(issue.severity != "error" for issue in issues)

    if not issues:
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            _emit("migration validation passed")
        return 0

    if skipped:
        # Report the reason the issue actually carries. The old code printed
        # "database is not reachable" for every non-error status, which would have told a
        # project running without a relational store that its database was down.
        reason = issues[0].rule_id.split(".", 1)[-1]
        if args.json:
            print(
                render_json(
                    {
                        "status": "skipped",
                        "reason": reason,
                        "issues": [_issue_to_payload(issue) for issue in issues],
                    }
                ),
                end="",
            )
        else:
            for issue in issues:
                _emit(issue.message)
        return 0

    if args.json:
        print(
            render_json(
                {
                    "status": "error",
                    "issues": [_issue_to_payload(issue) for issue in issues],
                }
            ),
            end="",
        )
        return max(issue.returncode for issue in issues) or 1

    for issue in issues:
        for line in format_migration_issue_lines(issue):
            _emit(line)
    return max(issue.returncode for issue in issues) or 1


# Module entrypoint that executes the migration validation gate as a CLI script.
if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
