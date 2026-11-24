# FILE: project/core/logging/trace_tree.py
# SUMMARY: Parses NDJSON trace events into the SpanNode/LeafEvent tree trace_formatter.py renders.
#
# trace_formatter re-exports its former parser symbols for existing callers.

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator


# ==================== DATA STRUCTURES ====================


@dataclass
class SpanNode:
    span_id: str
    name: str
    duration_ms: float | None = None
    parent_span_id: str | None = None
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_site: str | None = None
    # The span.error arrived at WARNING because something not an Exception cut it short —
    # a cancellation — never because it was a routine domain rejection. See client_rejection.
    interrupted: bool = False
    # WARNING also represents interruption. Old logs have no explicit rejection marker.
    client_rejection: bool = False
    # The span's own `span.start.data.input_params`, captured only for spans named
    # `agent.tool.<name>` — see _TOOL_SPAN_PREFIX below. Empty for every other span; nothing else
    # in this renderer reads it.
    input_params: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    children: list[SpanNode | LeafEvent] = field(default_factory=list)


# A non-span event (llm.call, metric, api.call) attached to a parent span.
@dataclass
class LeafEvent:
    span_id: str
    summary: str
    seq: int = 0


_VENDOR_MARKERS = ("site-packages", "/.venv/", "<frozen ")

# Only tool spans expose selected inputs in the compact tree. DB spans show an outcome;
# HTTP and DB inputs can contain large query mappings or parameter lists.
_TOOL_SPAN_PREFIX = "agent.tool."

_LEAF_MARKS = {"critical.": "✗✗", "client_error.": "⚠", "error.": "✗"}


# traceback_text: Value of the span.error event's exc_traceback field.
# Returns "path/to/file.py:LINE in func", or None when there is no own frame.
def _last_own_frame(traceback_text: str | None) -> str | None:
    # A trace points to the deepest frame in the service to keep the location actionable.
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
        # Absolute paths make the line unusable as a grep target on another
        # machine; keep the repository-relative tail.
        if "/project/" in path:
            path = "project/" + path.split("/project/", 1)[1]
        site = f"{path}:{number.strip()}" + (f" in {func.strip()}" if func else "")
    return site


# ==================== PARSING ====================


