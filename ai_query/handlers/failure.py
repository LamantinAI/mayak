from __future__ import annotations

from ai_query.common import failure_playbook
from ai_query.models import QueryPayload


def query_failure(rule_id: str) -> QueryPayload:
    return QueryPayload(
        kind="failure",
        identifier=rule_id,
        payload=failure_playbook(rule_id),
    )
