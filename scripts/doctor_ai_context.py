#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from ai_context.errors import ContextBuildError
from ai_context.rendering import render_json
from ai_query.common import degraded_query_payload, failure_playbook
from scripts.generate_ai_context import (
    _build_generated_outputs,
    build_context_map,
    generated_output_issues,
)
from scripts.doctor_layers import (
    REENTRY_ENV_VAR,
    diagnose_early_layers,
    diagnose_late_layers,
    skipped_layer_names,
)

# ATTRIBUTE: UNAVAILABLE_VALIDATOR (str | None)
# SUMMARY: Import error text when a validator module could not be loaded, otherwise None.
# NOTE: Every validator is imported at module level so the names stay patchable and mypy keeps
# checking the calls. The block is guarded because a validator that is missing, renamed or
# syntactically broken used to kill this module during import: `make doctor` answered a broken
# repository with a raw ModuleNotFoundError traceback, at the one moment a diagnostic tool has a job
# to do. Measured on 2026-08-13 — deleting scripts/validate_cbm.py produced a traceback from
# ai_query/common.py, not a diagnosis.
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
    from scripts.validate_endpoint_wiring import (
        collect_endpoint_wiring_issues,
        get_endpoint_rule_playbook,
    )
    from scripts.validate_file_policy import (
        collect_file_policy_issues,
        get_file_policy_rule_playbook,
    )
    from scripts.validate_migrations import collect_migration_issues, get_migrations_rule_playbook
    from scripts.validate_module_sizes import collect_module_size_issues, get_module_size_playbook
    from scripts.validate_project_context import (
        collect_project_context_issues,
        get_project_context_rule_playbook,
    )
    from scripts.validate_script_paths import (
        collect_script_path_issues,
        get_script_paths_rule_playbook,
    )
    from scripts.validate_skills_frontmatter import (
        collect_skills_frontmatter_issues,
        get_skills_frontmatter_rule_playbook,
    )
    from scripts.validate_runtime_ownership import (
        collect_runtime_ownership_issues,
        get_runtime_ownership_rule_playbook,
    )
except ImportError as error:
    UNAVAILABLE_VALIDATOR = str(error)


ROOT_DIR = Path(__file__).resolve().parent.parent


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return path.as_posix()


# ATTRIBUTE: LAYER_UNAVAILABLE_RULE_ID (str)
# SUMMARY: Reported when the doctor itself cannot load one of the validators it diagnoses.
LAYER_UNAVAILABLE_RULE_ID = "doctor.layer_unavailable"


# FUNCTION: unavailable_validator_payload
# SUMMARY: Build the doctor payload for a validator module that could not be imported.
# INPUT: detail (str): The import error, naming the module that is missing or broken.
# OUTPUT: (dict[str, object]): Same payload shape every other layer returns.
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


# FUNCTION: _fix_shape_for
# SUMMARY: Return the shape of the fix for a rule, or None when the rule carries no playbook.
# OUTPUT: (str | None): One sentence describing the edit, safe to print next to the diagnosis.
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


