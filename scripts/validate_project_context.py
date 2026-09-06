#!/usr/bin/env python3
# FILE: validate_project_context.py
# SUMMARY: Validate docs/project_context.json schema conformance and cross-reference consistency.

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ai_context.rendering import render_json
from ai_context.validator_contract import build_validator_issue_payload

ROOT_DIR = Path(__file__).resolve().parent.parent
PROJECT_CONTEXT_PATH = ROOT_DIR / "docs" / "project_context.json"

VALID_VERTICAL_STATUSES = {
    "active",
    "planned",
    "deprecated",
    "reference_implementation",
}
VALID_INTEGRATION_TYPES = {
    "database",
    "api",
    "message_queue",
    "llm",
    "storage",
    "other",
}

REQUIRED_TOP_LEVEL_KEYS = {
    "schema_version": int,
    "project_name": str,
    "domain": str,
    "verticals": dict,
    "integrations": dict,
    "business_rules": dict,
    "glossary": dict,
    "api_overview": dict,
    "project_decisions": list,
}

# ATTRIBUTE: TEMPLATE_PROJECT_NAME (str)
# SUMMARY: The name this file carries while it still describes the template rather than a project.
# NOTE: Everything else in this validator checks shape — required keys, types, enum values, id
# cross-references — which a file can satisfy while still describing a different project entirely.
# A repository initialized from the template inherited "Mayak" and the template's domain
# paragraph, and no gate said a word; the agent then read that paragraph as the truth about the
# service it was working on. `is_template` is the flip that `make init-project` asks the agent to
# make, and the rule below is what makes forgetting it loud.
#
# `is_template` is deliberately NOT in REQUIRED_TOP_LEVEL_KEYS. Making it required turned every
# existing project on this kernel red the moment it pulled the update, with no migration path and
# no way to tell from the failure that a one-line addition fixes it. Absent means "not decided yet",
# which is exactly the state a project that has not read this rule is in.
TEMPLATE_PROJECT_NAME = "Mayak"

# ATTRIBUTE: TEMPLATE_DOMAIN (str)
# SUMMARY: The `domain` paragraph this file ships with, verbatim.
# NOTE: The domain rule used to ask whether the string CONTAINED "mayak", which answers a
# different question. A service whose domain legitimately names the template it was built on —
# "RAG service on the Mayak kernel" — failed a rule about forgetting to write a domain at all,
# while a domain reworded from the template's without being replaced passed it. Equality against
# the shipped text is the actual subject: has this paragraph been written for this service.
# test_validate_project_context keeps this constant identical to the shipped file.
TEMPLATE_DOMAIN = (
    "Reusable AI-friendly FastAPI backend template. The kernel ships a CompositionRoot, "
    "semantic NDJSON logging, an LLMService with mock-first ADR-003 default, and the Mayak AI "
    "tooling (query CLI, validators, doctor, skills). Verticals (RAG / agents / domain-specific "
    "services) are added on top by extending build_reference_services and wiring typed dependency "
    "aliases."
)


