#!/usr/bin/env python3
# FILE: doctor_ai_context.py
# SUMMARY: Names the first blocking layer of `make quality-gates`, and answers `--rule <rule_id>` with its playbook.
# Every tool step (lockfile, lint, format, types, security, tests), every in-process validator and
# the agent-docs drift check, without mutating the repository.
# This module names every layer — tool steps and validators alike — in one place, because
# modelling only some of them lets it answer "doctor status: ok" while quality-gates is red; a
# doctor that leaves out the test run, mypy or ruff is worse than no doctor. The tool steps still
# shell out to `make gate-<name>` — the same targets quality-gates-steps runs — so the flags and
# source lists stay defined once, in the Makefile.

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from validation_support.rendering import render_json

# Import error text when a validator module could not be loaded, otherwise None.
# Every validator this doctor diagnoses is imported here, guarded, so a validator that is
# missing, renamed or syntactically broken is diagnosed rather than crashing the one tool whose job
# is to diagnose a broken repository: without this guard, deleting scripts/validate_cbm.py produces
# a raw ModuleNotFoundError, not a diagnosis. The guard also covers
# dependencies, secrets and test_quality — importing any of those three unconditionally would crash
# this module's own import instead of being diagnosed like everything else here.
UNAVAILABLE_VALIDATOR: str | None = None
try:
    from scripts.validate_architecture import (
        collect_architecture_issues,
        get_architecture_rule_playbook,
    )
    from scripts.validate_cbm import (
        classify_issue_rule_id,
        collect_validation_issues,
        get_cbm_rule_playbook,
    )
    from scripts.validate_dependencies import (
        collect_dependency_issues,
        get_dependencies_rule_playbook,
    )
    from scripts.validate_endpoint_wiring import (
        collect_endpoint_wiring_issues,
        get_endpoint_rule_playbook,
    )
    from scripts.validate_migrations import collect_migration_issues, get_migrations_rule_playbook
    from scripts.validate_module_sizes import collect_module_size_issues, get_module_size_playbook
    from scripts.validate_repository_metadata import (
        ProjectContextIssue,
        ScriptPathIssue,
        SkillFrontmatterIssue,
        collect_repository_metadata_issues,
        get_repository_metadata_rule_playbook,
    )
    from scripts.validate_runtime_ownership import (
        collect_runtime_ownership_issues,
        get_runtime_ownership_rule_playbook,
    )
    from scripts.validate_secrets import collect_secret_issues, get_secrets_rule_playbook
    from scripts.validate_test_quality import (
        collect_test_quality_issues,
        get_test_quality_rule_playbook,
    )
    from scripts.sync_agent_docs import get_agent_docs_rule_playbook
except ImportError as error:
    UNAVAILABLE_VALIDATOR = str(error)


ROOT_DIR = Path(__file__).resolve().parent.parent


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return path.as_posix()


# Reported when the doctor itself cannot load one of the validators it diagnoses.
LAYER_UNAVAILABLE_RULE_ID = "doctor.layer_unavailable"


# Build the doctor payload for a validator module that could not be imported.
# detail: The import error, naming the module that is missing or broken.
# Returns: Same payload shape every other layer returns.
def unavailable_validator_payload(detail: str) -> dict[str, object]:
    return {
        "status": "error",
        "blocking_layer": "doctor",
        "issues": [
            {
                "issue_type": "doctor_error",
                "rule_id": LAYER_UNAVAILABLE_RULE_ID,
                "category": "doctor",
                "file": "scripts/",
                "line": 1,
                "message": (
                    f"The doctor could not load a validator it diagnoses: {detail}. "
                    "Every layer below it went unchecked."
                ),
                "recommended_next_command": "git status --short scripts/",
                "likely_fix_shape": (
                    "Restore the named module from git, or fix the syntax error in it. Until then "
                    "no gate below that layer has been checked, whatever the rest of the run says."
                ),
                "stop_widening_condition": (
                    "Stop once `uv run python scripts/doctor_ai_context.py` reports a real layer "
                    "again."
                ),
            }
        ],
    }


