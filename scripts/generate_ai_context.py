#!/usr/bin/env python3
# FILE: generate_ai_context.py
# SUMMARY: Generate or verify the AI-friendly repository context artifacts consumed by onboarding docs and query tooling.

from __future__ import annotations

import argparse
from pathlib import Path

from ai_context.build_architecture_rules import build_architecture_rules
from ai_context.build_change_map import build_change_map
from ai_context.build_context_map import build_context_map
from ai_context.constants import (
    ARCHITECTURE_RULES_OUTPUT_PATH,
    CHANGE_MAP_OUTPUT_PATH,
    OUTPUT_PATH,
    QUERY_COMMAND_NAMES,
    ROOT_DIR,
)
from ai_context.errors import ContextBuildError
from ai_context.integrity import build_integrity_report
from ai_context.rendering import build_generated_outputs, render_json
from ai_context.validator_contract import build_validator_issue_payload


# ATTRIBUTE: _DRIFT_STOP_WIDENING_CONDITIONS (dict[str, str])
# SUMMARY: stop_widening_condition text for the two rule_ids this module emits directly.
# Duplicated (not imported) from ai_query.common._DRIFT_RULE_PLAYBOOKS to avoid a circular
# import (ai_query.common imports from this module). Keep both copies in sync if the wording
# changes — covered by tests/application/test_validator_error_contract.py.
_DRIFT_STOP_WIDENING_CONDITIONS: dict[str, str] = {
    "drift.generated.missing": (
        "Stop widening once the generated file exists again and generate_ai_context.py --check passes."
    ),
    "drift.generated.outdated": (
        "Stop widening once generate_ai_context.py --check passes with freshly regenerated artifacts."
    ),
}


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def _build_generated_outputs() -> dict[Path, str]:
    return build_generated_outputs(
        {
            OUTPUT_PATH: build_context_map(),
            CHANGE_MAP_OUTPUT_PATH: build_change_map(),
            ARCHITECTURE_RULES_OUTPUT_PATH: build_architecture_rules(),
        }
    )


def generated_output_issues(
    rendered_outputs: dict[Path, str],
) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    for output_path, rendered in rendered_outputs.items():
        if not output_path.exists():
            issues.append(
                build_validator_issue_payload(
                    rule_id="drift.generated.missing",
                    category="drift",
                    file=_display_path(output_path),
                    line=1,
                    message=f"Missing generated file: {_display_path(output_path)}",
                    playbook={
                        "suggested_fix": (
                            "Regenerate the AI-facing derived artifacts before continuing."
                        ),
                        "read_first": ["CLAUDE.md", "docs/ai_change_map.json"],
                        "next_checks": [
                            "make refresh-generated-docs",
                            "uv run python scripts/generate_ai_context.py --check",
                        ],
                        "stop_widening_condition": _DRIFT_STOP_WIDENING_CONDITIONS[
                            "drift.generated.missing"
                        ],
                    },
                )
            )
            continue
        if output_path.read_text(encoding="utf-8") != rendered:
            issues.append(
                build_validator_issue_payload(
                    rule_id="drift.generated.outdated",
                    category="drift",
                    file=_display_path(output_path),
                    line=1,
                    message=f"Outdated generated file: {_display_path(output_path)}",
                    playbook={
                        "suggested_fix": (
                            "Regenerate the derived artifacts after changing wiring, contracts, or agent docs."
                        ),
                        "read_first": ["CLAUDE.md", "docs/ai_context_map.json"],
                        "next_checks": [
                            "make refresh-generated-docs",
                            "uv run python scripts/generate_ai_context.py --check",
                        ],
                        "stop_widening_condition": _DRIFT_STOP_WIDENING_CONDITIONS[
                            "drift.generated.outdated"
                        ],
                    },
                )
            )
    return issues


def degraded_context_payload(
    *,
    degraded_status: str,
    issues: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "status": "error",
        "degraded_status": degraded_status,
        "issues": issues,
        "available_commands": [
            "uv run python scripts/generate_ai_context.py --check",
            "uv run python scripts/validate_architecture.py",
            "uv run python scripts/validate_endpoint_wiring.py",
            "uv run python scripts/validate_runtime_ownership.py",
        ],
        "temporarily_unavailable_queries": list(QUERY_COMMAND_NAMES),
    }


def _print_degraded_payload(
    payload: dict[str, object],
    *,
    as_json: bool,
) -> None:
    if as_json:
        print(render_json(payload), end="")
        return
    print(f"status: {payload['degraded_status']}")
    for issue in payload["issues"]:
        print(f"- {issue['message']}")
        # **LOGIC_STEP**: Print the registered rule_id alongside the message. The validator-recovery
        # rule in docs/agent_rules.md tells the agent to call `failure rule <rule_id>` with what it
        # just saw; degraded_status is a layer name ("generated_outdated"), not a rule_id
        # ("drift.generated.outdated"), so printing only that leaves the prescribed command failing
        # with "Unknown failure rule ID". Guarded by tests/application/test_generate_ai_context.py.
        # Guarded with .get: ContextIssue payloads (syntax_error path) carry issue_type only.
        rule_id = issue.get("rule_id")
        if rule_id:
            print(f"  rule: {rule_id}")
        print(f"  next: {issue['recommended_next_command']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify the AI-friendly repository context artifacts."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable check results.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that generated JSON artifacts are up to date without rewriting them.",
    )
    args = parser.parse_args()

    try:
        rendered_outputs = _build_generated_outputs()
        context_map = build_context_map()
    except ContextBuildError as error:
        payload = degraded_context_payload(
            degraded_status=error.issue.issue_type,
            issues=[error.issue.to_payload(ROOT_DIR)],
        )
        _print_degraded_payload(payload, as_json=args.json)
        return 1

    integrity = context_map["integrity"]
    if integrity["status"] != "ok":
        issues = [
            {
                "rule_id": "drift.integrity.error",
                "category": "drift",
                "file": "docs/ai_context_map.json",
                "line": 1,
                "message": issue["message"],
                "issue_type": issue["issue_type"],
                "suggested_fix": (
                    "Repair the broken wiring contract, then regenerate the derived artifacts."
                ),
                "repair_protocol": list(issue["repair_protocol"]),
                "read_first": [
                    "CLAUDE.md",
                    "docs/ai_context_map.json",
                    "docs/architecture_rules.json",
                ],
                "next_commands": [
                    "make refresh-generated-docs",
                    "uv run python scripts/generate_ai_context.py --check",
                ],
                "recommended_next_command": issue["recommended_next_command"],
            }
            for issue in integrity["issues"]
        ]
        _print_degraded_payload(
            degraded_context_payload(
                degraded_status="integrity_error",
                issues=issues,
            ),
            as_json=args.json,
        )
        return 1

    if args.check:
        issues = generated_output_issues(rendered_outputs)
        if issues:
            _print_degraded_payload(
                degraded_context_payload(
                    degraded_status="generated_outdated",
                    issues=[
                        {
                            **issue,
                            "issue_type": "generated_outdated",
                            "recommended_next_command": "make refresh-generated-docs",
                        }
                        for issue in issues
                    ],
                ),
                as_json=args.json,
            )
            return 1
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            print("AI context artifacts are up to date.")
        return 0

    for output_path, rendered in rendered_outputs.items():
        output_path.write_text(rendered, encoding="utf-8")
        print(f"Wrote {_display_path(output_path)}")
    return 0


_build_integrity_report = build_integrity_report


if __name__ == "__main__":
    raise SystemExit(main())
