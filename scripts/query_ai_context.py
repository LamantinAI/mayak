#!/usr/bin/env python3
# FILE: query_ai_context.py
# SUMMARY: Query the AI context artifacts through stable commands so agents can inspect wiring, impact, and validation guidance without broad repo scans.

from __future__ import annotations

import argparse

from ai_context.constants import QUERY_COMMAND_NAMES
from ai_context.errors import ContextBuildError
from ai_query.common import query_context_status
from ai_query.handlers.bootstrap import query_bootstrap
from ai_query.handlers.before_edit import query_before_edit
from ai_query.handlers.failure import query_failure
from ai_query.handlers.overview import query_overview
from ai_query.handlers.workset import query_workset
from ai_query.rendering import render_json as _render_json
from ai_query.rendering import render_text as _render_text
from ai_query.router import resolve_query as _resolve_query


SUPPORTED_FORMATS = ("json", "text")

_query_bootstrap = query_bootstrap
_query_overview = query_overview
_query_before_edit = query_before_edit
_query_failure = query_failure
_query_workset = query_workset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query Mayak AI context artifacts without broad repository scans."
    )
    parser.add_argument(
        "--format",
        choices=SUPPORTED_FORMATS,
        default="json",
        help="Choose JSON for automation-friendly output or text for quick terminal inspection.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "bootstrap",
        help="Show the compact startup payload for a new agent session.",
    )

    subparsers.add_parser(
        "overview",
        help="Show core entrypoints, integrity, supported query commands, and task names.",
    )

    workset_parser = subparsers.add_parser(
        "workset",
        help="Show the aggregated blast radius, checks, and regeneration plan for a diff or explicit file set.",
    )
    workset_subparsers = workset_parser.add_subparsers(dest="subject_kind", required=True)
    workset_subparsers.add_parser(
        "diff", help="Resolve the current git worktree diff into a workset payload."
    )

    before_edit_parser = subparsers.add_parser(
        "before-edit",
        help="Show the pre-edit guardrails, likely failures, and smallest-diff guidance for a high-signal repository file.",
    )
    before_edit_subparsers = before_edit_parser.add_subparsers(dest="subject_kind", required=True)
    before_edit_file = before_edit_subparsers.add_parser(
        "file", help="Pre-edit guidance for a repository file path."
    )
    before_edit_file.add_argument("repo_path")

    failure_parser = subparsers.add_parser(
        "failure",
        help="Show a remediation playbook for a stable validator or drift rule ID.",
    )
    failure_subparsers = failure_parser.add_subparsers(dest="subject_kind", required=True)
    failure_rule = failure_subparsers.add_parser("rule", help="Remediation for a rule ID.")
    failure_rule.add_argument("rule_id")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        context_status = query_context_status()
    except ContextBuildError as error:
        payload = {
            "status": "error",
            "degraded_status": error.issue.issue_type,
            "issues": [error.issue.to_payload()],
            "available_commands": [
                "uv run python scripts/generate_ai_context.py --check",
                "uv run python scripts/doctor_ai_context.py",
                "uv run python scripts/validate_architecture.py",
                "uv run python scripts/validate_endpoint_wiring.py",
                "uv run python scripts/validate_runtime_ownership.py",
            ],
            "temporarily_unavailable_queries": list(QUERY_COMMAND_NAMES),
        }
        if args.format == "text":
            print(f"status: {payload['degraded_status']}")
            for issue in payload["issues"]:
                print(f"- {issue['message']}")
                print(f"  next: {issue['recommended_next_command']}")
        else:
            print(_render_json(payload))
        return 1

    if context_status.get("degraded_status") == "integrity_error":
        if args.format == "text":
            print(f"status: {context_status['degraded_status']}")
            for issue in context_status["issues"]:
                print(f"- {issue['message']}")
                print(f"  next: {issue['recommended_next_command']}")
        else:
            print(_render_json(context_status))
        return 1

    try:
        result = _resolve_query(args)
    except (KeyError, RuntimeError, ValueError) as error:
        print(str(error))
        return 1

    if context_status["status"] == "generated_outdated":
        result.payload["context_status"] = context_status
    output = _render_json(result.payload)
    if args.format == "text":
        output = _render_text(result)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