# Pull the unreachable-database skip message out of a batch of migration issues.
# issues: Everything collect_migration_issues(ROOT_DIR) returned —
# errors, skips and disabled-store informational issues alike, unfiltered by severity.
# Returns: The skip's own message (carrying the MIGRATIONS NOT VERIFIED banner), or
# None when migrations were actually verified, POSTGRES_ENABLED=false, or a real error
# already short-circuited diagnose() before reaching the "ok" payload this feeds.
# Read from validate_migrations.py's own issue rather than re-worded here, so the wording an
# agent sees under `make doctor` and under `make quality-gates` is the same sentence, sourced once.
def _migrations_not_verified_notice(issues: Sequence[object]) -> str | None:
    for issue in issues:
        if getattr(issue, "rule_id", None) == "migrations.database_unreachable":
            return str(getattr(issue, "message", ""))
    return None


# Return the shape of the fix for a rule, or None when the rule carries no playbook.
def _fix_shape_for(rule_id: str) -> str | None:
    try:
        shape = failure_playbook(rule_id)["likely_fix_shape"]
    except KeyError:
        return None
    return str(shape) if shape else None


def _validator_issue_payload(
    *,
    issue_type: str,
    rule_id: str,
    category: str,
    file: str,
    line: int,
    message: str,
    playbook: dict[str, object] | None,
) -> dict[str, object]:
    return {
        "issue_type": issue_type,
        "rule_id": rule_id,
        "category": category,
        "file": file,
        "line": line,
        "message": message,
        "meaning": None if playbook is None else playbook["meaning"],
        "recommended_next_command": (
            "make quality-gates" if playbook is None else playbook["smallest_command_to_rerun"]
        ),
        "likely_fix_shape": None if playbook is None else playbook["likely_fix_shape"],
        "stop_widening_condition": (
            "Stop widening once the first blocking issue is resolved."
            if playbook is None
            else playbook["stop_widening_condition"]
        ),
    }


# ==================================================================================================
# Tool layers — the steps that shell out to `make gate-<name>` instead of calling Python in-process.
# One table serves both: quality-gates-steps calls the same targets, defined once in the Makefile,
# so there is no second table of ruff/mypy flags here to drift out of step with it.
# ==================================================================================================

# Set in every child process a tool layer spawns, and honoured on the way in.
# The tests layer runs the unit suite, and the unit suite exercises the doctor — without this the
# first `make doctor` on a clean tree recursed until it was killed. It is unforgeable only by
# convention: any shell that happens to export it disables five layers. Two things keep that from
# becoming a silent lie: the `doctor`/`doctor-json` make targets clear the variable on the way in,
# and whatever is skipped is named in `skipped_layers` and printed rather than folded into a bare
# "ok".
REENTRY_ENV_VAR = "MAYAK_DOCTOR_SUBPROCESS"


def tool_layers_enabled() -> bool:
    return os.environ.get(REENTRY_ENV_VAR) != "1"