# ATTRIBUTE: _PROJECT_CONTEXT_RULE_PLAYBOOKS (dict[str, dict[str, object]])
# SUMMARY: Stable rule_id → playbook mapping for project_context.json validator failures.
_PROJECT_CONTEXT_RULE_PLAYBOOKS: dict[str, dict[str, object]] = {
    "project_context.invalid_json": {
        "meaning": (
            "docs/project_context.json could not be parsed as JSON. The file may be missing, "
            "empty, or contain a syntax error."
        ),
        "suggested_fix": (
            "Open docs/project_context.json and resolve the parser error reported by the validator. "
            "Run `uv run python scripts/validate_project_context.py --json` to see the precise location."
        ),
        "read_first": [
            "docs/project_context.json",
            "PROJECT.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_project_context.py",
        "likely_fix_shape": (
            "Restore valid JSON syntax: balanced quotes, brackets, commas, and a top-level object."
        ),
        "next_checks": [
            "uv run python scripts/validate_project_context.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop once validate_project_context.py succeeds and the file parses cleanly."
        ),
    },
    "project_context.missing_required_field": {
        "meaning": (
            "A required top-level field in docs/project_context.json is missing or has the wrong type. "
            "Required fields: schema_version, project_name, domain, verticals, integrations, "
            "business_rules, glossary, api_overview, project_decisions."
        ),
        "suggested_fix": (
            "Add the missing field with the expected type, or correct the existing value's type. "
            "Reference PROJECT.md for the canonical structure."
        ),
        "read_first": [
            "PROJECT.md",
            "docs/project_context.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_project_context.py",
        "likely_fix_shape": (
            "Add the named field with a value of the expected type (int, str, dict, or list)."
        ),
        "next_checks": [
            "uv run python scripts/validate_project_context.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop once all required top-level keys exist with the expected types."
        ),
    },
    "project_context.template_identity_not_replaced": {
        "meaning": (
            "docs/project_context.json says is_template is false, but project_name or domain still "
            "carries the Mayak template's own identity. Every agent that reads this file "
            "will describe the wrong project."
        ),
        "suggested_fix": (
            "Set project_name to this service's real name and rewrite domain to say what this "
            "service does. If this repository IS the template, set is_template back to true."
        ),
        "read_first": [
            "docs/project_context.json",
            "PROJECT.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_project_context.py",
        "likely_fix_shape": (
            "Replace two string values: project_name and domain. No structural change is needed."
        ),
        "next_checks": [
            "uv run python scripts/validate_project_context.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop once project_name and domain describe this project rather than the template."
        ),
    },
    "project_context.invalid_vertical_status": {
        "meaning": (
            "A vertical entry has a status not in the allowed set "
            "(active, planned, deprecated, reference_implementation)."
        ),
        "suggested_fix": ("Replace the invalid status with one of the four allowed values."),
        "read_first": [
            "docs/project_context.json",
            "PROJECT.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_project_context.py",
        "likely_fix_shape": (
            "Set the vertical's status field to one of: active, planned, deprecated, reference_implementation."
        ),
        "next_checks": [
            "uv run python scripts/validate_project_context.py",
        ],
        "stop_widening_condition": ("Stop once every vertical status is in the allowed set."),
    },
    "project_context.invalid_integration_type": {
        "meaning": (
            "An integration entry has a type not in the allowed set "
            "(database, api, message_queue, llm, storage, other)."
        ),
        "suggested_fix": (
            "Replace the invalid integration type with one of the six allowed categories."
        ),
        "read_first": [
            "docs/project_context.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_project_context.py",
        "likely_fix_shape": (
            "Set the integration's type field to one of the six allowed categories."
        ),
        "next_checks": [
            "uv run python scripts/validate_project_context.py",
        ],
        "stop_widening_condition": ("Stop once every integration type is in the allowed set."),
    },
    "project_context.missing_field": {
        "meaning": (
            "A vertical, integration, or business_rule entry is missing a required sub-field "
            "(e.g., description, status, type, summary, vertical)."
        ),
        "suggested_fix": (
            "Add the missing sub-field as documented in PROJECT.md and the existing entries."
        ),
        "read_first": [
            "docs/project_context.json",
            "PROJECT.md",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_project_context.py",
        "likely_fix_shape": (
            "Insert the named field with a string or list value matching the section's schema."
        ),
        "next_checks": [
            "uv run python scripts/validate_project_context.py",
        ],
        "stop_widening_condition": ("Stop once every entry has all required sub-fields."),
    },
    "project_context.cross_reference_unknown": {
        "meaning": (
            "A business_rule references a vertical name that does not exist in the verticals "
            "section, or a vertical references a business_rule id that does not exist."
        ),
        "suggested_fix": (
            "Either fix the reference to point at an existing entry, or add the missing entry."
        ),
        "read_first": [
            "docs/project_context.json",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_project_context.py",
        "likely_fix_shape": (
            "Correct the cross-reference to a known vertical / business_rule key, or define the missing key."
        ),
        "next_checks": [
            "uv run python scripts/validate_project_context.py",
        ],
        "stop_widening_condition": (
            "Stop once every cross-reference resolves to an existing entry."
        ),
    },
    "project_context.vertical_status_contradicts_wiring": {
        "meaning": (
            "A vertical's declared status disagrees with the wiring: it is described as running "
            "while nothing registers it, or as planned while its service or router is already "
            "wired in."
        ),
        "suggested_fix": (
            "Change the status to the one the code implements, or finish/remove the wiring. The "
            "code is the authority here; the status is a description of it."
        ),
        "read_first": [
            "docs/project_context.json",
            "project/core/service_registration.py",
            "project/infrastructure/api/router_registration.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_project_context.py",
        "likely_fix_shape": (
            "One word in docs/project_context.json, or the registration line the status promised."
        ),
        "next_checks": ["make quality-gates"],
        "stop_widening_condition": (
            "The declared status and the registration agree for every vertical."
        ),
    },
}


@dataclass(slots=True)
class ProjectContextIssue:
    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Stable rule identifier consumable by query_ai_context.py failure rule.
    rule_id: str

    # ATTRIBUTE: field (str)
    # SUMMARY: JSON path of the field that failed validation.
    field: str

    # ATTRIBUTE: message (str)
    # SUMMARY: Human-readable description of the issue.
    message: str

    # ATTRIBUTE: severity (str)
    # SUMMARY: 'error' (blocks gate), 'warning' (visible but non-blocking), or 'info' (purely informational).
    severity: str = "error"


# FUNCTION: get_project_context_rule_playbook
# SUMMARY: Return a copy of the playbook for a project_context rule_id, or None if unknown.
# OUTPUT: (dict[str, object] | None): Playbook copy, or None if the rule_id is not registered.
def get_project_context_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _PROJECT_CONTEXT_RULE_PLAYBOOKS.get(rule_id)
    if playbook is None:
        return None
    return dict(playbook)


# FUNCTION: _validate_top_level
# SUMMARY: Check that all required top-level keys exist with correct types.
# INPUT: data (dict): Parsed project_context.json content.
def _validate_top_level(data: dict[str, object]) -> list[ProjectContextIssue]:
    issues: list[ProjectContextIssue] = []
    for key, expected_type in REQUIRED_TOP_LEVEL_KEYS.items():
        if key not in data:
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_required_field",
                    field=key,
                    message=f"Missing required field '{key}'.",
                )
            )
        elif not isinstance(data[key], expected_type):
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_required_field",
                    field=key,
                    message=f"Field '{key}' must be {expected_type.__name__}, got {type(data[key]).__name__}.",
                )
            )
    return issues


# FUNCTION: _validate_identity
# SUMMARY: Once the file claims to describe a real project, refuse the template's leftover identity.
# INPUT: data (dict): Parsed project_context.json content.
def _validate_identity(data: dict[str, object]) -> list[ProjectContextIssue]:
    if data.get("is_template") is not False:
        return []

    issues: list[ProjectContextIssue] = []
    if data.get("project_name") == TEMPLATE_PROJECT_NAME:
        issues.append(
            ProjectContextIssue(
                rule_id="project_context.template_identity_not_replaced",
                field="project_name",
                message=(
                    f"project_name is still '{TEMPLATE_PROJECT_NAME}' while is_template is false. "
                    "Set it to this project's real name."
                ),
            )
        )

    domain = data.get("domain")
    if isinstance(domain, str) and " ".join(domain.split()) == TEMPLATE_DOMAIN:
        issues.append(
            ProjectContextIssue(
                rule_id="project_context.template_identity_not_replaced",
                field="domain",
                message=(
                    "domain still describes the template while is_template is false. "
                    "Replace it with what this service actually does."
                ),
            )
        )
    return issues


# FUNCTION: _validate_verticals
# SUMMARY: Validate each vertical entry has required fields and valid status.
# INPUT: verticals (dict): The verticals section of project_context.json.
def _validate_verticals(verticals: dict[str, object]) -> list[ProjectContextIssue]:
    issues: list[ProjectContextIssue] = []
    for name, entry in verticals.items():
        prefix = f"verticals.{name}"
        if not isinstance(entry, dict):
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_field",
                    field=prefix,
                    message="Vertical entry must be an object.",
                )
            )
            continue
        if "description" not in entry:
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_field",
                    field=f"{prefix}.description",
                    message="Missing required field 'description'.",
                )
            )
        if "status" not in entry:
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_field",
                    field=f"{prefix}.status",
                    message="Missing required field 'status'.",
                )
            )
        elif entry["status"] not in VALID_VERTICAL_STATUSES:
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.invalid_vertical_status",
                    field=f"{prefix}.status",
                    message=f"Invalid status '{entry['status']}'. Must be one of: {', '.join(sorted(VALID_VERTICAL_STATUSES))}.",
                )
            )
        if "domain_entities" in entry and not isinstance(entry["domain_entities"], list):
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_field",
                    field=f"{prefix}.domain_entities",
                    message="Field 'domain_entities' must be a list.",
                )
            )
        if "business_rules" in entry and not isinstance(entry["business_rules"], list):
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_field",
                    field=f"{prefix}.business_rules",
                    message="Field 'business_rules' must be a list.",
                )
            )
    return issues