# Each line parsed once, for every reader: an event is a JSON object with a non-empty string
# trace_id; blank, broken and trace-less lines are skipped.
def _trace_events(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if isinstance(ev, dict) and isinstance(ev.get("trace_id"), str) and ev["trace_id"]:
            yield ev


# Returns (filtered_events, trace_meta) where trace_meta has header info.
def _parse_events(
    lines: Iterable[str],
    trace_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_events: list[dict[str, Any]] = []
    auto_trace_id: str | None = trace_id

    for ev in _trace_events(lines):
        tid = ev["trace_id"]
        # Auto-detect: track the last trace that has an http_request span.start
        if trace_id is None:
            eid = ev.get("event_id", "")
            if eid == "span.start" and ev.get("span_name") == "http_request":
                auto_trace_id = tid

        all_events.append(ev)

    if auto_trace_id is None and all_events:
        # Fallback: use the last trace_id seen
        auto_trace_id = all_events[-1].get("trace_id")

    if trace_id is not None:
        trace_ids = list(dict.fromkeys(ev["trace_id"] for ev in all_events))
        if trace_id not in trace_ids:
            matches = [candidate for candidate in trace_ids if candidate.startswith(trace_id)]
            if len(matches) == 1:
                auto_trace_id = matches[0]
            elif matches:
                candidates = ", ".join(
                    candidate[: max(8, len(trace_id) + 1)] for candidate in matches
                )
                return [], {
                    "selection_error": f"{len(matches)} traces start with {trace_id}: "
                    f"{candidates} — give more characters"
                }
            else:
                return [], {
                    "selection_error": f"no trace {trace_id} in this log ({len(trace_ids)} traces)"
                }

    # Filter to target trace
    filtered = [ev for ev in all_events if ev.get("trace_id") == auto_trace_id]
    return filtered, _trace_meta(auto_trace_id or "", filtered)


# The header facts of one trace's events — one place for the single and the all-traces view.
def _trace_meta(trace_id: str, filtered: list[dict[str, Any]]) -> dict[str, Any]:
    # The first start event has the request metadata. Its lifecycle parent has no trace ID.
    meta: dict[str, Any] = {"trace_id": trace_id}
    for ev in filtered:
        if ev.get("event_id") == "span.start":
            meta["session_id"] = ev.get("session_id", "")
            meta["user_id"] = ev.get("user_id", "")
            data = ev.get("data", {})
            # Accept both the middleware's nested inputs and the flat format used by older spans.
            params = (
                data.get("input_params", {}) if isinstance(data.get("input_params"), dict) else {}
            )
            meta["method"] = data.get("method") or params.get("method", "")
            meta["path"] = data.get("path") or params.get("path", "")
            break
    return meta


# ==================== TREE BUILDING ====================


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

        if eid == "span.start" and ev.get("span_name", "").startswith(_TOOL_SPAN_PREFIX):
            # Build tool nodes from the start event to keep inputs even when the call is interrupted.
            sid = ev.get("span_id", "")
            node = spans.get(sid)
            if node is None:
                node = SpanNode(span_id=sid, name=ev.get("span_name", "?"))
                spans[sid] = node
            params = data.get("input_params")
            if isinstance(params, dict):
                node.input_params = params
            # Use the start position until a finish or error records the final position.
            node.seq = seq
            # Keep an unfinished tool attached to its request. A finish/error can update the parent.
            node.parent_span_id = ev.get("parent_span_id")

        elif eid == "span.finish":
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
            node.client_rejection = bool(data.get("client_rejection", False))
            # A rejection's error_message is its type and the field it names (ADR-013), which
            # says more than the type alone; a failure's is raw text, so its type is shown.
            if node.client_rejection:
                node.error = data.get("error_message") or data.get("exception_type", "error")
            else:
                node.error = data.get("exception_type", data.get("error_message", "error"))
            node.error_site = _last_own_frame(ev.get("exc_traceback"))
            # WARNING with a rejection marker is a 4xx; unmarked WARNING is an interruption.
            node.interrupted = ev.get("level") == "WARNING" and not node.client_rejection
            node.seq = seq

        elif eid == "request.summary":
            summary = ev

        elif eid == "llm.call":
            model = data.get("model", "?")
            dur = ev.get("duration_ms", data.get("duration_ms"))
            ok = data.get("success", True)
            # Successful transport can still carry a truncated answer. Mark that outcome separately.
            finish_reason = data.get("finish_reason")
            truncated = bool(ok) and finish_reason == "length"
            mark = "✗" if not ok else ("⚠" if truncated else "✓")
            dur_str = f"({dur}ms) " if dur is not None else ""
            reason_str = " finish_reason=length" if truncated else ""
            leaves.append(
                LeafEvent(
                    span_id=ev.get("span_id", ""),
                    summary=f"llm.call {model} {dur_str}{mark}{reason_str}",
                    seq=seq,
                )
            )

        elif eid.startswith(("critical.", "error.", "client_error.")):
            # Attach the failure detail to the span so the reader can see why the request failed.
            failure = data.get("failure_type") or data.get("error_type") or eid
            exception_type = data.get("exception_type", "")
            detail = (
                data.get("exception_message") or data.get("rejection") or data.get("message") or ""
            )
            # A 422 names its fields and why each failed; the values stay out (ADR-013).
            failed_fields = data.get("validation_errors")
            if isinstance(failed_fields, list) and failed_fields:
                named = ", ".join(
                    f"{item.get('field')} ({item.get('type')})"
                    for item in failed_fields
                    if isinstance(item, dict)
                )
                detail = f"{detail}: {named}" if detail else named
            # Give a routine client rejection a distinct mark and keep its cause visible.
            mark = next(m for prefix, m in _LEAF_MARKS.items() if eid.startswith(prefix))
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

        # A missing parent can be the lifecycle span outside this trace. Treat the request as a root.
        if node.parent_span_id is None or node.parent_span_id not in spans:
            roots.append(node)
        else:
            spans[node.parent_span_id].children.append(node)

    roots.sort(key=lambda n: n.seq)

    # The exception handler logs outside the closed span. Attach that cause to the request root.
    orphans = [leaf for leaf in leaves if leaf.span_id not in spans]
    if orphans and roots:
        roots[0].children.extend(orphans)

    # Sort children by seq for correct ordering
    for node in spans.values():
        node.children.sort(key=lambda c: c.seq)

    return roots, summary