# Remediation playbooks for the tool layers and the doctor's own unavailable-layer rule,
# keyed by the rule_id this module prints. Reachable through `doctor_ai_context.py --rule <id>`.
_GATE_RULE_PLAYBOOKS: dict[str, dict[str, object]] = {
    "gate.lockfile.stale": {
        "meaning": "uv.lock no longer matches pyproject.toml, so the environment is not reproducible.",
        "read_first": ["pyproject.toml", "uv.lock"],
        "smallest_command_to_rerun": "uv lock --check",
        "likely_fix_shape": (
            "Run `make update-deps` to regenerate uv.lock, then commit it alongside the "
            "pyproject.toml change that caused the drift."
        ),
        "next_checks": ["uv lock --check", "make quality-gates"],
        "stop_widening_condition": "Stop widening once `uv lock --check` exits 0.",
    },
    "gate.lint.failed": {
        "meaning": "ruff reported lint violations in the checked sources.",
        "read_first": ["pyproject.toml"],
        "smallest_command_to_rerun": "make gate-lint",
        "likely_fix_shape": (
            "Run `make ai-autofix`, which applies ruff's own fixes; repair by hand only what it "
            "leaves behind."
        ),
        "next_checks": ["make gate-lint", "make quality-gates"],
        "stop_widening_condition": "Stop widening once `make gate-lint` exits 0.",
    },
    "doctor.layer_unavailable": {
        "meaning": (
            "The doctor could not import one of the validators it diagnoses, so that layer and "
            "every layer below it went unchecked, reported here instead of as a raw traceback — "
            "a broken validator is exactly when a diagnosis is worth having."
        ),
        "read_first": ["scripts/doctor_ai_context.py", "the validator named in the message"],
        "smallest_command_to_rerun": "uv run python scripts/doctor_ai_context.py",
        "likely_fix_shape": (
            "Restore the named module from git, or fix the syntax error in it. Nothing below that "
            "layer has been checked until it imports again."
        ),
        "next_checks": ["uv run python scripts/doctor_ai_context.py", "make quality-gates"],
        "stop_widening_condition": "Stop once the doctor reports a real layer again.",
    },
    "gate.format.failed": {
        "meaning": "ruff format would rewrite at least one file, so the tree is not formatted.",
        "read_first": ["pyproject.toml"],
        "smallest_command_to_rerun": "make gate-format",
        "likely_fix_shape": "Run `make ai-autofix`. Formatting is never fixed by hand here.",
        "next_checks": ["make gate-format", "make quality-gates"],
        "stop_widening_condition": "Stop widening once `make gate-format` exits 0.",
    },
    "gate.types.failed": {
        "meaning": (
            "mypy reported type errors somewhere in MYPY_TARGETS: project, scripts, "
            "validation_support, alembic, or a test suite — tests/functional, tests/application, "
            "tests/infrastructure, tests/integration and tests/conftest.py are all in scope."
        ),
        "read_first": ["pyproject.toml", "AGENTS.md"],
        "smallest_command_to_rerun": "make gate-types",
        "likely_fix_shape": (
            "Fix the annotation or the call the error names. The test suites are in scope on "
            "purpose. A fake whose signature drifted from the Protocol it is annotated with fails "
            "here and nowhere else — pytest cannot see that at all."
        ),
        "next_checks": ["make gate-types", "make quality-gates"],
        "stop_widening_condition": "Stop widening once `make gate-types` exits 0.",
    },
    "gate.security.failed": {
        "meaning": (
            "bandit reported a medium-or-higher finding under project/. The gate runs it, so a "
            "green suite and a red security step are the same run."
        ),
        "read_first": ["the file named in the message", "Makefile"],
        "smallest_command_to_rerun": "make security-scan",
        "likely_fix_shape": (
            "Fix the finding. Suppress only what is genuinely safe, with `# nosec <id>` on the "
            "line bandit reports — a marker on the closing parenthesis of a multi-line statement "
            "suppresses nothing, which is how a real finding once looked handled."
        ),
        "next_checks": ["make security-scan", "make quality-gates"],
        "stop_widening_condition": "Stop widening once `make security-scan` exits 0.",
    },
    "gate.tests.failed": {
        "meaning": (
            "The local suites — unit, infrastructure and integration — failed; the code is "
            "broken, not the tooling."
        ),
        "read_first": ["AGENTS.md"],
        "smallest_command_to_rerun": "make test",
        "likely_fix_shape": (
            "Read the first failing assertion and fix the behaviour it names. Re-run the single "
            "test file before re-running the suite."
        ),
        "next_checks": ["make test", "make quality-gates"],
        "stop_widening_condition": "Stop widening once `make test` exits 0.",
    },
}


# Resolve the remediation playbook for a gate-layer rule_id, or None when unknown.
def get_doctor_layer_playbook(rule_id: str) -> dict[str, object] | None:
    return _GATE_RULE_PLAYBOOKS.get(rule_id)


# The playbook for any rule_id a gate step prints — the answer to "three failures running, what
# now?". Every validator's getter is asked in turn; in the template,
# tests/template/test_doctor_ai_context.py parametrises over every rule_id a validator declares, so
# a new rule without a playbook is red.
# Raises KeyError for a rule_id nothing declares.
def failure_playbook(rule_id: str) -> dict[str, object]:
    for getter in (
        get_doctor_layer_playbook,
        get_dependencies_rule_playbook,
        get_secrets_rule_playbook,
        get_test_quality_rule_playbook,
        get_architecture_rule_playbook,
        get_endpoint_rule_playbook,
        get_runtime_ownership_rule_playbook,
        get_cbm_rule_playbook,
        get_module_size_playbook,
        get_repository_metadata_rule_playbook,
        get_migrations_rule_playbook,
        get_agent_docs_rule_playbook,
    ):
        playbook = getter(rule_id)
        if playbook is None:
            continue
        return {
            "rule_id": rule_id,
            "meaning": playbook["meaning"],
            "smallest_files_to_read": list(playbook["read_first"]),
            "smallest_command_to_rerun": playbook["smallest_command_to_rerun"],
            "likely_fix_shape": playbook["likely_fix_shape"],
            "next_checks": list(playbook["next_checks"]),
            "stop_widening_condition": playbook["stop_widening_condition"],
        }
    raise KeyError(f"Unknown failure rule ID: {rule_id}")