# FUNCTION: _validate_integrations
# SUMMARY: Validate each integration entry has required fields and valid type.
# INPUT: integrations (dict): The integrations section of project_context.json.
def _validate_integrations(
    integrations: dict[str, object],
) -> list[ProjectContextIssue]:
    issues: list[ProjectContextIssue] = []
    for name, entry in integrations.items():
        prefix = f"integrations.{name}"
        if not isinstance(entry, dict):
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_field",
                    field=prefix,
                    message="Integration entry must be an object.",
                )
            )
            continue
        if "type" not in entry:
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_field",
                    field=f"{prefix}.type",
                    message="Missing required field 'type'.",
                )
            )
        elif entry["type"] not in VALID_INTEGRATION_TYPES:
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.invalid_integration_type",
                    field=f"{prefix}.type",
                    message=f"Invalid type '{entry['type']}'. Must be one of: {', '.join(sorted(VALID_INTEGRATION_TYPES))}.",
                )
            )
        if "description" not in entry:
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_field",
                    field=f"{prefix}.description",
                    message="Missing required field 'description'.",
                )
            )
    return issues


# FUNCTION: _validate_business_rules
# SUMMARY: Validate each business rule entry and check vertical cross-references.
# INPUT: vertical_names (set[str]): Known vertical names for cross-reference checking.
def _validate_business_rules(
    business_rules: dict[str, object],
    vertical_names: set[str],
) -> list[ProjectContextIssue]:
    issues: list[ProjectContextIssue] = []
    for rule_id, entry in business_rules.items():
        prefix = f"business_rules.{rule_id}"
        if not isinstance(entry, dict):
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_field",
                    field=prefix,
                    message="Business rule entry must be an object.",
                )
            )
            continue
        if "summary" not in entry:
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_field",
                    field=f"{prefix}.summary",
                    message="Missing required field 'summary'.",
                )
            )
        if "vertical" not in entry:
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.missing_field",
                    field=f"{prefix}.vertical",
                    message="Missing required field 'vertical'.",
                )
            )
        elif entry["vertical"] != "cross-cutting" and entry["vertical"] not in vertical_names:
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.cross_reference_unknown",
                    field=f"{prefix}.vertical",
                    message=f"Vertical '{entry['vertical']}' not found in verticals section.",
                )
            )
    return issues


