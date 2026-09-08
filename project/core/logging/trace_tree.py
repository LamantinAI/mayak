# FILE: project/core/logging/trace_tree.py
# SUMMARY: Parses NDJSON trace events into the SpanNode/LeafEvent tree trace_formatter.py renders.
#
# Split out of trace_formatter.py on 2026-09-08, purely to stay under
# scripts/validate_module_sizes.py's per-module executable-line budget — the tool-span and
# domain-rejection work landed in the same commit as the pre-existing "not genuinely large, mostly
# comments" file ai_context/line_metrics.py used trace_formatter.py itself as its worked example
# of, and that comment is now describing a smaller file than the one it names; whoever next edits
# ai_context/line_metrics.py should re-measure rather than trust the old figure. No behavior moved
# — trace_formatter.py's public functions call straight through to this module's, and every test
# that imported from trace_formatter.py before this split still does; SpanNode, LeafEvent,
# _build_tree and _parse_events are re-exported there unchanged.

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable


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
    # SUMMARY: The span.error arrived at WARNING because something not an Exception cut it short —
    # a cancellation — never because it was a routine domain rejection. See client_rejection.
    interrupted: bool = False
    # ATTRIBUTE: client_rejection (bool)
    # SUMMARY: The span.error carried `client_rejection: true` — a ConflictError, NotFoundError or
    # similar raised inside this span that exception_handlers.py answers with a 4xx, not the
    # application failing. Also WARNING, like `interrupted`, and kept as its own field rather than
    # inferred from the level so the two are never rendered as the same thing: one means "stopped",
    # the other means "this is the application working as intended". Added 2026-09-08 alongside
    # project.core.logging.logger.span()'s own classification; absent on older logs, which read as
    # False and keep rendering exactly as they did before this field existed.
    client_rejection: bool = False
    # ATTRIBUTE: input_params (dict[str, Any])
    # SUMMARY: The span's own `span.start.data.input_params`, captured only for spans named
    # `agent.tool.<name>` — see _TOOL_SPAN_PREFIX below. Empty for every other span; nothing else
    # in this renderer reads it.
    input_params: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    children: list[SpanNode | LeafEvent] = field(default_factory=list)


# CLASS: LeafEvent
# SUMMARY: A non-span event (llm.call, metric, api.call) attached to a parent span.
@dataclass
class LeafEvent:
    span_id: str
    summary: str
    seq: int = 0


# ATTRIBUTE: _VENDOR_MARKERS (tuple[str, ...])
# SUMMARY: Path fragments that mark a frame as somebody else's code.
_VENDOR_MARKERS = ("site-packages", "/.venv/", "<frozen ")

# ATTRIBUTE: _TOOL_SPAN_PREFIX (str)
# SUMMARY: Span-name prefix for one tool call inside an agent loop — see the convention documented
# beside project.core.logging.logger.SemanticLogger.span.
# NOTE: Added 2026-09-08. The compact renderer showed a span's name and duration and nothing about
# what it did; for `db.*` spans that is enough because `output` carries the outcome (`row_found`,
# `rows_written`, …), but a tool call's interesting fact is what it was CALLED WITH, and that lives
# in `input_params` on `span.start`, which `_build_tree` otherwise never reads. Scoped to this one
# prefix rather than to every span: `input_params` on an `http_request` or `db.*` span can hold a
# full query-string mapping or a SQL parameter list, and rendering those inline in every trace
# would be the noise trace_formatter.py's own "compact" goal exists to avoid. Imported into
# trace_formatter.py, which needs the same prefix to decide what to render, not just what to parse.
_TOOL_SPAN_PREFIX = "agent.tool."

# ATTRIBUTE: _LEAF_MARKS (dict[str, str])
# SUMMARY: The mark a failure-shaped leaf carries, keyed by its event-id prefix.
_LEAF_MARKS = {"critical.": "✗✗", "client_error.": "⚠", "error.": "✗"}


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

        if eid == "span.start" and ev.get("span_name", "").startswith(_TOOL_SPAN_PREFIX):
            # **LOGIC_STEP**: The only event carrying `input_params`, and it arrives before
            # span.finish/span.error — this is what creates the node early for a tool span. Every
            # other span name is left alone here exactly as before this branch existed: no node is
            # created from a plain span.start, so an unrelated span with no finish or error still
            # renders as absent rather than as an empty line.
            sid = ev.get("span_id", "")
            node = spans.get(sid)
            if node is None:
                node = SpanNode(span_id=sid, name=ev.get("span_name", "?"))
                spans[sid] = node
            params = data.get("input_params")
            if isinstance(params, dict):
                node.input_params = params
            # **LOGIC_STEP**: A provisional position, overwritten by span.finish/span.error's own
            # seq once one of those arrives (both always run after span.start). Left at 0 — the
            # dataclass default — a tool call that never finished would sort before every sibling
            # regardless of when it actually started, which is worse than an approximate position.
            node.seq = seq

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
            node.error = data.get("exception_type", data.get("error_message", "error"))
            node.error_site = _last_own_frame(ev.get("exc_traceback"))
            # **LOGIC_STEP**: Three different reasons a span ends in span.error, and a reader needs
            # to tell them apart at a glance rather than by grepping the exception type: the
            # application's own failure (ERROR, full traceback), an interruption — asyncio
            # cancellation, Ctrl-C — cut it short (WARNING, `client_rejection` absent), and a
            # routine domain rejection heading for a 4xx (also WARNING, `client_rejection: true`).
            # Before this field existed both WARNING cases looked identical here, so a duplicate
            # name inside a nested span rendered with ⊘ — the "stopped" mark a cancelled request
            # gets — which was as misleading as the ERROR-plus-traceback it replaced.
            node.client_rejection = bool(data.get("client_rejection", False))
            node.interrupted = ev.get("level") == "WARNING" and not node.client_rejection
            node.seq = seq

        elif eid == "request.summary":
            summary = ev

        elif eid == "llm.call":
            model = data.get("model", "?")
            dur = ev.get("duration_ms", data.get("duration_ms"))
            ok = data.get("success", True)
            # **LOGIC_STEP**: `finish_reason == "length"` on an otherwise-successful call means the
            # provider was cut off mid-answer by an output-token or tool-schema limit — the exact
            # shape that read as a plain ✓ here until 2026-09-08, because success and finish_reason
            # are two different facts on the same response and only the first was ever rendered.
            # This is the compact-trace half of the fix; log_llm_call carries the same field (and
            # the WARNING level) into the raw NDJSON itself.
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
            # **LOGIC_STEP**: These carry the actual cause of a failed request. Without this
            # branch they were parsed and then silently discarded while the tree still rendered,
            # so a reader saw a shaped trace with no reason in it. Attach them as leaves so the
            # exception type and message appear where the failure happened.
            failure = data.get("failure_type") or data.get("error_type") or eid
            exception_type = data.get("exception_type", "")
            detail = data.get("exception_message") or data.get("message") or ""
            # **LOGIC_STEP**: A 4xx is the application working — it read a request it could
            # not serve and said so — so it is marked apart from a failure rather than sharing
            # the ✗ of one. It is rendered at all because the alternative, silence, is worse: the
            # rejection's cause was the one thing a reader opened the trace for, and when these
            # records moved to `client_error.` on 2026-09-06 they matched no branch here and were
            # parsed and dropped.
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