# One gate step the doctor diagnoses by running the make target that owns it.
@dataclass(frozen=True, slots=True)
class ToolLayer:
    name: str
    make_target: str
    rule_id: str
    file: str


# The four steps quality-gates runs before any validator, in the same order.
TOOL_LAYERS: tuple[ToolLayer, ...] = (
    ToolLayer("lockfile", "gate-lockfile", "gate.lockfile.stale", "uv.lock"),
    ToolLayer("lint", "gate-lint", "gate.lint.failed", "pyproject.toml"),
    ToolLayer("format", "gate-format", "gate.format.failed", "pyproject.toml"),
    ToolLayer("types", "gate-types", "gate.types.failed", "pyproject.toml"),
)

# The bandit step (runs after the validators) and the final test step, each its own
# ToolLayer since both shell out to the same make target the gate itself calls.
SECURITY_LAYER = ToolLayer("security", "security-scan", "gate.security.failed", "project/")
TESTS_LAYER = ToolLayer("tests", "gate-tests", "gate.tests.failed", "tests/")

EARLY_LAYER_NAMES: tuple[str, ...] = tuple(layer.name for layer in TOOL_LAYERS)
LATE_LAYER_NAMES: tuple[str, ...] = (
    "test_quality",
    "dependencies",
    "secrets",
    SECURITY_LAYER.name,
    TESTS_LAYER.name,
)

# Shapes that carry the actual finding rather than a runner's banner: `FAILED tests/...`
# from pytest, `file.py:12: error: ...` from mypy/ruff, `would reformat: ...`, `>> Issue: [B608:...]`
# from bandit.
_DIAGNOSTIC_LINE = re.compile(
    r"^(FAILED |ERROR |E\s|>> Issue:|\S+:\d+[:\s]|would reformat|unformatted)", re.IGNORECASE
)


# Pick the most useful single line out of a tool's combined output.
# Two passes — a runner announces itself before it fails (pytest's first line is the command
# it is about to run), so the first non-framing line reported the banner as the diagnosis. Prefer a
# line that looks like a finding; fall back to the first ordinary line only when nothing does.
def _first_meaningful_line(output: str, fallback: str) -> str:
    candidates = [
        stripped
        for line in output.splitlines()
        if (stripped := line.strip())
        and not stripped.startswith("make[")
        and not stripped.startswith("make:")
    ]
    for candidate in candidates:
        if _DIAGNOSTIC_LINE.match(candidate):
            return candidate
    return candidates[0] if candidates else fallback


# Name the layers this process will not run, so an "ok" can never over-claim.
def skipped_layer_names() -> tuple[str, ...]:
    if tool_layers_enabled() and shutil.which("make") is not None:
        return ()
    return (*EARLY_LAYER_NAMES, SECURITY_LAYER.name, TESTS_LAYER.name)