# ATTRIBUTE: _RUNNING_STATUSES (frozenset[str])
# SUMMARY: Statuses that assert the vertical is wired into the running application.
_RUNNING_STATUSES = frozenset({"active", "reference_implementation"})


# FUNCTION: _parsed
# SUMMARY: The syntax tree of a wiring file, or None when it cannot be read.
def _parsed(path: Path) -> ast.Module | None:
    if not path.is_file():
        return None
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


# FUNCTION: _wiring_registers_nothing
# SUMMARY: Report whether the wiring files plainly register nothing at all.
# OUTPUT: (bool): True when no function hands a value back and no router is included.
# NOTE: This is the discriminator between the two ways a project can yield no vertical names, and
# it is written as the narrow question rather than the broad one on purpose. The extractors read
# one file's syntax tree and follow no imports, so a registry assembled anywhere else — a helper
# module, a merged mapping, a module-level constant, a comprehension — is invisible to them, and
# an earlier attempt to enumerate the readable shapes kept finding another one it had missed and
# reporting a correctly wired project. Anything that hands a value back, or includes a router at
# all, is therefore treated as wiring this validator cannot vouch for, and the check stands down.
#
# What is left is the shape that registers nothing under any reading: functions that return
# nothing and no router inclusion. That is the ordinary mistake the rule exists for — replacing
# the example vertical and forgetting to wire the replacement.
def _wiring_registers_nothing(registration: Path, routers: Path) -> bool:
    for path in (registration, routers):
        tree = _parsed(path)
        if path.is_file() and tree is None:
            return False
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and node.value is not None:
                return False
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router"
            ):
                return False
    return True


