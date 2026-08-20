from __future__ import annotations

from ai_query.common import context_bundle, resolve_workset_subject, workset_payload
from ai_query.models import QueryPayload


def query_workset(subject_kind: str, subject_identifiers: list[str]) -> QueryPayload:
    context_map, change_map, architecture_rules = context_bundle()
    resolved = resolve_workset_subject(subject_kind, subject_identifiers)
    payload = workset_payload(
        context_map=context_map,
        change_map=change_map,
        architecture_rules=architecture_rules,
        subject_kind=subject_kind,
        subject=resolved["subject"],
        changed_files=resolved["changed_files"],
        deleted_files=resolved["deleted_files"],
        status=resolved["status"],
    )
    return QueryPayload(
        kind="workset",
        identifier=f"{subject_kind}:{resolved['subject']}",
        payload=payload,
    )