# Run one gate target once and turn a non-zero exit into a doctor payload from that same
# result — never re-run to fetch the error after checking pass/fail separately.
# layer: The step to run.
# Returns: Issue payload when the step fails, otherwise None.
def diagnose_tool_layer(layer: ToolLayer) -> dict[str, object] | None:
    make = shutil.which("make")
    if make is None or not tool_layers_enabled():
        # Skip rather than fail: a checkout without make can still run the doctor directly, and
        # inside a process the doctor spawned, skipping is what stops the recursion.
        return None
    completed = subprocess.run(
        (make, "--no-print-directory", layer.make_target),
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, REENTRY_ENV_VAR: "1"},
    )
    if completed.returncode == 0:
        return None
    playbook = get_doctor_layer_playbook(layer.rule_id) or {}
    return {
        "status": "error",
        "blocking_layer": layer.name,
        "issues": [
            {
                "issue_type": f"{layer.name}_error",
                "rule_id": layer.rule_id,
                "category": "gate",
                "file": layer.file,
                "line": 1,
                "message": _first_meaningful_line(
                    completed.stdout + completed.stderr,
                    f"make {layer.make_target} exited with {completed.returncode}",
                ),
                "recommended_next_command": str(
                    playbook.get("smallest_command_to_rerun", f"make {layer.make_target}")
                ),
                # The recommended command for a gate layer is the gate itself — the one that just
                # failed. What actually moves the diff forward lives in likely_fix_shape.
                "likely_fix_shape": playbook.get("likely_fix_shape"),
                "stop_widening_condition": str(playbook.get("stop_widening_condition", "")),
            }
        ],
    }


# Keep the blocking issues out of a collector's mixed result, filtering by severity —
# never by rule_id literal, so a warning-severity issue never becomes a blocking layer by accident.
def _errors_only(issues: Sequence[object]) -> list[object]:
    return [issue for issue in issues if getattr(issue, "severity", "error") == "error"]


def _collector_payload(
    *,
    blocking_layer: str,
    rule_id: str,
    file: str,
    line: int,
    message: str,
    playbook: dict[str, object] | None,
) -> dict[str, object]:
    guidance = playbook or {}
    return {
        "status": "error",
        "blocking_layer": blocking_layer,
        "issues": [
            {
                "issue_type": f"{blocking_layer}_error",
                "rule_id": rule_id,
                "category": blocking_layer,
                "file": file,
                "line": line,
                "message": message,
                "recommended_next_command": str(
                    guidance.get("smallest_command_to_rerun", "make quality-gates")
                ),
                "likely_fix_shape": guidance.get("likely_fix_shape"),
                "stop_widening_condition": str(guidance.get("stop_widening_condition", "")),
            }
        ],
    }


def _diagnose_test_quality() -> dict[str, object] | None:
    issues = _errors_only(collect_test_quality_issues(ROOT_DIR))
    if not issues:
        return None
    issue = issues[0]
    return _collector_payload(
        blocking_layer="test_quality",
        rule_id=issue.rule_id,
        file=Path(issue.path).relative_to(ROOT_DIR).as_posix(),
        line=issue.line,
        message=issue.message,
        playbook=get_test_quality_rule_playbook(issue.rule_id),
    )


def _diagnose_dependencies() -> dict[str, object] | None:
    issues = _errors_only(collect_dependency_issues(ROOT_DIR))
    if not issues:
        return None
    issue = issues[0]
    return _collector_payload(
        blocking_layer="dependencies",
        rule_id=issue.rule_id,
        file=Path(issue.path).relative_to(ROOT_DIR).as_posix(),
        line=issue.line,
        message=issue.message,
        playbook=get_dependencies_rule_playbook(issue.rule_id),
    )


def _diagnose_secrets() -> dict[str, object] | None:
    issues = _errors_only(collect_secret_issues(ROOT_DIR))
    if not issues:
        return None
    issue = issues[0]
    return _collector_payload(
        blocking_layer="secrets",
        rule_id=issue.rule_id,
        file=issue.source_file,
        line=issue.line,
        message=issue.message,
        playbook=get_secrets_rule_playbook(issue.rule_id),
    )


def _diagnose_security() -> dict[str, object] | None:
    return diagnose_tool_layer(SECURITY_LAYER)


def _diagnose_tests() -> dict[str, object] | None:
    # Actually run them — "every layer I check is clean, so it must be the tests" was measured and
    # rejected: eight quality-gates steps sit outside the layers this module models in-process, and
    # `make doctor` is also a standalone command with no evidence that gates just failed.
    return diagnose_tool_layer(TESTS_LAYER)


_LATE_LAYERS: tuple[Callable[[], dict[str, object] | None], ...] = (
    _diagnose_test_quality,
    _diagnose_dependencies,
    _diagnose_secrets,
    _diagnose_security,
    _diagnose_tests,
)