# FUNCTION: _wired_vertical_names
# SUMMARY: The vertical names the wiring files actually register, or None when they are absent.
# OUTPUT: (tuple[set[str], dict[str, str]] | None): Names registered outright, and the singular
# of each plural endpoint module mapped back to that module.
# NOTE: Two sources, because a project can wire a vertical either way. The service key is what
# router_registration.py keys the conditional include on, and the endpoint module is what an
# unconditional include names. The endpoint module is conventionally plural, so its singular is
# accepted too. Returns None rather than an empty set when the files are missing: a checkout
# without them says nothing about the statuses, and treating silence as "nothing is wired" would
# fail every project that keeps its wiring elsewhere.
def _wired_vertical_names(root_dir: Path) -> tuple[set[str], dict[str, str]] | None:
    from ai_context.extraction import extract_router_modules, extract_service_registry_entries

    registration = root_dir / "project" / "core" / "service_registration.py"
    routers = root_dir / "project" / "infrastructure" / "api" / "router_registration.py"
    if not registration.is_file() and not routers.is_file():
        return None

    names: set[str] = set()
    singulars: dict[str, str] = {}
    # **LOGIC_STEP**: A wiring file that does not parse is somebody else's problem — ruff and the
    # test run both report it, loudly and first. Here it raised out of a validator that is about
    # a JSON document, which named the wrong thing and stopped the rest of this file's checks.
    try:
        if registration.is_file():
            for key in extract_service_registry_entries(registration, root_dir, "vertical"):
                names.add(key.removesuffix("_service").removesuffix("_repository"))
        if routers.is_file():
            for module in extract_router_modules(routers):
                names.add(module)
                if module.endswith("s"):
                    singulars.setdefault(module[:-1], module)
    except Exception:
        return None
    # **LOGIC_STEP**: Nothing read is not the same as nothing registered — but neither is it the
    # same as nothing to read. Wiring this validator cannot follow is silence; wiring that plainly
    # registers nothing is the mistake the rule exists for. _wiring_registers_nothing draws that
    # line, and errs towards silence.
    if not names and not _wiring_registers_nothing(registration, routers):
        return None
    return names, singulars


# FUNCTION: _validate_vertical_wiring
# SUMMARY: Check each declared status against what the wiring files register.
def _validate_vertical_wiring(
    verticals: dict[str, object],
    wired: set[str],
    singulars: dict[str, str],
) -> list[ProjectContextIssue]:
    issues: list[ProjectContextIssue] = []
    for name, entry in verticals.items():
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        # **LOGIC_STEP**: The singular of a plural endpoint module counts as that vertical only
        # while the plural is not itself a declared vertical. A project with both `order` and
        # `orders` has two verticals, and reading the router for `orders` as evidence that `order`
        # is wired reported the planned one as already registered — a red gate on correct work.
        plural = singulars.get(name)
        registered = name in wired or (plural is not None and plural not in verticals)
        if status in _RUNNING_STATUSES and not registered:
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.vertical_status_contradicts_wiring",
                    field=f"verticals.{name}.status",
                    message=(
                        f"Status '{status}' says this vertical is running, but neither "
                        "service_registration.py nor router_registration.py registers it."
                    ),
                )
            )
        elif status == "planned" and registered:
            issues.append(
                ProjectContextIssue(
                    rule_id="project_context.vertical_status_contradicts_wiring",
                    field=f"verticals.{name}.status",
                    message=(
                        "Status 'planned' says this vertical does not exist yet, but it is "
                        "already registered in the wiring."
                    ),
                )
            )
    return issues


