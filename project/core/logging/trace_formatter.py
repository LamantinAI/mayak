# FILE: project/core/logging/trace_formatter.py
# SUMMARY: NDJSON-to-text tree transformer for LLM-friendly trace visualization.
# Re-export the parser names so older imports still work.

from __future__ import annotations

import json
import sys
from typing import Any, Iterable

from project.core.logging.enums import RequestOutcome
from project.core.logging.trace_tree import (
    LeafEvent,
    SpanNode,
    _build_tree,
    _parse_events,
    _TOOL_SPAN_PREFIX,
)

# Routine outcomes include 4xx; reserve the failed marker for server-side faults.
_ROUTINE_STATUSES = frozenset(
    {RequestOutcome.OK.value.upper(), "UNKNOWN", RequestOutcome.CLIENT_ERROR.value.upper()}
)


# ==================== RENDERING ====================


_MAX_ARG_CHARS = 80


# Escape line breaks before clipping so a model argument cannot look like a second tree node.
def _clipped(value: str) -> str:
    flattened = value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    if len(flattened) <= _MAX_ARG_CHARS:
        return flattened
    return f"{flattened[:_MAX_ARG_CHARS]}… (+{len(flattened) - _MAX_ARG_CHARS} chars)"


def _render_span_line(node: SpanNode) -> str:
    sid = f"[{node.span_id[:8]}] " if node.span_id else ""
    dur = f"({node.duration_ms}ms)" if node.duration_ms is not None else ""
    if node.error:
        # Mark interruptions and client rejections separately from server failures.
        if node.interrupted:
            mark = "⊘"
        elif node.client_rejection:
            mark = "⚠"
        else:
            mark = "✗"
        suffix = f" {mark} {node.error}"
        if node.error_site:
            suffix = f"{suffix} at {node.error_site}"
    else:
        out_parts = []
        for k, v in node.output.items():
            if isinstance(v, (int, float, bool)):
                out_parts.append(f"{k}={v}")
            elif isinstance(v, str):
                out_parts.append(f'{k}="{v}"')
        suffix = f" → {', '.join(out_parts)}" if out_parts else ""

    # Compact tool arguments show scalars only; clip strings to keep the tree readable.
    args_suffix = ""
    if node.name.startswith(_TOOL_SPAN_PREFIX) and node.input_params:
        arg_parts = []
        for k, v in node.input_params.items():
            if isinstance(v, (int, float, bool)):
                arg_parts.append(f"{k}={v}")
            elif isinstance(v, str):
                arg_parts.append(f'{k}="{_clipped(v)}"')
        if arg_parts:
            args_suffix = f" ({', '.join(arg_parts)})"

    return f"{sid}{node.name}{args_suffix} {dur}{suffix}".strip()


def _render_tree(
    node: SpanNode | LeafEvent,
    prefix: str = "",
    is_last: bool = True,
) -> list[str]:
    connector = "└── " if is_last else "├── "

    if isinstance(node, LeafEvent):
        return [f"{prefix}{connector}{node.summary}"]

    line = _render_span_line(node)
    result = [f"{prefix}{connector}{line}"]

    child_prefix = prefix + ("    " if is_last else "│   ")
    for i, child in enumerate(node.children):
        child_is_last = i == len(node.children) - 1
        result.extend(_render_tree(child, child_prefix, child_is_last))

    return result


# ==================== PUBLIC API ====================


# Render the outcome of a request.summary payload, understanding both the current and the pre-2026-08-05 shape.
# Logs written before `outcome` existed carry a boolean `success`. Defaulting a missing
# `outcome` to OK made every archived failure render as OK — the exact lie the outcome field was
# introduced to remove, reappearing in the reader instead of the writer. Old files are the ones an
# agent opens when investigating something that already went wrong, so they matter most.
def _summary_status(sdata: dict[str, Any]) -> str:
    outcome = sdata.get("outcome")
    if isinstance(outcome, str):
        return outcome.upper()
    legacy_success = sdata.get("success")
    if isinstance(legacy_success, bool):
        return RequestOutcome.OK.value.upper() if legacy_success else "FAILED (legacy)"
    return "UNKNOWN"


