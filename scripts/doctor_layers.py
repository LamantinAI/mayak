#!/usr/bin/env python3
# FILE: scripts/doctor_layers.py
# SUMMARY: The gate layers `doctor_ai_context.py` did not model — the four tool steps that run
# before any validator, the four pure-Python validators that run after them, and the test suite —
# so `make doctor` can name the blocking layer for every step `make quality-gates` runs.
#
# NOTE: This module exists because the doctor answered "doctor status: ok" while quality-gates was
# red. It modelled 14 of the 23 steps, and the nine it did not model included the test run, mypy
# and ruff. `make quality-gates` is documented as the way to find out what blocks you,
# and on a plain failing test it said nothing was wrong. Reproduced twice, on two different
# failures.
#
# The tool layers shell out to `make gate-<name>`, the same targets `quality-gates` itself runs, so
# the flags and the source lists have one definition. Copying `ruff check $(PYTHON_SOURCES)` into
# Python would have created exactly the second copy this repository keeps getting bitten by.

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from scripts.validate_dependencies import collect_dependency_issues, get_dependencies_rule_playbook
from scripts.validate_secrets import collect_secret_issues, get_secrets_rule_playbook
from scripts.validate_test_quality import (
    collect_test_quality_issues,
    get_test_quality_rule_playbook,
)

# ATTRIBUTE: ROOT_DIR (Path)
# SUMMARY: Absolute repository root, resolved the same way doctor_ai_context.py resolves it.
ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: _GATE_RULE_PLAYBOOKS (dict[str, dict[str, object]])
# SUMMARY: Remediation playbooks for the tool layers, keyed by the rule_id the doctor prints.
# Registered here and reachable through `query_ai_context.py failure rule <id>` — a printed
# identifier that resolves to nothing is its own defect, already paid for once.
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
            "every layer below it went unchecked. Reported instead of the traceback this used to "
            "produce, because a broken validator is exactly when a diagnosis is worth having."
        ),
        "read_first": [
            "scripts/doctor_ai_context.py",
            "the validator named in the message",
        ],
        "smallest_command_to_rerun": "uv run python scripts/doctor_ai_context.py",
        "likely_fix_shape": (
            "Restore the named module from git, or fix the syntax error in it. Nothing below that "
            "layer has been checked until it imports again."
        ),
        "next_checks": [
            "uv run python scripts/doctor_ai_context.py",
            "make quality-gates",
        ],
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
            "mypy reported type errors somewhere in MYPY_TARGETS: project, scripts, ai_context, "
            "ai_query, alembic, or a test suite — tests/functional, tests/application, "
            "tests/infrastructure, tests/integration and tests/conftest.py are all in scope."
        ),
        "read_first": ["pyproject.toml", "CLAUDE.md"],
        "smallest_command_to_rerun": "make gate-types",
        "likely_fix_shape": (
            "Fix the annotation or the call the error names. The test suites are in scope on "
            "purpose. A fake whose signature drifted from the Protocol it is annotated with "
            "fails here and nowhere else — pytest cannot see that at all. In tests/functional a "
            "constructor missing a field fails here instead of twenty minutes into "
            "`make test-e2e`."
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
        "read_first": ["CLAUDE.md"],
        "smallest_command_to_rerun": "make test",
        "likely_fix_shape": (
            "Read the first failing assertion and fix the behaviour it names. Re-run the single "
            "test file before re-running the suite."
        ),
        "next_checks": ["make test", "make quality-gates"],
        "stop_widening_condition": "Stop widening once `make test` exits 0.",
    },
}


# FUNCTION: get_doctor_layer_playbook
# SUMMARY: Resolve the remediation playbook for a gate-layer rule_id, or None when unknown.
# INPUT: rule_id (str): Identifier printed by the doctor for one of the tool layers.
# OUTPUT: (dict[str, object] | None): The playbook, or None so the caller can try the next family.
def get_doctor_layer_playbook(rule_id: str) -> dict[str, object] | None:
    return _GATE_RULE_PLAYBOOKS.get(rule_id)


