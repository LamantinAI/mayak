#!/usr/bin/env python3
# FILE: validate_migrations.py
# SUMMARY: Quality gate that verifies Alembic migrations are up to date with the SQLAlchemy metadata.

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from ai_context.rendering import render_json
from ai_context.validator_contract import build_validator_issue_payload


# ATTRIBUTE: ROOT_DIR (Path)
# SUMMARY: Absolute repository root used as the working directory for Alembic commands.
ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: ALEMBIC_INI_PATH (Path)
# SUMMARY: Explicit Alembic configuration file path passed to every CLI invocation.
ALEMBIC_INI_PATH = ROOT_DIR / "alembic.ini"


# ATTRIBUTE: _MIGRATIONS_RULE_PLAYBOOKS (dict[str, dict[str, object]])
# SUMMARY: Stable rule_id → playbook mapping for migration validator failures.
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
            "Alembic upgrade head failed against the local database. A previous revision is "
            "broken, the database state is inconsistent, or required objects are missing."
        ),
        "suggested_fix": (
            "Inspect the failing revision file, fix the upgrade op (or restore the database "
            "to a consistent baseline), and rerun the validator."
        ),
        "read_first": [
            "alembic/versions/",
            "alembic.ini",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_migrations.py",
        "likely_fix_shape": (
            "Repair the failing Alembic revision or reset the local DB to a consistent state."
        ),
        "next_checks": [
            "uv run python scripts/validate_migrations.py",
        ],
        "stop_widening_condition": (
            "Stop once `alembic upgrade head` succeeds against the local database."
        ),
    },
    "migrations.database_unreachable": {
        "meaning": (
            "Alembic could not connect to the configured database. Validator skipped without "
            "actually verifying migrations — common in dev/CI environments without Postgres."
        ),
        "suggested_fix": (
            "Either start a local Postgres (`docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d db`) and rerun, "
            "or accept the skip — `migrations.database_unreachable` is non-fatal by design."
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
            "CLAUDE.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_migrations.py",
        "likely_fix_shape": "No change expected; flip POSTGRES_ENABLED only if the project really uses a database.",
        "next_checks": [
            "uv run python scripts/query_ai_context.py overview",
        ],
        "stop_widening_condition": (
            "Stop immediately — this status is informational and blocks nothing."
        ),
    },
    "migrations.database_required": {
        "meaning": (
            "Alembic could not connect to the configured database while the validator was "
            "required to actually verify migrations (CI, or --require-database). Skipping here "
            "would let a broken revision chain reach main with a green pipeline."
        ),
        "suggested_fix": (
            "Provision Postgres for this run — a `services: postgres` block in the CI job, or "
            "`docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d db` locally — and rerun the validator."
        ),
        "read_first": [
            ".github/workflows/ci.yml",
            "docker-compose.yml",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_migrations.py",
        "likely_fix_shape": (
            "Add or repair the Postgres service and the POSTGRES_* environment for this job."
        ),
        "next_checks": [
            "uv run python scripts/validate_migrations.py",
        ],
        "stop_widening_condition": (
            "Stop once the validator actually runs `alembic upgrade head` and `alembic check`."
        ),
    },
}


# ATTRIBUTE: _SKIP_OPT_OUT_ENV (str)
# SUMMARY: Environment variable that re-permits the database skip inside CI for deliberate exceptions.
_SKIP_OPT_OUT_ENV = "MIGRATIONS_ALLOW_SKIP"


# FUNCTION: postgres_is_enabled
# SUMMARY: Report whether this project uses a relational store at all.
# OUTPUT: (bool): False when POSTGRES_ENABLED is explicitly disabled in the environment or .env.
def postgres_is_enabled() -> bool:
    # **LOGIC_STEP**: Read the same variable the runtime reads, without importing the settings
    # models — this script must stay runnable when application dependencies are unavailable,
    # which is also why _is_database_reachable() loads dotenv by hand a few lines below.
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT_DIR / ".env")
    except ImportError:
        pass
    return os.environ.get("POSTGRES_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }


# FUNCTION: database_skip_is_allowed
# SUMMARY: Report whether an unreachable database may downgrade this gate to an informational skip.
# OUTPUT: (bool): False inside CI, where a silent skip would hide a broken revision chain.
def database_skip_is_allowed() -> bool:
    # **LOGIC_STEP**: Locally a developer without Postgres should not be blocked. In CI the
    # skip is the whole problem: no job provisioned a database, so the gate never ran and a
    # second alembic head or a dangling down_revision would sail through green.
    if os.environ.get(_SKIP_OPT_OUT_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return True
    return os.environ.get("CI", "").strip().lower() not in {"1", "true", "yes"}


# DATACLASS: validate_migrations.MigrationCommand
# SUMMARY: Immutable descriptor for a single Alembic validation step.
@dataclass(frozen=True)
class MigrationCommand:
    # ATTRIBUTE: name (str)
    # SUMMARY: Human-readable command label shown in progress output.
    name: str

    # ATTRIBUTE: argv (tuple[str, ...])
    # SUMMARY: Fully expanded command argv executed without a shell.
    argv: tuple[str, ...]


# DATACLASS: validate_migrations.MigrationIssue
# SUMMARY: Structured result of a failed migration validation step, suitable for JSON output and playbook lookup.
@dataclass(slots=True)
class MigrationIssue:
    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Stable rule identifier consumable by query_ai_context.py failure rule.
    rule_id: str

    # ATTRIBUTE: command_name (str)
    # SUMMARY: Name of the failed Alembic step (upgrade-head, check, ...).
    command_name: str

    # ATTRIBUTE: message (str)
    # SUMMARY: Human-readable description of the failure.
    message: str

    # ATTRIBUTE: returncode (int)
    # SUMMARY: Subprocess exit code. Zero indicates a non-failure status (skip).
    returncode: int

    # ATTRIBUTE: severity (str)
    # SUMMARY: 'error' (blocks gate), 'warning' (visible but non-blocking), or 'info' (purely informational).
    severity: str = "error"

    # ATTRIBUTE: stderr (str | None)
    # SUMMARY: Captured stderr output from the failed Alembic subprocess (truncated). None when not applicable or no stderr was emitted.
    stderr: str | None = None


# FUNCTION: get_migrations_rule_playbook
# SUMMARY: Return a copy of the playbook for a migrations rule_id, or None if unknown.
def get_migrations_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _MIGRATIONS_RULE_PLAYBOOKS.get(rule_id)
    if playbook is None:
        return None
    return dict(playbook)


# FUNCTION: _is_database_reachable
# SUMMARY: Check whether the PostgreSQL database is reachable using psycopg.
# OUTPUT: (bool): True when the database accepts connections, False otherwise.
def _is_database_reachable() -> bool:
    # Load .env so that POSTGRES_* variables are available.
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT_DIR / ".env")
    except ImportError:
        pass

    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    db = os.environ.get("POSTGRES_DB", "")

    # **LOGIC_STEP**: No user at all means there is nothing to try; an unconfigured checkout gets
    # its answer without waiting for a TCP timeout. The placeholder value itself is NOT a reason
    # to answer no. `your_postgres_user` used to short-circuit to False, and the functional stack
    # creates its database with exactly that name — compose passes POSTGRES_USER straight to the
    # postgres image — so the one environment in this repository that HAS a database was the one
    # environment where this function said it had none, and the migration gate skipped there too.
    if not user:
        return False

    # Try a lightweight TCP+auth check via psycopg.
    try:
        import psycopg  # noqa: WPS433

        with psycopg.connect(
            host=host,
            port=int(port),
            user=user,
            password=password,
            dbname=db,
            connect_timeout=3,
        ):
            return True
    except Exception:
        return False


# FUNCTION: _build_commands
# SUMMARY: Build the ordered Alembic commands required to validate migration completeness.
# OUTPUT: (list[MigrationCommand]): Upgrade and metadata-check commands executed by the gate.
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


# FUNCTION: _emit
# SUMMARY: Print a stable progress line for the current migration validation step.
def _emit(message: str) -> None:
    sys.stdout.write(f"[validate_migrations] {message}\n")
    sys.stdout.flush()


# FUNCTION: _command_to_rule_id
# SUMMARY: Map an Alembic command name to the corresponding rule_id when it fails.
def _command_to_rule_id(command_name: str) -> str:
    if command_name == "check":
        return "migrations.head_drift"
    if command_name == "upgrade-head":
        return "migrations.upgrade_failure"
    return "migrations.upgrade_failure"


# FUNCTION: _remediation_messages
# SUMMARY: Build stable remediation guidance for a failed migration validation step.
# OUTPUT: (list[str]): Ordered remediation hints to print after the failure.
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
    return [
        "Inspect the failing Alembic command output and correct the migration state before retrying."
    ]


# ATTRIBUTE: _STDERR_TAIL_LIMIT (int)
# SUMMARY: Maximum characters of captured Alembic stderr to retain on a MigrationIssue (tail-truncated).
_STDERR_TAIL_LIMIT = 1000


# FUNCTION: _run_command
# SUMMARY: Execute one Alembic validation step and fail fast on non-zero exit codes.
# RAISES: subprocess.CalledProcessError: When the Alembic subprocess exits with a non-zero status. exc.stderr carries Alembic's stderr output (text mode).
def _run_command(command: MigrationCommand) -> None:
    _emit(f"running {command.name}: {' '.join(command.argv)}")
    subprocess.run(
        command.argv,
        cwd=ROOT_DIR,
        check=True,
        capture_output=True,
        text=True,
    )


# FUNCTION: collect_migration_issues
# SUMMARY: Run the full migration check sequence and return a list of structured issues.
# OUTPUT: (list[MigrationIssue]): Issues found. Empty list means the gate passed.
def collect_migration_issues(
    root_dir: Path,
    require_database: bool | None = None,
) -> list[MigrationIssue]:
    del (
        root_dir
    )  # _build_commands uses module-level ROOT_DIR; preserved for symmetry with other validators.
    # **LOGIC_STEP**: A project that declared it needs no relational store has nothing for this
    # gate to verify. This is deliberately NOT the MIGRATIONS_ALLOW_SKIP path: that env var is
    # the emergency hatch for "the database is temporarily unreachable", and letting it also
    # cover "the database is not part of this project" would make one switch hide two very
    # different situations — the second permanently.
    if not postgres_is_enabled():
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
    if not _is_database_reachable():
        if require_database:
            return [
                MigrationIssue(
                    rule_id="migrations.database_required",
                    command_name="precheck",
                    message=(
                        "Database is not reachable, but this run requires it — migrations were "
                        "not verified. Provision Postgres for this job or set "
                        f"{_SKIP_OPT_OUT_ENV}=1 to accept an unverified run."
                    ),
                    returncode=1,
                    severity="error",
                )
            ]
        return [
            MigrationIssue(
                rule_id="migrations.database_unreachable",
                command_name="precheck",
                message=(
                    "Database is not reachable — migration validation skipped. "
                    "`make test-e2e` runs this same check against the functional stack's "
                    "database, where it cannot skip."
                ),
                returncode=0,
                severity="info",
            )
        ]
    try:
        for command in _build_commands():
            _run_command(command)
    except subprocess.CalledProcessError as exc:
        failed_command = next(
            (command for command in _build_commands() if tuple(command.argv) == tuple(exc.cmd)),
            None,
        )
        failed_name = failed_command.name if failed_command is not None else "unknown"
        # **LOGIC_STEP**: Capture Alembic stderr (text mode) so JSON consumers see the
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


# FUNCTION: _issue_to_payload
# SUMMARY: Convert a MigrationIssue to a JSON-serializable payload with playbook hints.
def _issue_to_payload(issue: MigrationIssue) -> dict[str, object]:
    # **LOGIC_STEP**: MigrationIssue has no native file/line — command_name is the closest
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


# FUNCTION: main
# SUMMARY: Run the migration validation quality gate and return a process exit code.
# OUTPUT: (int): Zero when migrations are valid or skipped; non-zero on detected drift/failure.
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
            "Fail instead of skipping when the database is unreachable. Implied when CI=true; "
            f"override with {_SKIP_OPT_OUT_ENV}=1."
        ),
    )
    # **LOGIC_STEP**: Default to an empty argv when called directly from tests so that
    # pytest's own argv does not leak into argparse.
    args = parser.parse_args([] if argv is None else argv)

    require_database = args.require_database or not database_skip_is_allowed()
    issues = collect_migration_issues(ROOT_DIR, require_database=require_database)
    # **LOGIC_STEP**: Treat any all-non-error batch as a skip (info/warning issues do not block).
    # See `docs/agent_rules.md` "Validator authoring conventions" for the severity contract.
    skipped = bool(issues) and all(issue.severity != "error" for issue in issues)

    if not issues:
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            _emit("migration validation passed")
        return 0

    if skipped:
        # **LOGIC_STEP**: Report the reason the issue actually carries. The old code printed
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
        _emit(f"{issue.command_name} failed: {issue.message}")
        for message in _remediation_messages(issue.command_name):
            _emit(message)
    return max(issue.returncode for issue in issues) or 1


# FUNCTION: __main__
# SUMMARY: Module entrypoint that executes the migration validation gate as a CLI script.
if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