# trace_id: Auto-detects if None.
def format_trace_for_llm(
    lines: Iterable[str],
    *,
    trace_id: str | None = None,
) -> str:
    events, meta = _parse_events(lines, trace_id)
    if not events:
        return f"({meta.get('selection_error', 'no events found')})"

    roots, summary = _build_tree(events)
    if not roots:
        return "(no spans found)"

    # The framework can handle a failure outside the span. Use its events and summary too.
    summary_data = (
        summary.get("data", {}) if summary and isinstance(summary.get("data"), dict) else {}
    )
    # Cancellation is distinct from a server failure.
    cancelled_status = RequestOutcome.CANCELLED.value.upper()
    summary_status = _summary_status(summary_data)
    trace_failed = any(
        ev.get("event_id", "").startswith(("critical.", "error."))
        or (ev.get("event_id") == "span.error" and ev.get("level") != "WARNING")
        for ev in events
    ) or summary_status not in _ROUTINE_STATUSES | {cancelled_status}
    trace_cancelled = not trace_failed and summary_status == cancelled_status

    # Header
    parts: list[str] = []
    tid = meta.get("trace_id", "")[:8]
    header_parts = [f"[Trace: {tid}]"]
    if meta.get("session_id"):
        header_parts.append(f"[Session: {meta['session_id']}]")
    if meta.get("user_id"):
        header_parts.append(f"[User: {meta['user_id']}]")
    parts.append(" ".join(header_parts))

    if meta.get("method"):
        parts.append(f"{meta['method']} {meta.get('path', '')}")

    parts.append("")  # blank line

    # Root spans
    for root in roots:
        # Root span line (no connector prefix)
        sid = f"[{root.span_id[:8]}] " if root.span_id else ""
        dur = f"({root.duration_ms}ms)" if root.duration_ms is not None else ""
        # The root needs its own rendering without a connector, including the error and location.
        if root.error:
            # A non-HTTP root can raise a domain rejection directly. Keep the child-span semantics.
            if root.interrupted:
                root_mark = "⊘"
            elif root.client_rejection:
                root_mark = "⚠"
            else:
                root_mark = "✗"
            mark = f" {root_mark} {root.error}"
            # Root failures carry their actionable location.
            if root.error_site:
                mark = f"{mark} at {root.error_site}"
        elif trace_failed:
            mark = " ✗"
        else:
            mark = " ⊘" if trace_cancelled else " ✓"
        parts.append(f"{sid}{root.name} {dur}{mark}".strip())

        # Children
        for i, child in enumerate(root.children):
            child_is_last = i == len(root.children) - 1
            parts.extend(_render_tree(child, "", child_is_last))

    # Summary line
    if summary:
        sdata = summary.get("data", {}) if isinstance(summary.get("data"), dict) else {}
        spans = sdata.get("child_span_count", 0)
        filtered = sdata.get("filtered_child_span_count", 0)
        hidden = (
            f" (+{filtered} below the log level, not written)"
            if isinstance(filtered, int) and filtered > 0
            else ""
        )
        llm_calls = sdata.get("llm_calls", 0)
        errors = sdata.get("error_count", 0)
        status = _summary_status(sdata)
        status_code = sdata.get("status_code")
        if status_code is not None:
            status = f"{status} {status_code}"
        parts.append(
            f"request.summary: {spans} spans{hidden}, {llm_calls} llm_calls, {errors} errors → {status}"
        )

        if hidden:
            parts.append("  Use level=logging.INFO for an important span you need in this trace.")

    return "\n".join(parts)


def format_all_traces_for_llm(lines: Iterable[str]) -> str:
    # Collect all events and discover distinct HTTP trace IDs (ordered by appearance).
    all_events: list[dict[str, Any]] = []
    http_trace_ids: list[str] = []
    seen_traces: set[str] = set()

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        tid = ev.get("trace_id")
        if not tid:
            continue
        all_events.append(ev)

        eid = ev.get("event_id", "")
        if eid == "span.start" and ev.get("span_name") == "http_request" and tid not in seen_traces:
            http_trace_ids.append(tid)
            seen_traces.add(tid)

    if not http_trace_ids:
        return "(no HTTP traces found)"

    # Cache lines as strings for reuse
    cached_lines = [json.dumps(ev) for ev in all_events]

    sections: list[str] = []
    for tid in http_trace_ids:
        tree = format_trace_for_llm(cached_lines, trace_id=tid)
        if tree and not tree.startswith("(no"):
            sections.append(tree)

    return "\n\n---\n\n".join(sections) if sections else "(no traces rendered)"