# DATACLASS: scripts.doctor_layers.ToolLayer
# SUMMARY: One gate step the doctor diagnoses by running the make target that owns it.
@dataclass(frozen=True, slots=True)
class ToolLayer:
    # ATTRIBUTE: name (str)
    # SUMMARY: Value reported as `blocking_layer` and listed in `checked_layers`.
    name: str

    # ATTRIBUTE: make_target (str)
    # SUMMARY: The make target that runs this step, shared with quality-gates.
    make_target: str

    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Registered identifier `query_ai_context.py failure rule` can resolve.
    rule_id: str

    # ATTRIBUTE: file (str)
    # SUMMARY: Repo-relative path that best identifies where to start reading.
    file: str


# ATTRIBUTE: TOOL_LAYERS (tuple[ToolLayer, ...])
# SUMMARY: The four steps quality-gates runs before any validator, in the same order.
TOOL_LAYERS: tuple[ToolLayer, ...] = (
    ToolLayer("lockfile", "gate-lockfile", "gate.lockfile.stale", "uv.lock"),
    ToolLayer("lint", "gate-lint", "gate.lint.failed", "pyproject.toml"),
    ToolLayer("format", "gate-format", "gate.format.failed", "pyproject.toml"),
    ToolLayer("types", "gate-types", "gate.types.failed", "pyproject.toml"),
)

# ATTRIBUTE: SECURITY_LAYER (ToolLayer)
# SUMMARY: The bandit step, which quality-gates runs after the validators and before the drift
# checks. A tool layer rather than a validator layer: it shells out to the same make target.
SECURITY_LAYER = ToolLayer("security", "security-scan", "gate.security.failed", "project/")

# ATTRIBUTE: TESTS_LAYER (ToolLayer)
# SUMMARY: The last step quality-gates runs, and the one the doctor was asked for by name.
TESTS_LAYER = ToolLayer("tests", "gate-tests", "gate.tests.failed", "tests/")

# ATTRIBUTE: EARLY_LAYER_NAMES (tuple[str, ...])
# SUMMARY: Layers diagnosed before the context map is built, mirroring quality-gates' order.
EARLY_LAYER_NAMES: tuple[str, ...] = tuple(layer.name for layer in TOOL_LAYERS)

# ATTRIBUTE: LATE_LAYER_NAMES (tuple[str, ...])
# SUMMARY: Layers diagnosed after every existing layer is clean.
LATE_LAYER_NAMES: tuple[str, ...] = (
    "test_quality",
    "dependencies",
    "secrets",
    SECURITY_LAYER.name,
    TESTS_LAYER.name,
)


# ATTRIBUTE: _DIAGNOSTIC_LINE (re.Pattern[str])
# SUMMARY: Shapes that carry the actual finding rather than a runner's banner.
# `FAILED tests/...::test_x` from pytest, `file.py:12: error: ...` from mypy and ruff,
# `would reformat: ...` from the formatter, `>> Issue: [B608:...]` from bandit — whose other
# output is a `[tester] WARNING ...` line that matches nothing else here and would otherwise be
# reported as the finding.
_DIAGNOSTIC_LINE = re.compile(
    r"^(FAILED |ERROR |E\s|>> Issue:|\S+:\d+[:\s]|would reformat|unformatted)", re.IGNORECASE
)


# FUNCTION: _first_meaningful_line
# SUMMARY: Pick the most useful single line out of a tool's combined output.
# INPUT: output (str): Combined stdout and stderr.
# INPUT: fallback (str): Message used when the tool printed nothing.
# OUTPUT: (str): One line, never empty.
def _first_meaningful_line(output: str, fallback: str) -> str:
    # **LOGIC_STEP**: Two passes. A runner announces itself before it fails — pytest's first line
    # is the command it is about to run — so taking the first non-framing line reported the banner
    # as the diagnosis. Prefer a line that looks like a finding, and fall back to the first
    # ordinary line only when nothing does.
    candidates = [
        stripped
        for line in output.splitlines()
        # **LOGIC_STEP**: make's own framing is never the answer: reporting
        # "make[1]: *** [gate-types] Error 1" names the wrapper instead of the problem.
        if (stripped := line.strip())
        and not stripped.startswith("make[")
        and not stripped.startswith("make:")
    ]
    for candidate in candidates:
        if _DIAGNOSTIC_LINE.match(candidate):
            return candidate
    return candidates[0] if candidates else fallback