# Diagnose the four tool steps quality-gates runs before any validator.
# Returns: First failing payload (or None) and
# the names of the layers that actually ran, so `checked_layers` never over-claims.
def diagnose_early_layers() -> tuple[dict[str, object] | None, tuple[str, ...]]:
    if not tool_layers_enabled():
        return None, ()
    executed: list[str] = []
    for layer in TOOL_LAYERS:
        payload = diagnose_tool_layer(layer)
        executed.append(layer.name)
        if payload is not None:
            return payload, tuple(executed)
    return None, tuple(executed)


# Diagnose the validators and the test suite: test_quality, dependencies, secrets,
# security, tests.
def diagnose_late_layers() -> tuple[dict[str, object] | None, tuple[str, ...]]:
    executed: list[str] = []
    for name, diagnose_step in zip(LATE_LAYER_NAMES, _LATE_LAYERS):
        # Both tool layers here shell out to make, so both are skipped inside a process the doctor
        # spawned — that is what stops the recursion.
        if name in (SECURITY_LAYER.name, TESTS_LAYER.name) and not tool_layers_enabled():
            continue
        payload = diagnose_step()
        executed.append(name)
        if payload is not None:
            return payload, tuple(executed)
    return None, tuple(executed)


# ==================================================================================================
# Validator layers — in-process collector calls. One function drives the five whose payload shape
# is uniform (collect issues, keep the errors, take the first, resolve its playbook); cbm,
# module_size and migrations stay bespoke below because each needs something that shape cannot
# express — a derived rule_id, a self-describing message, or the unfiltered batch for a notice.
# ==================================================================================================


def _path_issue(issue: Any) -> tuple[str, int, str]:
    return _display_path(issue.path), issue.line, issue.message


def _repository_metadata_locate(issue: Any) -> tuple[str, int, str]:
    if isinstance(issue, ScriptPathIssue):
        return issue.source_file, issue.line, issue.message
    if isinstance(issue, SkillFrontmatterIssue):
        return _display_path(issue.path), 1, issue.message
    assert isinstance(issue, ProjectContextIssue)
    return "docs/project_context.json", 1, f"[{issue.field}] {issue.message}"


# Collect, filter to errors, and report the first blocking issue for one in-process
# validator. Takes the collector's own result and the playbook getter as plain callables — resolved
# by the caller as bare names, so a test that monkeypatches "scripts.doctor_ai_context.collect_x"
# still reaches this, the same late-binding the rest of this module relies on.
def _diagnose_validator_layer(
    name: str,
    raw_issues: Sequence[Any],
    locate: Callable[[Any], tuple[str, int, str]],
    playbook_for: Callable[[str], dict[str, object] | None],
) -> dict[str, object] | None:
    issues = _errors_only(raw_issues)
    if not issues:
        return None
    issue = issues[0]
    file, line, message = locate(issue)
    return {
        "status": "error",
        "blocking_layer": name,
        "issues": [
            _validator_issue_payload(
                issue_type=f"{name}_error",
                rule_id=issue.rule_id,
                category=name,
                file=file,
                line=line,
                message=message,
                playbook=playbook_for(issue.rule_id),
            )
        ],
    }