# FUNCTION: _validate_cross_references
# SUMMARY: Check that BR-ids referenced in verticals exist in business_rules.
def _validate_cross_references(
    verticals: dict[str, object],
    business_rule_ids: set[str],
) -> list[ProjectContextIssue]:
    issues: list[ProjectContextIssue] = []
    for name, entry in verticals.items():
        if not isinstance(entry, dict):
            continue
        br_refs = entry.get("business_rules", [])
        if not isinstance(br_refs, list):
            continue
        for br_id in br_refs:
            if br_id not in business_rule_ids:
                issues.append(
                    ProjectContextIssue(
                        rule_id="project_context.cross_reference_unknown",
                        field=f"verticals.{name}.business_rules",
                        message=f"Referenced business rule '{br_id}' not found in business_rules section.",
                    )
                )
    return issues


# FUNCTION: collect_project_context_issues
# SUMMARY: Load and validate docs/project_context.json, returning all found issues.
def collect_project_context_issues(root_dir: Path) -> list[ProjectContextIssue]:
    context_path = root_dir / "docs" / "project_context.json"

    if not context_path.exists():
        return [
            ProjectContextIssue(
                rule_id="project_context.invalid_json",
                field="file",
                message="docs/project_context.json does not exist. Run: uv run python init_project_context.py",
            )
        ]

    try:
        data = json.loads(context_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [
            ProjectContextIssue(
                rule_id="project_context.invalid_json",
                field="file",
                message=f"Invalid JSON: {error}",
            )
        ]

    if not isinstance(data, dict):
        return [
            ProjectContextIssue(
                rule_id="project_context.invalid_json",
                field="file",
                message="Root must be a JSON object.",
            )
        ]

    issues: list[ProjectContextIssue] = []

    # **LOGIC_STEP**: Validate top-level structure first; bail early on critical failures.
    issues.extend(_validate_top_level(data))
    if any(issue.field in REQUIRED_TOP_LEVEL_KEYS for issue in issues):
        return issues

    issues.extend(_validate_identity(data))

    verticals = data.get("verticals", {})
    integrations = data.get("integrations", {})
    business_rules = data.get("business_rules", {})

    if isinstance(verticals, dict):
        issues.extend(_validate_verticals(verticals))

    if isinstance(integrations, dict):
        issues.extend(_validate_integrations(integrations))

    vertical_names = set(verticals.keys()) if isinstance(verticals, dict) else set()
    business_rule_ids = set(business_rules.keys()) if isinstance(business_rules, dict) else set()

    if isinstance(business_rules, dict):
        issues.extend(_validate_business_rules(business_rules, vertical_names))

    if isinstance(verticals, dict):
        issues.extend(_validate_cross_references(verticals, business_rule_ids))
        registrations = _wired_vertical_names(root_dir)
        if registrations is not None:
            issues.extend(_validate_vertical_wiring(verticals, *registrations))

    return issues


# FUNCTION: _issue_to_payload
# SUMMARY: Convert an issue to a JSON-serializable payload with playbook hints.
def _issue_to_payload(issue: ProjectContextIssue) -> dict[str, object]:
    # **LOGIC_STEP**: ProjectContextIssue has no native file/line — field is the closest
    # identifying location (a JSON path within docs/project_context.json). Map it into the
    # canon's `file` field with a neutral `line=1` while keeping field/severity (asdict) unchanged.
    playbook = get_project_context_rule_playbook(issue.rule_id)
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category="project_context",
        file=issue.field,
        line=1,
        message=issue.message,
        severity=issue.severity,
        playbook=playbook,
        extra=asdict(issue),
    )


# FUNCTION: main
# SUMMARY: Run project_context validation and return process exit code.
# OUTPUT: (int): Zero on success, non-zero when issues are found.
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate docs/project_context.json structure and references."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable validation results.",
    )
    args = parser.parse_args()

    issues = collect_project_context_issues(ROOT_DIR)
    if not issues:
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            print("Project context validation passed.")
        return 0

    if args.json:
        print(
            render_json(
                {
                    "status": "error",
                    "issues": [_issue_to_payload(issue) for issue in issues],
                }
            ),
            end="",
        )
        return 1

    for issue in issues:
        print(f"[{issue.field}] [{issue.rule_id}] {issue.message}")
    print(f"\n{len(issues)} issue(s) found.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