# Returns ordered HTTP trace ids, the subset that carries
# a failure, and the subset that was cancelled without failing.
def trace_inventory(lines: Iterable[str]) -> tuple[list[str], set[str], set[str]]:
    trace_ids: list[str] = []
    seen: set[str] = set()
    failed: set[str] = set()
    cancelled: set[str] = set()
    cancelled_status = RequestOutcome.CANCELLED.value.upper()

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        tid = ev.get("trace_id")
        if not tid:
            continue

        eid = ev.get("event_id", "")
        if eid == "span.start" and ev.get("span_name") == "http_request" and tid not in seen:
            trace_ids.append(tid)
            seen.add(tid)

        # Rejection and interruption share WARNING. A recorded failure takes precedence.
        if eid == "span.error":
            error_data = ev.get("data", {}) if isinstance(ev.get("data"), dict) else {}
            if ev.get("level") == "WARNING":
                if not error_data.get("client_rejection"):
                    cancelled.add(tid)
            else:
                failed.add(tid)
        elif eid.startswith("critical.") or eid.startswith("error."):
            failed.add(tid)
        elif eid == "request.summary":
            data = ev.get("data", {}) if isinstance(ev.get("data"), dict) else {}
            status = _summary_status(data)
            if status == cancelled_status:
                cancelled.add(tid)
            # A client rejection belongs outside the failure count.
            elif status not in _ROUTINE_STATUSES:
                failed.add(tid)

    return trace_ids, failed, cancelled - failed


def render_inventory_note(
    trace_ids: list[str],
    failed: set[str],
    shown_trace_id: str,
    cancelled: set[str] | None = None,
) -> str:
    # Explain which earlier traces the default selection hides.
    hidden = [tid for tid in trace_ids if tid != shown_trace_id]
    if not hidden:
        return ""
    hidden_failed = [tid for tid in hidden if tid in failed]
    hidden_cancelled = [tid for tid in hidden if tid in (cancelled or set())]
    note = f"({len(hidden)} more trace(s) in this log"
    if hidden_failed:
        preview = ", ".join(tid[:8] for tid in hidden_failed[:3])
        note = f"{note}, {len(hidden_failed)} with errors: {preview}{'…' if len(hidden_failed) > 3 else ''}"
    if hidden_cancelled:
        note = f"{note}, {len(hidden_cancelled)} cancelled"
    if hidden_failed or hidden_cancelled:
        return note + " — rerun with --all)"
    return note + " — rerun with --all to see them)"


def prepend_trace_summary(log_file_path: str) -> bool:
    from pathlib import Path

    path = Path(log_file_path)
    if not path.exists() or path.stat().st_size == 0:
        return False

    with open(path, encoding="utf-8") as f:
        original_lines = f.readlines()

    summary = format_all_traces_for_llm(original_lines)
    if summary.startswith("(no"):
        return False

    # Build header block as comment-style lines (non-JSON, prefixed with #)
    header_lines = [
        "# ==================== TRACE SUMMARY (LLM-friendly) ====================\n",
        "#\n",
    ]
    for line in summary.split("\n"):
        header_lines.append(f"# {line}\n")
    header_lines.append("#\n")
    header_lines.append(
        "# ==================== RAW NDJSON BELOW ====================\n",
    )

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(header_lines)
        f.writelines(original_lines)

    return True


# ==================== CLI ====================


def _cli() -> None:
    """CLI entry point: python -m project.core.logging.trace_formatter [file] [--trace ID]."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Transform NDJSON logs into LLM-friendly text tree.",
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="NDJSON log file path (reads stdin if omitted)",
    )
    parser.add_argument(
        "--trace",
        default=None,
        help="Filter by trace_id (auto-detects last HTTP trace if omitted)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Render every HTTP trace in the file instead of only the last one",
    )
    args = parser.parse_args()

    if args.file:
        with open(args.file) as f:
            lines = f.readlines()
    else:
        lines = sys.stdin.readlines()

    # Docker Compose prefixes can hide every JSON line. Explain the input issue explicitly.
    if lines and not any(line.lstrip().startswith("{") for line in lines):
        print(
            f"(no NDJSON found in {len(lines)} input line(s) — every line was skipped. "
            "If this came from `docker compose logs`, re-run it with --no-log-prefix.)"
        )
        return

    if args.all:
        print(format_all_traces_for_llm(lines))
        return

    _, meta = _parse_events(lines, args.trace)
    print(format_trace_for_llm(lines, trace_id=args.trace))

    # Earlier failures should remain discoverable from a green last trace.
    trace_ids, failed, cancelled = trace_inventory(lines)
    note = render_inventory_note(trace_ids, failed, meta.get("trace_id", ""), cancelled)
    if note:
        print()
        print(note)


if __name__ == "__main__":
    _cli()
