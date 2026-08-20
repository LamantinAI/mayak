from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class QueryPayload:
    kind: str
    identifier: str | None
    payload: dict[str, object]
