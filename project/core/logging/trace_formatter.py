# FILE: project/core/logging/trace_formatter.py
# SUMMARY: NDJSON-to-text tree transformer for LLM-friendly trace visualization.

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from project.core.logging.enums import RequestOutcome


# ==================== DATA STRUCTURES ====================


# CLASS: SpanNode
# SUMMARY: A finished or errored span with optional children forming a trace tree.
@dataclass
class SpanNode:
    span_id: str
    name: str
    duration_ms: float | None = None
    parent_span_id: str | None = None
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_site: str | None = None
    # ATTRIBUTE: interrupted (bool)
    # SUMMARY: The span.error arrived at WARNING — a cancellation, not the application's failure.
    interrupted: bool = False
    seq: int = 0
    children: list[SpanNode | LeafEvent] = field(default_factory=list)


# CLASS: LeafEvent
# SUMMARY: A non-span event (llm.call, metric, api.call) attached to a parent span.
@dataclass
class LeafEvent:
    span_id: str
    summary: str
    seq: int = 0


# ==================== PARSING ====================


# FUNCTION: _parse_events
# SUMMARY: Parse NDJSON lines, filter by trace_id, and return structured event dicts.
# INPUT: trace_id (str | None): Optional trace filter; if None, auto-detect first HTTP trace.
# OUTPUT: (tuple): (filtered_events, trace_meta) where trace_meta has header info.
def _parse_events(
    lines: Iterable[str],
    trace_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_events: list[dict[str, Any]] = []
    auto_trace_id: str | None = trace_id

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

        # Auto-detect: track the last trace that has an http_request span.start
        if trace_id is None:
            eid = ev.get("event_id", "")
            if eid == "span.start" and ev.get("span_name") == "http_request":
                auto_trace_id = tid

        all_events.append(ev)

    if auto_trace_id is None and all_events:
        # Fallback: use the last trace_id seen
        auto_trace_id = all_events[-1].get("trace_id")

    # Filter to target trace
    filtered = [ev for ev in all_events if ev.get("trace_id") == auto_trace_id]

    # Extract header metadata from the trace's first span.start event.
    # **LOGIC_STEP**: Second place that assumed the root has no parent. It does have one — the
    # application_lifecycle span — so the method and path never reached the header and every
    # rendered trace was identified only by a hex id. The first span.start of a filtered trace is
    # its root by construction: everything above it was dropped for having no trace_id.
    meta: dict[str, Any] = {"trace_id": auto_trace_id or ""}
    for ev in filtered:
        if ev.get("event_id") == "span.start":
            meta["session_id"] = ev.get("session_id", "")
            meta["user_id"] = ev.get("user_id", "")
            data = ev.get("data", {})
            # **LOGIC_STEP**: Third mismatch with what the emitter writes. middleware.py nests the
            # request facts under data.input_params; this read expected them at the top level, so
            # even a correctly-rooted trace rendered without its method and path. Both spellings
            # are accepted so a span that logs them flat still works.
            params = (
                data.get("input_params", {}) if isinstance(data.get("input_params"), dict) else {}
            )
            meta["method"] = data.get("method") or params.get("method", "")
            meta["path"] = data.get("path") or params.get("path", "")
            break

    return filtered, meta


# ATTRIBUTE: _VENDOR_MARKERS (tuple[str, ...])
# SUMMARY: Path fragments that mark a frame as somebody else's code.
_VENDOR_MARKERS = ("site-packages", "/.venv/", "<frozen ")


# FUNCTION: _last_own_frame
# SUMMARY: Reduce a traceback to the deepest frame belonging to this repository.
# INPUT: traceback_text (str | None): Value of the span.error event's `exc_traceback` field.
# OUTPUT: (str | None): "path/to/file.py:LINE in func", or None when there is no own frame.
def _last_own_frame(traceback_text: str | None) -> str | None:
    # **LOGIC_STEP**: The whole traceback is in the log and none of it reached the reader — the
    # rendered tree named the exception type and left the location out, so an agent had the word
    # "KeyError" and 24 frames to grep for. One frame is what it needs: the deepest one that is
    # not vendored. Twenty-four frames in the measured case, six of them ours.
    if not traceback_text:
        return None

    site: str | None = None
    for raw in traceback_text.splitlines():
        line = raw.strip()
        if not line.startswith('File "'):
            continue
        if any(marker in line for marker in _VENDOR_MARKERS):
            continue
        try:
            path = line.split('"')[1]
            rest = line.split(", line ", 1)[1]
            number, _, func = rest.partition(", in ")
        except IndexError:
            continue
        # **LOGIC_STEP**: Absolute paths make the line unusable as a grep target on another
        # machine; keep the repository-relative tail.
        if "/project/" in path:
            path = "project/" + path.split("/project/", 1)[1]
        site = f"{path}:{number.strip()}" + (f" in {func.strip()}" if func else "")
    return site


# ==================== TREE BUILDING ====================


# FUNCTION: _build_tree
# SUMMARY: Construct span tree from parsed events and attach leaf events.
# OUTPUT: (tuple): (root_spans, summary_event) where root_spans are top-level SpanNodes.
def _build_tree(
    events: list[dict[str, Any]],
) -> tuple[list[SpanNode], dict[str, Any] | None]:
    spans: dict[str, SpanNode] = {}
    leaves: list[LeafEvent] = []
    summary: dict[str, Any] | None = None

    for ev in events:
        eid = ev.get("event_id", "")
        data = ev.get("data", {}) if isinstance(ev.get("data"), dict) else {}
        seq = ev.get("seq", 0)

        if eid == "span.finish":
            sid = ev.get("span_id", "")
            node = spans.get(sid)
            if node is None:
                node = SpanNode(span_id=sid, name=ev.get("span_name", "?"))
                spans[sid] = node
            node.duration_ms = ev.get("duration_ms")
            node.parent_span_id = ev.get("parent_span_id")
            node.output = data.get("output", {}) if isinstance(data.get("output"), dict) else {}
            node.seq = seq

        elif eid == "span.error":
            sid = ev.get("span_id", "")
            node = spans.get(sid)
            if node is None:
                node = SpanNode(span_id=sid, name=ev.get("span_name", "?"))
                spans[sid] = node
            node.duration_ms = ev.get("duration_ms")
            node.parent_span_id = ev.get("parent_span_id")
            node.error = data.get("exception_type", data.get("error_message", "error"))
            node.error_site = _last_own_frame(ev.get("exc_traceback"))
            # **LOGIC_STEP**: logger.span writes an interruption — CancelledError at uvicorn's
            # shutdown timeout, KeyboardInterrupt — as span.error at WARNING, an application
            # failure at ERROR. The level is the one field that tells them apart here.
            node.interrupted = ev.get("level") == "WARNING"
            node.seq = seq

        elif eid == "request.summary":
            summary = ev

        elif eid == "llm.call":
            model = data.get("model", "?")
            dur = ev.get("duration_ms", data.get("duration_ms"))
            ok = data.get("success", True)
            mark = "✓" if ok else "✗"
            dur_str = f"({dur}ms) " if dur is not None else ""
            leaves.append(
                LeafEvent(
                    span_id=ev.get("span_id", ""),
                    summary=f"llm.call {model} {dur_str}{mark}",
                    seq=seq,
                )
            )

        elif eid.startswith("critical.") or eid.startswith("error."):
            # **LOGIC_STEP**: These carry the actual cause of a failed request. Without this
            # branch they were parsed and then silently discarded while the tree still rendered,
            # so a reader saw a shaped trace with no reason in it. Attach them as leaves so the
            # exception type and message appear where the failure happened.
            failure = data.get("failure_type") or data.get("error_type") or eid
            exception_type = data.get("exception_type", "")
            detail = data.get("exception_message") or data.get("message") or ""
            mark = "✗✗" if eid.startswith("critical.") else "✗"
            head = f"{mark} {failure}"
            if exception_type:
                head = f"{head} [{exception_type}]"
            leaves.append(
                LeafEvent(
                    span_id=ev.get("span_id", ""),
                    summary=f"{head}: {detail}" if detail else head,
                    seq=seq,
                )
            )

        elif eid.startswith("metric."):
            name = data.get("metric_name", eid)
            val = data.get("value", "?")
            unit = data.get("unit", "")
            leaves.append(
                LeafEvent(
                    span_id=ev.get("span_id", ""),
                    summary=f"metric {name}={val}{unit}",
                    seq=seq,
                )
            )

        elif eid.startswith("api.call."):
            # Extract HTTP method from event_id (api.call.POST.http://...) or data
            eid_tail = eid[len("api.call.") :]  # "POST.http://..."
            method = eid_tail.split(".")[0] if eid_tail else data.get("method", "?")
            endpoint = data.get("endpoint", "")
            # Use short service name from last URL path segment
            service = endpoint.rsplit("/", 1)[-1] if "/" in endpoint else endpoint
            status = data.get("status_code", "?")
            dur = ev.get("duration_ms", data.get("duration_ms"))
            dur_str = f"({dur}ms) " if dur is not None else ""
            leaves.append(
                LeafEvent(
                    span_id=ev.get("span_id", ""),
                    summary=f"api.call {method} {service} {dur_str}→ {status}",
                    seq=seq,
                )
            )

    # Attach leaves to their parent span
    leaf_by_span: dict[str, list[LeafEvent]] = defaultdict(list)
    for leaf in leaves:
        leaf_by_span[leaf.span_id].append(leaf)

    # Build parent-child relationships
    roots: list[SpanNode] = []
    for node in spans.values():
        # Attach leaf events as children
        if node.span_id in leaf_by_span:
            node.children.extend(leaf_by_span[node.span_id])

        # **LOGIC_STEP**: A span whose parent is not in this trace is a root of it. Requiring
        # parent_span_id to be None made every real log render as "(no spans found)": the
        # application's http_request span is a child of application_lifecycle, and that parent
        # carries no trace_id, so _parse_events drops it one step earlier. The span then matched
        # neither branch and disappeared — no roots, no tree, and prepend_trace_summary returning
        # False on every shutdown without saying so. Measured on a live NDJSON with four traces.
        if node.parent_span_id is None or node.parent_span_id not in spans:
            roots.append(node)
        else:
            spans[node.parent_span_id].children.append(node)

    roots.sort(key=lambda n: n.seq)

    # **LOGIC_STEP**: Attach orphan leaves — events logged outside any span, which is exactly
    # where the unhandled-exception record lands: the span has already closed by the time the
    # framework's exception handler runs. Dropping them hid the cause of every 500.
    orphans = [leaf for leaf in leaves if leaf.span_id not in spans]
    if orphans and roots:
        roots[0].children.extend(orphans)

    # Sort children by seq for correct ordering
    for node in spans.values():
        node.children.sort(key=lambda c: c.seq)

    return roots, summary


# ==================== RENDERING ====================


# FUNCTION: _render_span_line
# SUMMARY: Render a single span node as a compact text line.
def _render_span_line(node: SpanNode) -> str:
    sid = f"[{node.span_id[:8]}] " if node.span_id else ""
    dur = f"({node.duration_ms}ms)" if node.duration_ms is not None else ""
    if node.error:
        # **LOGIC_STEP**: ⊘ for a span that was stopped, ✗ for one that failed. The same mark
        # on both put a cancelled request next to a 500 with nothing to tell them apart.
        suffix = f" {'⊘' if node.interrupted else '✗'} {node.error}"
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
    return f"{sid}{node.name} {dur}{suffix}".strip()


# FUNCTION: _render_tree
# SUMMARY: Recursively render a span tree with box-drawing characters.
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


# FUNCTION: _summary_status
# SUMMARY: Render the outcome of a request.summary payload, understanding both the current and the pre-2026-08-05 shape.
# NOTE: Logs written before `outcome` existed carry a boolean `success`. Defaulting a missing
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


# FUNCTION: format_trace_for_llm
# SUMMARY: Transform NDJSON log lines into a compact LLM-friendly text tree.
# INPUT: lines (Iterable[str]): NDJSON log lines (file, stdin, list).
# INPUT: trace_id (str | None): Optional trace ID filter; auto-detects if None.
def format_trace_for_llm(
    lines: Iterable[str],
    *,
    trace_id: str | None = None,
) -> str:
    events, meta = _parse_events(lines, trace_id)
    if not events:
        return "(no events found)"

    roots, summary = _build_tree(events)
    if not roots:
        return "(no spans found)"

    # **LOGIC_STEP**: A request can fail without the root span raising: the framework catches the
    # exception outside the span, so span.finish is emitted normally and only the summary and the
    # critical record know it went wrong. Deciding the root mark from span.error alone printed a
    # ✓ on top of a 500.
    summary_data = (
        summary.get("data", {}) if summary and isinstance(summary.get("data"), dict) else {}
    )
    # **LOGIC_STEP**: A cancelled request is neither a success nor the application's failure.
    # Its span.error arrives at WARNING and its summary says `cancelled`; counting either as a
    # failure put the ✗ of a 500 on a request the server was told to stop — the confusion the
    # WARNING level exists to avoid.
    cancelled_status = RequestOutcome.CANCELLED.value.upper()
    summary_status = _summary_status(summary_data)
    trace_failed = any(
        ev.get("event_id", "").startswith(("critical.", "error."))
        or (ev.get("event_id") == "span.error" and ev.get("level") != "WARNING")
        for ev in events
    ) or summary_status not in {RequestOutcome.OK.value.upper(), "UNKNOWN", cancelled_status}
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
        # **LOGIC_STEP**: Child spans render their error text via _render_span_line; the root
        # printed a bare ✗ and swallowed it, so a failed request showed the mark and nothing else.
        if root.error:
            mark = f" {'⊘' if root.interrupted else '✗'} {root.error}"
            # **LOGIC_STEP**: The root is where a failed request's exception lands, so this is the
            # one line that must carry the location. Duplicated from _render_span_line rather than
            # shared because the root has no connector prefix — the same reason the error text
            # itself was missing here until it was added by hand.
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
        llm_calls = sdata.get("llm_calls", 0)
        errors = sdata.get("error_count", 0)
        status = _summary_status(sdata)
        status_code = sdata.get("status_code")
        if status_code is not None:
            status = f"{status} {status_code}"
        parts.append(
            f"request.summary: {spans} spans, {llm_calls} llm_calls, {errors} errors → {status}"
        )

    return "\n".join(parts)


# FUNCTION: format_all_traces_for_llm
# SUMMARY: Transform NDJSON log lines into compact text trees for ALL HTTP traces in the file.
# OUTPUT: (str): Concatenated compact text trees separated by blank lines.
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


# FUNCTION: trace_inventory
# SUMMARY: List the HTTP traces present in a log and mark which of them failed or were cancelled.
# INPUT: lines (Iterable[str]): NDJSON log lines.
# OUTPUT: (tuple[list[str], set[str], set[str]]): Ordered HTTP trace ids, the subset that carries
#         a failure, and the subset that was cancelled without failing.
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

        # **LOGIC_STEP**: The same split as format_trace_for_llm: a span.error at WARNING is an
        # interruption, and a summary saying `cancelled` is not a failure. A trace that both
        # failed and was cancelled counts as failed — the failure is the older, more useful fact.
        if eid == "span.error" and ev.get("level") == "WARNING":
            cancelled.add(tid)
        elif eid == "span.error" or eid.startswith("critical.") or eid.startswith("error."):
            failed.add(tid)
        elif eid == "request.summary":
            data = ev.get("data", {}) if isinstance(ev.get("data"), dict) else {}
            status = _summary_status(data)
            if status == cancelled_status:
                cancelled.add(tid)
            elif status not in {RequestOutcome.OK.value.upper(), "UNKNOWN"}:
                failed.add(tid)

    return trace_ids, failed, cancelled - failed


# FUNCTION: render_inventory_note
# SUMMARY: Build the one-line note telling the reader what the single-trace view is not showing.
# INPUT: shown_trace_id (str): Trace id that was rendered.
# INPUT: cancelled (set[str] | None): Traces that were cancelled; named separately from failures.
# OUTPUT: (str): Note text, or an empty string when the log holds nothing else worth mentioning.
def render_inventory_note(
    trace_ids: list[str],
    failed: set[str],
    shown_trace_id: str,
    cancelled: set[str] | None = None,
) -> str:
    # **LOGIC_STEP**: The default view renders one trace — the last HTTP one. A failure in any
    # earlier request was therefore invisible, and a reader who saw a green tree concluded the
    # run was fine. Say out loud what is being hidden.
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


# FUNCTION: prepend_trace_summary
# SUMMARY: Read an NDJSON log file, generate compact trace trees, and prepend them to the file.
# OUTPUT: (bool): True if summary was prepended, False if no HTTP traces found or file missing.
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

    # **LOGIC_STEP**: Distinguish "nothing matched" from "nothing was NDJSON at all". Piping
    # `docker compose logs` without --no-log-prefix feeds every line through prefixed with the
    # service name; the parser then skips all of them and the old code printed a bare
    # "(no events found)", which reads as "the service logged nothing".
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

    # **LOGIC_STEP**: Tell the reader what this view leaves out. Without it the default render of
    # a green last request reads as "the whole run was fine" even when an earlier one failed.
    trace_ids, failed, cancelled = trace_inventory(lines)
    note = render_inventory_note(trace_ids, failed, meta.get("trace_id", ""), cancelled)
    if note:
        print()
        print(note)


if __name__ == "__main__":
    _cli()