# ATTRIBUTE: REENTRY_ENV_VAR (str)
# SUMMARY: Set in every child process a tool layer spawns, and honoured on the way in.
# The tests layer runs the unit suite, and the unit suite exercises the doctor — without this the
# first `make doctor` on a clean tree recursed until it was killed. The sentinel is not a
# nicety: it is what makes "the doctor runs the tests" terminate at all.
#
# It is also unforgeable only by convention: any shell that happens to export it disables five
# layers. Two things keep that from becoming a silent lie. The `doctor` and `doctor-json` make
# targets clear the variable, so the entry point a human uses cannot be disabled from outside;
# and whatever is skipped is reported in `skipped_layers` and printed, so a doctor that checked
# less than everything says so instead of answering a bare "ok".
REENTRY_ENV_VAR = "MAYAK_DOCTOR_SUBPROCESS"


# FUNCTION: tool_layers_enabled
# SUMMARY: Report whether this process may shell out to the gate targets.
# OUTPUT: (bool): False inside a process the doctor itself spawned.
def tool_layers_enabled() -> bool:
    return os.environ.get(REENTRY_ENV_VAR) != "1"


# FUNCTION: skipped_layer_names
# SUMMARY: Name the layers this process will not run, so an "ok" can never over-claim.
# OUTPUT: (tuple[str, ...]): Empty when every layer is available.
def skipped_layer_names() -> tuple[str, ...]:
    if tool_layers_enabled() and shutil.which("make") is not None:
        return ()
    return (*EARLY_LAYER_NAMES, SECURITY_LAYER.name, TESTS_LAYER.name)


# FUNCTION: diagnose_tool_layer
# SUMMARY: Run one gate target and turn a non-zero exit into a doctor payload.
# INPUT: layer (ToolLayer): The step to run.
# OUTPUT: (dict[str, object] | None): Issue payload when the step fails, otherwise None.
def diagnose_tool_layer(layer: ToolLayer) -> dict[str, object] | None:
    make = shutil.which("make")
    if make is None or not tool_layers_enabled():
        # **LOGIC_STEP**: Skip rather than fail, for two different reasons. A checkout without
        # make can still run the doctor through `uv run python scripts/doctor_ai_context.py`, and
        # calling that absence a blocking gate layer diagnoses the wrong thing. Inside a process
        # the doctor spawned, skipping is what stops the recursion.
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
                # **LOGIC_STEP**: The recommended command for a gate layer is the gate itself —
                # the command that just failed. What actually moves the diff forward lives in
                # `likely_fix_shape` ("Run `make ai-autofix`…"), which this payload dropped until
                # 2026-08-13, so the doctor answered a failed `make gate-format` with "run `make
                # gate-format`" and kept the real instruction to itself.
                "likely_fix_shape": playbook.get("likely_fix_shape"),
                "stop_widening_condition": str(playbook.get("stop_widening_condition", "")),
            }
        ],
    }


# FUNCTION: _collector_payload
# SUMMARY: Build a doctor payload from the first error-severity issue a collector returned.
# INPUT: blocking_layer (str): Value reported as `blocking_layer`.
# INPUT: rule_id (str): The issue's own rule identifier.
# INPUT: file (str): Repo-relative path identifying the issue.
# INPUT: line (int): 1-based line, or 1 when the issue has no line concept.
# INPUT: message (str): Human-readable description.
# INPUT: playbook (dict[str, object] | None): Remediation guidance for the rule.
# OUTPUT: (dict[str, object]): Doctor payload.
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