def diagnose() -> dict[str, object]:
    if UNAVAILABLE_VALIDATOR is not None:
        return unavailable_validator_payload(UNAVAILABLE_VALIDATOR)

    try:
        rendered_outputs = _build_generated_outputs()
    except ContextBuildError as error:
        return degraded_query_payload(
            degraded_status=error.issue.issue_type,
            issues=[error.issue.to_payload(ROOT_DIR)],
        )

    try:
        drift_issues = generated_output_issues(rendered_outputs)
    except ContextBuildError as error:
        return degraded_query_payload(
            degraded_status=error.issue.issue_type,
            issues=[error.issue.to_payload(ROOT_DIR)],
        )
    if drift_issues:
        return {
            "status": "error",
            "degraded_status": "generated_outdated",
            "issues": [
                {
                    **issue,
                    "issue_type": "generated_outdated",
                    "recommended_next_command": "make refresh-generated-docs",
                    "likely_fix_shape": (
                        "Regenerate the artifact instead of editing it: "
                        "`make refresh-generated-docs`, then commit what it wrote."
                    ),
                }
                for issue in drift_issues
            ],
        }

    context_map = build_context_map()
    if context_map["integrity"]["status"] != "ok":
        return degraded_query_payload(
            degraded_status="integrity_error",
            issues=list(context_map["integrity"]["issues"]),
        )

    architecture_issues = collect_architecture_issues(ROOT_DIR)
    if architecture_issues:
        issue = architecture_issues[0]
        playbook = get_architecture_rule_playbook(issue.rule_id)
        return {
            "status": "error",
            "blocking_layer": "architecture",
            "issues": [
                _validator_issue_payload(
                    issue_type="architecture_error",
                    rule_id=issue.rule_id,
                    category=issue.category,
                    file=_display_path(issue.path),
                    line=issue.line,
                    message=issue.message,
                    playbook=playbook,
                )
            ],
        }

    endpoint_issues = collect_endpoint_wiring_issues(ROOT_DIR)
    if endpoint_issues:
        issue = endpoint_issues[0]
        playbook = get_endpoint_rule_playbook(issue.rule_id)
        return {
            "status": "error",
            "blocking_layer": "endpoint_wiring",
            "issues": [
                _validator_issue_payload(
                    issue_type="endpoint_wiring_error",
                    rule_id=issue.rule_id,
                    category=issue.category,
                    file=_display_path(issue.path),
                    line=issue.line,
                    message=issue.message,
                    playbook=playbook,
                )
            ],
        }

    runtime_issues = collect_runtime_ownership_issues(ROOT_DIR)
    if runtime_issues:
        issue = runtime_issues[0]
        playbook = get_runtime_ownership_rule_playbook(issue.rule_id)
        return {
            "status": "error",
            "blocking_layer": "runtime_ownership",
            "issues": [
                _validator_issue_payload(
                    issue_type="runtime_ownership_error",
                    rule_id=issue.rule_id,
                    category=issue.category,
                    file=_display_path(issue.path),
                    line=issue.line,
                    message=issue.message,
                    playbook=playbook,
                )
            ],
        }

    cbm_issues = collect_validation_issues(ROOT_DIR)
    if cbm_issues:
        issue = cbm_issues[0]
        # **LOGIC_STEP**: Derive the real rule_id from the issue message so the agent
        # routes to the correct CBM playbook, not a hard-coded literal that masks
        # which of the 9 CBM rules actually fired.
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
                    # **LOGIC_STEP**: One wording, produced by the issue itself. The doctor used to
                    # rebuild its own sentence, which silently became wrong the moment the
                    # validator grew a second budget with a different unit.
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

    skills_issues = [
        issue for issue in collect_skills_frontmatter_issues(ROOT_DIR) if issue.severity == "error"
    ]
    if skills_issues:
        issue = skills_issues[0]
        playbook = get_skills_frontmatter_rule_playbook(issue.rule_id)
        return {
            "status": "error",
            "blocking_layer": "skills_frontmatter",
            "issues": [
                _validator_issue_payload(
                    issue_type="skills_frontmatter_error",
                    rule_id=issue.rule_id,
                    category="skills_frontmatter",
                    file=_display_path(issue.path),
                    line=1,
                    message=issue.message,
                    playbook=playbook,
                )
            ],
        }

    project_context_issues = [
        issue for issue in collect_project_context_issues(ROOT_DIR) if issue.severity == "error"
    ]
    if project_context_issues:
        issue = project_context_issues[0]
        playbook = get_project_context_rule_playbook(issue.rule_id)
        return {
            "status": "error",
            "blocking_layer": "project_context",
            "issues": [
                _validator_issue_payload(
                    issue_type="project_context_error",
                    rule_id=issue.rule_id,
                    category="project_context",
                    file="docs/project_context.json",
                    line=1,
                    message=f"[{issue.field}] {issue.message}",
                    playbook=playbook,
                )
            ],
        }

    # **LOGIC_STEP**: Filter informational issues (e.g., DB unreachable skip) by severity rather than
    # by rule_id literal. See `docs/agent_rules.md` "Validator authoring conventions".
    migration_issues = [
        issue for issue in collect_migration_issues(ROOT_DIR) if issue.severity == "error"
    ]
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

    file_policy_issues = [
        issue for issue in collect_file_policy_issues(ROOT_DIR) if issue.severity == "error"
    ]
    if file_policy_issues:
        issue = file_policy_issues[0]
        playbook = get_file_policy_rule_playbook(issue.rule_id)
        return {
            "status": "error",
            "blocking_layer": "file_policy",
            "issues": [
                _validator_issue_payload(
                    issue_type="file_policy_error",
                    rule_id=issue.rule_id,
                    category="file_policy",
                    file="ai_context/file_policy.py",
                    line=1,
                    message=f"[{issue.entry_key}] [{issue.field}] {issue.message}",
                    playbook=playbook,
                )
            ],
        }

    script_path_issues = [
        issue for issue in collect_script_path_issues(ROOT_DIR) if issue.severity == "error"
    ]
    if script_path_issues:
        issue = script_path_issues[0]
        playbook = get_script_paths_rule_playbook(issue.rule_id)
        return {
            "status": "error",
            "blocking_layer": "script_paths",
            "issues": [
                _validator_issue_payload(
                    issue_type="script_paths_error",
                    rule_id=issue.rule_id,
                    category="script_paths",
                    file=issue.source_file,
                    line=issue.line,
                    message=issue.message,
                    playbook=playbook,
                )
            ],
        }

    # **LOGIC_STEP**: Drift checks — two generators that ship `--check` mode and own surfaces
    # not covered by `_build_generated_outputs` (the agent wrappers, docs/project_map.md). A third
    # covered `.claude/commands/` until that directory and its generator left on 2026-08-20.
    drift_check = _diagnose_drift_layer(
        layer="agent_docs_drift",
        blocking_layer="agent_docs_drift",
        command=("scripts/sync_agent_docs.py", "--check"),
        rule_id="drift.agent_docs.outdated",
        target="CLAUDE.md",
    )
    if drift_check is not None:
        return drift_check

    drift_check = _diagnose_drift_layer(
        layer="project_map_drift",
        blocking_layer="project_map_drift",
        command=("scripts/structure_builder.py", "--check"),
        rule_id="drift.generated.outdated",
        target="docs/project_map.md",
    )
    if drift_check is not None:
        return drift_check

    return {
        "status": "ok",
        "checked_layers": [
            "context",
            "architecture",
            "endpoint_wiring",
            "runtime_ownership",
            "cbm",
            "module_size",
            "skills_frontmatter",
            "project_context",
            "migrations",
            "file_policy",
            "script_paths",
            "agent_docs_drift",
            "project_map_drift",
        ],
        "final_gate": "make quality-gates",
    }


