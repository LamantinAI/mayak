from __future__ import annotations

from ai_query.common import find_symbol
from ai_query.models import QueryPayload


def query_symbol(name: str) -> QueryPayload:
    return QueryPayload(
        kind="symbol",
        identifier=name,
        payload=find_symbol(name),
    )
