# FILE: project/core/serialization.py
# SUMMARY: High-performance serialization utilities using orjson for structured data handling.

import dataclasses
import reprlib
from enum import Enum
from typing import Any, Optional, Set

import orjson

# ==================== CONFIGURATION ====================

# SUMMARY: Shared repr helper configured to keep fallback serialization output bounded.
_REPR = reprlib.Repr()
_REPR.maxstring = 2000
_REPR.maxother = 2000

# SUMMARY: Sensitive key fragments that trigger value redaction during structured serialization.
REDACT_KEYS = {
    "password",
    "token",
    "secret",
    "authorization",
    "api_key",
    "private_key",
    "credentials",
    "jwt",
    "bearer",
    "cookie",
}

# SUMMARY: Exact field names that would otherwise match REDACT_KEYS by substring
#          but are non-sensitive metadata (e.g. LLM token counters, not auth tokens).
#          These must never be redacted so observability / cost analysis keeps numbers.
REDACT_EXEMPT_KEYS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "reasoning_tokens",
    "tokens_per_second",
    # The two names below are the span-level accumulators written by
    # logger._emit_request_summary — a different naming scheme from the per-call names emitted
    # by llm.call, so leaving them out of this set reports "total_input_tokens":
    # "***REDACTED***" next to a plain integer "input_tokens" from the same run. Guarded by
    # tests/application/test_serialization.py.
    "total_input_tokens",
    "total_output_tokens",
}

# SUMMARY: Maximum number of collection items preserved during safe serialization.
# Performance optimization constants
MAX_COLLECTION_SIZE = 100
# SUMMARY: Maximum recursion depth preserved during safe serialization.
MAX_DEPTH = 5
# SUMMARY: Maximum string length preserved before truncation is applied.
MAX_STRING_LENGTH = 2000

# ==================== UTILITIES ====================


# SUMMARY: Check if a key should be redacted based on sensitive keywords.
# OUTPUT: (bool): True if the key should be redacted, False otherwise.
def _redact_key(k: str) -> bool:
    # Normalize key format for consistent checking.
    lk = k.lower().replace("-", "_")
    # Allow explicit exempt names through even if they substring-match
    #                 a sensitive keyword (e.g. `input_tokens` contains `token`).
    if lk in REDACT_EXEMPT_KEYS:
        return False
    # Check if key contains any sensitive keywords.
    return any(s in lk for s in REDACT_KEYS)


# SUMMARY: Optimized serialization using orjson for high-performance structured data handling.
# INPUT: _depth (int): Current recursion depth for circular reference protection.
# INPUT: _seen (Optional[Set[int]]): Set of seen object IDs for circular reference detection.
# OUTPUT: (Any): Serialized object safe for structured logging output.
def safe_serialize(obj: Any, _depth: int = 0, _seen: Optional[Set[int]] = None) -> Any:
    if _seen is None:
        _seen = set()

    # Prevent infinite recursion and limit depth
    if _depth > MAX_DEPTH or id(obj) in _seen:
        return _REPR.repr(obj)

    _seen.add(id(obj))

    try:
        # Handle dataclass serialization with orjson optimization.
        if dataclasses.is_dataclass(obj):
            return safe_serialize(dataclasses.asdict(obj), _depth + 1, _seen)  # type: ignore

        # Handle dictionary serialization with redaction and size limits.
        if isinstance(obj, dict):
            out = {}
            items = list(obj.items())[:MAX_COLLECTION_SIZE]  # Limit collection size
            for k, v in items:
                if isinstance(k, str) and _redact_key(k):
                    out[k] = "***REDACTED***"
                else:
                    out[k] = safe_serialize(v, _depth + 1, _seen)
            return out

        # Handle iterable serialization with size limits.
        if isinstance(obj, (list, tuple, set)):
            limited_obj = list(obj)[:MAX_COLLECTION_SIZE]  # Limit collection size
            return [safe_serialize(x, _depth + 1, _seen) for x in limited_obj]

        # Handle enum serialization.
        if isinstance(obj, Enum):
            return obj.value

        # Handle string truncation for large payloads.
        if isinstance(obj, str):
            if len(obj) > MAX_STRING_LENGTH:
                return obj[:MAX_STRING_LENGTH] + "... [TRUNCATED]"
            return obj

        # Handle primitive types that orjson can serialize directly.
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj

        # Handle generic object serialization with orjson fallback.
        try:
            # Try orjson serialization first for performance
            orjson.dumps(obj)
            return obj
        except (TypeError, ValueError):
            # Fallback to repr for non-serializable objects
            return _REPR.repr(obj)

    except Exception:
        # Fallback to safe repr on serialization error.
        return _REPR.repr(obj)
    finally:
        _seen.discard(id(obj))