def diagnose() -> dict[str, object]:
    if UNAVAILABLE_VALIDATOR is not None:
        return unavailable_validator_payload(UNAVAILABLE_VALIDATOR)

    for layer_name, collect, playbook_for in (
        ("architecture", collect_architecture_issues, get_architecture_rule_playbook),
        ("endpoint_wiring", collect_endpoint_wiring_issues, get_endpoint_rule_playbook),
        (
            "runtime_ownership",
            collect_runtime_ownership_issues,
            get_runtime_ownership_rule_playbook,
        ),
    ):
        payload = _diagnose_validator_layer(
            layer_name, collect(ROOT_DIR), _path_issue, playbook_for
        )
        if payload is not None:
            return payload

    cbm_issues = collect_validation_issues(ROOT_DIR)
    if cbm_issues:
        issue = cbm_issues[0]
        # Derive the real rule_id from the issue message so the agent routes to the
        # correct playbook, not a hard-coded literal that masks which header rule fired.
        cbm_rule_id = classify_issue_rule_id(issue.message)
        cbm_playbook = get_cbm_rule_playbook(cbm_rule_id) or {}
        return {
            "status": "error",
            "blocking_layer": "cbm",
            "issues": [
                {
                    "issue_type": "cbm_error",
                    "rule_id": cbm_rule_id,
                    "category": "cbm",
                    "file": _display_path(issue.path),
                    "line": issue.line,
                    "message": issue.message,
                    "recommended_next_command": "uv run python scripts/validate_cbm.py",
                    "likely_fix_shape": cbm_playbook.get("likely_fix_shape"),
                    "stop_widening_condition": (
                        "Stop widening once validate_cbm.py passes for the edited file set."
                    ),
                }
            ],
        }

    module_size_issues = collect_module_size_issues(ROOT_DIR)
    if module_size_issues:
        issue = module_size_issues[0]
        return {
            "status": "error",
            "blocking_layer": "module_size",
            "issues": [
                {
                    "issue_type": "module_size_error",
                    "rule_id": issue.rule_id,
                    "category": "module_size",
                    "file": issue.path.as_posix(),
                    "line": issue.line,
                    # One wording, produced by the issue itself — rebuilding the sentence here
                    # would drift silently the moment the budget grows a second unit.
                    "message": issue.describe(),
                    "recommended_next_command": "uv run python scripts/validate_module_sizes.py",
                    "likely_fix_shape": (get_module_size_playbook(issue.rule_id) or {}).get(
                        "likely_fix_shape"
                    ),
                    "stop_widening_condition": (
                        "Stop widening once validate_module_sizes.py passes."
                    ),
                }
            ],
        }

    # One doctor layer for skills_frontmatter, project_context and script_paths
    # together — their validators live in scripts/validate_repository_metadata.py, and the
    # Makefile runs one quality-gates step for all three, so the doctor names one blocking layer
    # for it too.
    payload = _diagnose_validator_layer(
        "repository_metadata",
        collect_repository_metadata_issues(ROOT_DIR),
        _repository_metadata_locate,
        get_repository_metadata_rule_playbook,
    )
    if payload is not None:
        return payload

    # Filter informational issues (e.g. DB-unreachable skip) by severity rather than
    # rule_id literal. The unfiltered list is kept (not discarded) so the "ok" payload below can
    # still say a skip happened — see _migrations_not_verified_notice.
    all_migration_issues = collect_migration_issues(ROOT_DIR)
    migration_issues = [issue for issue in all_migration_issues if issue.severity == "error"]
    if migration_issues:
        issue = migration_issues[0]
        playbook = get_migrations_rule_playbook(issue.rule_id)
        return {
            "status": "error",
            "blocking_layer": "migrations",
            "issues": [
                _validator_issue_payload(
                    issue_type="migrations_error",
                    rule_id=issue.rule_id,
                    category="migrations",
                    file="alembic/",
                    line=1,
                    message=f"[{issue.command_name}] {issue.message}",
                    playbook=playbook,
                )
            ],
        }

    # The one generator left: the agent wrappers rendered from docs/agent_rules.md.
    drift_check = _diagnose_drift_layer(
        layer="agent_docs_drift",
        blocking_layer="agent_docs_drift",
        command=("scripts/sync_agent_docs.py", "--check"),
        rule_id="drift.agent_docs.outdated",
        target="AGENTS.md",
    )
    if drift_check is not None:
        return drift_check

    ok_payload: dict[str, object] = {
        "status": "ok",
        "checked_layers": [
            "architecture",
            "endpoint_wiring",
            "runtime_ownership",
            "cbm",
            "module_size",
            "repository_metadata",
            "migrations",
            "agent_docs_drift",
        ],
        "final_gate": "make quality-gates",
    }
    # "migrations" stays in checked_layers whenever the database was simply
    # unreachable — that skip is not an error and must not make "ok" a lie either.
    migrations_notice = _migrations_not_verified_notice(all_migration_issues)
    if migrations_notice is not None:
        ok_payload["migrations_notice"] = migrations_notice
    return ok_payload