# FUNCTION: diagnose_full
# SUMMARY: Every layer `make quality-gates` runs — the tool steps around `diagnose()`'s validators.
# OUTPUT: (dict[str, object]): Same payload shape as diagnose(), with checked_layers widened.
# NOTE: Separate from diagnose() on purpose. diagnose() is called from many tests in
# tests/application/test_doctor_ai_context.py, and the tests layer runs that very suite; folding
# the two together made the first clean run recurse. This is the entry point `main()` uses, so
# `make doctor` sees everything while the unit tests keep exercising the cheap half directly.
def diagnose_full() -> dict[str, object]:
    # **LOGIC_STEP**: Tool steps first, in the order quality-gates runs them, so the layer this
    # reports is the one that actually blocked. With warm caches the four together cost a
    # fraction of a second.
    early_failure, early_layers = diagnose_early_layers()
    if early_failure is not None:
        return early_failure

    payload = diagnose()
    if payload["status"] != "ok":
        return payload

    # **LOGIC_STEP**: The validators and the suite the doctor used to leave unmodelled, last
    # because they are the expensive ones. Reaching "ok" here means every step quality-gates runs
    # was checked, which is what `make quality-gates` has always claimed.
    late_failure, late_layers = diagnose_late_layers()
    if late_failure is not None:
        return late_failure

    checked = payload.get("checked_layers")
    payload["checked_layers"] = [
        *early_layers,
        *(checked if isinstance(checked, list) else []),
        *late_layers,
    ]
    # **LOGIC_STEP**: An "ok" that checked less than everything has to say so. The re-entry
    # sentinel is a plain environment variable, so any shell carrying it disables five layers —
    # and a bare "doctor status: ok" would then be the exact lie this whole module was written to
    # remove. Reporting the gap costs one field and one line of output.
    skipped = skipped_layer_names()
    if skipped:
        payload["skipped_layers"] = list(skipped)
    return payload


# FUNCTION: _diagnose_drift_layer
# SUMMARY: Run a generator script in `--check` mode and convert non-zero exit into a doctor payload.
# INPUT: blocking_layer (str): Value reported back as `blocking_layer`.
# INPUT: command (tuple[str, ...]): Script path + args (relative to ROOT_DIR).
# INPUT: target (str): Human-readable target file or directory describing what's drifted.
# OUTPUT: (dict[str, object] | None): Issue payload when drift is detected, otherwise None.
def _diagnose_drift_layer(
    *,
    layer: str,
    blocking_layer: str,
    command: tuple[str, ...],
    rule_id: str,
    target: str,
) -> dict[str, object] | None:
    del (
        layer
    )  # Reserved for future layer-specific routing; kept for symmetry with checked_layers names.
    script_path = ROOT_DIR / command[0]
    if not script_path.exists():
        return None
    full_command = (sys.executable, str(script_path), *command[1:])
    completed = subprocess.run(
        full_command,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
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
                "recommended_next_command": "make refresh-generated-docs",
                "likely_fix_shape": _fix_shape_for(rule_id),
                "stop_widening_condition": (f"Stop widening once `{' '.join(command)}` exits 0."),
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose the first blocking AI-context or validation issue without mutating the repository."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    args = parser.parse_args()

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
        print("final_gate: make quality-gates")
    else:
        print("doctor status: error")
        if "degraded_status" in payload:
            print(f"blocking_layer: {payload['degraded_status']}")
        else:
            print(f"blocking_layer: {payload['blocking_layer']}")
        issue = payload["issues"][0]
        print(f"message: {issue['message']}")
        # **LOGIC_STEP**: The fix shape is printed above the command on purpose. For every gate
        # layer the recommended command IS the command that just failed — `make gate-format` after
        # `make gate-format` — and the sentence that moves the diff forward ("Run `make
        # ai-autofix`…") sat unread in the payload until 2026-08-13.
        fix_shape = issue.get("likely_fix_shape")
        if fix_shape:
            print(f"fix: {fix_shape}")
        print(f"next: {issue['recommended_next_command']}")
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
