from __future__ import annotations

import argparse

from ai_query.handlers.bootstrap import query_bootstrap
from ai_query.handlers.before_edit import query_before_edit
from ai_query.handlers.failure import query_failure
from ai_query.handlers.overview import query_overview
from ai_query.handlers.symbol import query_symbol
from ai_query.handlers.workset import query_workset
from ai_query.models import QueryPayload


def resolve_query(args: argparse.Namespace) -> QueryPayload:
    if args.command == "bootstrap":
        return query_bootstrap()
    if args.command == "overview":
        return query_overview()
    if args.command == "before-edit":
        return query_before_edit(args.repo_path)
    if args.command == "workset":
        return query_workset(
            args.subject_kind,
            getattr(args, "subject_identifiers", []),
        )
    if args.command == "failure":
        return query_failure(args.rule_id)
    if args.command == "symbol":
        return query_symbol(args.name)
    raise ValueError(f"Unsupported command: {args.command}")