# Every layer `make quality-gates` runs — the tool steps around `diagnose()`'s validators.
# Returns: Same payload shape as diagnose(), with checked_layers widened.
# Separate from diagnose() on purpose — diagnose() is exercised directly by many tests in the
# template's tests/template/test_doctor_ai_context.py, and the tests layer runs that suite; folding
# the two together made the first clean run recurse.
def diagnose_full() -> dict[str, object]:
    early_failure, early_layers = diagnose_early_layers()
    if early_failure is not None:
        return early_failure

    payload = diagnose()
    if payload["status"] != "ok":
        return payload

    late_failure, late_layers = diagnose_late_layers()
    if late_failure is not None:
        return late_failure

    checked = payload.get("checked_layers")
    payload["checked_layers"] = [
        *early_layers,
        *(checked if isinstance(checked, list) else []),
        *late_layers,
    ]
    skipped = skipped_layer_names()
    if skipped:
        payload["skipped_layers"] = list(skipped)
    return payload


# Run a generator script in `--check` mode and convert non-zero exit into a doctor payload.
def _diagnose_drift_layer(
    *,
    layer: str,
    blocking_layer: str,
    command: tuple[str, ...],
    rule_id: str,
    target: str,
) -> dict[str, object] | None:
    del layer  # Reserved for future layer-specific routing; kept for symmetry with checked_layers.
    script_path = ROOT_DIR / command[0]
    if not script_path.exists():
        return None
    full_command = (sys.executable, str(script_path), *command[1:])
    completed = subprocess.run(
        full_command, cwd=ROOT_DIR, capture_output=True, text=True, check=False
    )
    if completed.returncode == 0:
        return None
    output = (completed.stdout + completed.stderr).strip()
    snippet = (
        output.splitlines()[0]
        if output
        else f"{command[0]} --check exited with {completed.returncode}"
    )
    return {
        "status": "error",
        "blocking_layer": blocking_layer,
        "issues": [
            {
                "issue_type": f"{blocking_layer}_error",
                "rule_id": rule_id,
                "category": "drift",
                "file": target,
                "line": 1,
                "message": snippet,
                "recommended_next_command": "make refresh-agent-docs",
                "likely_fix_shape": _fix_shape_for(rule_id),
                "stop_widening_condition": (f"Stop widening once `{' '.join(command)}` exits 0."),
            }
        ],
    }


# Print the playbook for one rule_id without running any gate. Returns the exit code.
def _print_rule(rule_id: str, as_json: bool) -> int:
    try:
        playbook = failure_playbook(rule_id)
    except KeyError as error:
        print(error.args[0], file=sys.stderr)
        return 2
    if as_json:
        print(render_json(playbook), end="")
        return 0
    for key, value in playbook.items():
        shown = ", ".join(map(str, value)) if isinstance(value, list) else value
        print(f"{key}: {shown}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose the first blocking quality-gates layer without mutating the repository."
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    parser.add_argument(
        "--rule", metavar="RULE_ID", help="Print the playbook for one rule_id; run no gate."
    )
    args = parser.parse_args()
    if args.rule:
        return _print_rule(args.rule, args.json)

    payload = diagnose_full()
    if args.json:
        print(render_json(payload), end="")
    elif payload["status"] == "ok":
        skipped = payload.get("skipped_layers")
        if isinstance(skipped, list) and skipped:
            print(
                f"doctor status: ok — but {len(skipped)} layer(s) were NOT run: "
                + ", ".join(skipped)
            )
            print(f"  cause: {REENTRY_ENV_VAR} is set in this environment, or make is unavailable")
            print(f"  to check everything: unset {REENTRY_ENV_VAR} and re-run `make doctor`")
        else:
            print("doctor status: ok")
        # Printed whether or not skipped_layers fired above — this is a different gap: that one
        # means the doctor ran fewer layers, this means migrations ran but could not verify.
        migrations_notice = payload.get("migrations_notice")
        if isinstance(migrations_notice, str) and migrations_notice:
            print(f"  note: {migrations_notice}")
        print("final_gate: make quality-gates")
    else:
        print("doctor status: error")
        print(f"blocking_layer: {payload['blocking_layer']}")
        issue = payload["issues"][0]
        print(f"message: {issue['message']}")
        # The fix shape is printed above the command on purpose — for every gate
        # layer the recommended command IS the command that just failed.
        fix_shape = issue.get("likely_fix_shape")
        if fix_shape:
            print(f"fix: {fix_shape}")
        print(f"next: {issue['recommended_next_command']}")
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
