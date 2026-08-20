from __future__ import annotations

import json

from ai_query.models import QueryPayload


def render_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)


def render_text(result: QueryPayload) -> str:
    lines = [f"query: {result.kind}"]
    if result.identifier is not None:
        lines.append(f"identifier: {result.identifier}")
    context_status = result.payload.get("context_status")
    if isinstance(context_status, dict) and context_status.get("status") != "ok":
        lines.append("context_status: " + str(context_status["status"]))
    confidence_warning = result.payload.get("confidence_warning")
    if isinstance(confidence_warning, dict):
        lines.append("confidence_warning: " + str(confidence_warning["message"]))
    if result.kind == "overview":
        lines.append("summary:")
        lines.append("  shortcuts: " + ", ".join(result.payload["query_cli"].get("shortcuts", [])))
        lines.append(
            "  strictly_validated_rules: "
            + ", ".join(
                rule["rule_id"]
                for rule in result.payload["enforcement_model"]["strictly_validated_rules"]
            )
        )
    elif result.kind == "bootstrap":
        lines.append("summary:")
        lines.append("  read_first: " + ", ".join(result.payload["read_first"]))
        lines.append("  task_shortcuts: " + ", ".join(result.payload["task_shortcuts"]))
    elif result.kind == "before-edit":
        lines.append("summary:")
        lines.append("  edit_zone: " + f"{result.payload['edit_zone']} ({result.payload['risk']})")
        lines.append("  watch_rules: " + ", ".join(result.payload.get("watch_rules", [])))
        related = result.payload.get("related_tests", {})
        unit = related.get("likely_unit_tests", [])
        integration = related.get("likely_integration_tests", [])
        if unit or integration:
            lines.append("  related_tests: " + ", ".join(unit + integration))
    elif result.kind == "workset":
        lines.append("summary:")
        lines.append("  status: " + result.payload.get("status", "ok"))
        lines.append("  changed_files: " + ", ".join(result.payload.get("changed_files", [])[:3]))
        lines.append("  validators: " + ", ".join(result.payload.get("required_validators", [])))
        if result.payload.get("partial_context"):
            lines.append("  partial_context: confirm real wiring files before edit")
    elif result.kind == "failure":
        lines.append("summary:")
        lines.append("  rerun: " + result.payload["smallest_command_to_rerun"])
        lines.append("  stop_widening: " + result.payload["stop_widening_condition"])
    lines.append(json.dumps(result.payload, ensure_ascii=True, sort_keys=True))
    return "\n".join(lines)