# FUNCTION: _errors_only
# SUMMARY: Keep the blocking issues out of a collector's mixed result.
# INPUT: issues (Sequence[object]): Issues with a `severity` attribute.
# OUTPUT: (list[object]): Only those whose severity is "error".
def _errors_only(issues: Sequence[object]) -> list[object]:
    # **LOGIC_STEP**: Filter by severity, never by rule_id literal — the same convention the
    # migrations layer above already follows. A warning-severity issue is reported by its
    # validator and does not block, so naming it as the blocking layer would be wrong.
    return [issue for issue in issues if getattr(issue, "severity", "error") == "error"]


# FUNCTION: _diagnose_test_quality
# SUMMARY: Report the first blocking test-quality issue.
# OUTPUT: (dict[str, object] | None): Payload, or None when the layer is clean.
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


# FUNCTION: _diagnose_dependencies
# SUMMARY: Report the first blocking dependency issue.
# OUTPUT: (dict[str, object] | None): Payload, or None when the layer is clean.
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


# FUNCTION: _diagnose_secrets
# SUMMARY: Report the first blocking secret-scan issue.
# OUTPUT: (dict[str, object] | None): Payload, or None when the layer is clean.
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


# FUNCTION: _diagnose_security
# SUMMARY: Run the bandit step and report a finding as the security layer.
# OUTPUT: (dict[str, object] | None): Payload, or None when the scan is clean.
def _diagnose_security() -> dict[str, object] | None:
    return diagnose_tool_layer(SECURITY_LAYER)


# FUNCTION: _diagnose_tests
# SUMMARY: Run the unit and infrastructure suites and report a failure as the tests layer.
# OUTPUT: (dict[str, object] | None): Payload, or None when the suites pass.
def _diagnose_tests() -> dict[str, object] | None:
    # **LOGIC_STEP**: Actually run them. The cheap alternative — "every layer I check is clean, so
    # it must be the tests" — was measured and rejected: eight quality-gates steps sit outside the
    # layers the doctor models, and `make doctor` is also a standalone command with no evidence
    # that gates just failed, so the inference would produce confident wrong diagnoses.
    return diagnose_tool_layer(TESTS_LAYER)


# ATTRIBUTE: _LATE_LAYERS (tuple[Callable[[], dict[str, object] | None], ...])
# SUMMARY: Diagnosers run once every earlier layer is clean, cheapest first.
_LATE_LAYERS: tuple[Callable[[], dict[str, object] | None], ...] = (
    _diagnose_test_quality,
    _diagnose_dependencies,
    _diagnose_secrets,
    _diagnose_security,
    _diagnose_tests,
)


# FUNCTION: diagnose_early_layers
# SUMMARY: Diagnose the four tool steps quality-gates runs before any validator.
# OUTPUT: (tuple[dict[str, object] | None, tuple[str, ...]]): First failing payload (or None) and
#         the names of the layers that actually ran, so `checked_layers` never over-claims.
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


# FUNCTION: diagnose_late_layers
# SUMMARY: Diagnose the validators and the test suite the doctor used to leave unmodelled.
# OUTPUT: (tuple[dict[str, object] | None, tuple[str, ...]]): First failing payload (or None) and
#         the names of the layers that actually ran.
def diagnose_late_layers() -> tuple[dict[str, object] | None, tuple[str, ...]]:
    executed: list[str] = []
    for name, diagnose in zip(LATE_LAYER_NAMES, _LATE_LAYERS):
        # **LOGIC_STEP**: Both tool layers here shell out to make, so both are skipped inside a
        # process the doctor spawned — that is what stops the recursion.
        if name in (SECURITY_LAYER.name, TESTS_LAYER.name) and not tool_layers_enabled():
            continue
        payload = diagnose()
        executed.append(name)
        if payload is not None:
            return payload, tuple(executed)
    return None, tuple(executed)


if __name__ == "__main__":
    early, _ = diagnose_early_layers()
    late = (None, ()) if early is not None else diagnose_late_layers()
    result = early or late[0]
    print("clean" if result is None else result["blocking_layer"])
    sys.exit(0 if result is None else 1)
