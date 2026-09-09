#!/usr/bin/env python3
# FILE: validate_repository_metadata.py
# SUMMARY: Three repository-metadata validators merged into one module: skills/commands frontmatter,
# path-shaped literals inside scripts/*.py, and docs/project_context.json schema + cross-references.
# NOTE: By design, this module does not flag project_context.vertical_status_contradicts_wiring —
# the declared status in project_context.json is a description, and
# scripts/validate_endpoint_wiring.py is what actually checks the wiring.

from __future__ import annotations

import argparse
import ast
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from ai_context.rendering import render_json
from ai_context.validator_contract import build_validator_issue_payload

ROOT_DIR = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------------------------------------------
# Shared: one rule-playbook table and one getter, replacing the three per-validator ones.
# ------------------------------------------------------------------------------------------------

_RERUN = "uv run python scripts/validate_repository_metadata.py"

# ATTRIBUTE: _REPOSITORY_METADATA_RULE_PLAYBOOKS (dict[str, dict[str, object]])
# SUMMARY: Every rule_id this module can emit, across all three former validators.
_REPOSITORY_METADATA_RULE_PLAYBOOKS: dict[str, dict[str, object]] = {
    "skills_frontmatter.missing_frontmatter": {
        "meaning": "A skill's SKILL.md does not open with a YAML frontmatter block (--- ... ---).",
        "suggested_fix": "Add a frontmatter block with at least name, description, and triggers.",
        "read_first": [".agents/skills/"],
        "smallest_command_to_rerun": _RERUN,
        "likely_fix_shape": "Prepend a `---\\n<fields>\\n---` block before the skill body.",
        "next_checks": [_RERUN],
        "stop_widening_condition": "Stop once every SKILL.md has a parseable frontmatter block.",
    },
    "skills_frontmatter.missing_field": {
        "meaning": "A required frontmatter field (name, description, or triggers) is missing or empty.",
        "suggested_fix": "Add the missing field. triggers must be a non-empty list.",
        "read_first": [".agents/skills/"],
        "smallest_command_to_rerun": _RERUN,
        "likely_fix_shape": "Insert the named field with a non-empty value or list.",
        "next_checks": [_RERUN],
        "stop_widening_condition": "Stop once every required field is present and non-empty.",
    },
    "skills_frontmatter.invalid_value": {
        "meaning": "A frontmatter value has the wrong shape — e.g. validation_command must start "
        "with `make ` or `uv run`.",
        "suggested_fix": "Replace the value with one matching the documented format.",
        "read_first": [".agents/skills/"],
        "smallest_command_to_rerun": _RERUN,
        "likely_fix_shape": "Set validation_command to a value starting with `make ` or `uv run`.",
        "next_checks": [_RERUN],
        "stop_widening_condition": "Stop once every value matches the expected format.",
    },
    "skills_frontmatter.broken_path": {
        "meaning": "A minimal_read_set path does not exist on disk relative to the repo root.",
        "suggested_fix": "Fix the path, or remove the entry from minimal_read_set.",
        "read_first": [".agents/skills/"],
        "smallest_command_to_rerun": _RERUN,
        "likely_fix_shape": "Point the entry at a real repo-relative path, or delete it.",
        "next_checks": [_RERUN],
        "stop_widening_condition": "Stop once every minimal_read_set entry resolves to a file.",
    },
    "script_paths.broken_reference": {
        "meaning": "A string literal inside scripts/*.py references a path that does not exist on "
        "disk — usually a rename or delete that left a tooling literal behind.",
        "suggested_fix": "Fix the literal to the new path, remove it if the path was deleted on "
        "purpose, or allowlist it in this module if it is not really a path.",
        "read_first": ["scripts/"],
        "smallest_command_to_rerun": _RERUN,
        "likely_fix_shape": "Rename the literal to its new on-disk location, or delete the reference.",
        "next_checks": [_RERUN, "make quality-gates"],
        "stop_widening_condition": "Stop once every path-shaped literal in scripts/*.py resolves.",
    },
    "project_context.invalid_json": {
        "meaning": "docs/project_context.json could not be parsed — missing, empty, or malformed.",
        "suggested_fix": "Fix the JSON syntax error the validator reports.",
        "read_first": ["docs/project_context.json", "PROJECT.md"],
        "smallest_command_to_rerun": _RERUN,
        "likely_fix_shape": "Restore valid JSON: balanced quotes, brackets, commas, a top-level object.",
        "next_checks": [_RERUN, "make quality-gates"],
        "stop_widening_condition": "Stop once the file parses cleanly.",
    },
    "project_context.missing_required_field": {
        "meaning": "A required top-level field is missing or has the wrong type. Required: "
        "schema_version, project_name, domain, verticals, integrations, business_rules, glossary, "
        "api_overview, project_decisions.",
        "suggested_fix": "Add the field with the expected type; see PROJECT.md for the shape.",
        "read_first": ["PROJECT.md", "docs/project_context.json"],
        "smallest_command_to_rerun": _RERUN,
        "likely_fix_shape": "Add the named field with a value of the expected type.",
        "next_checks": [_RERUN, "make quality-gates"],
        "stop_widening_condition": "Stop once every required top-level key has the expected type.",
    },
    "project_context.template_identity_not_replaced": {
        "meaning": "is_template is false, but project_name or domain still carries the Mayak "
        "template's own identity — every agent reading this file will describe the wrong project.",
        "suggested_fix": "Set project_name and domain to this service's own. If this IS the "
        "template, set is_template back to true.",
        "read_first": ["docs/project_context.json", "PROJECT.md"],
        "smallest_command_to_rerun": _RERUN,
        "likely_fix_shape": "Replace project_name and domain. No structural change is needed.",
        "next_checks": [_RERUN, "make quality-gates"],
        "stop_widening_condition": "Stop once project_name and domain describe this project.",
    },
    "project_context.invalid_vertical_status": {
        "meaning": "A vertical's status is not one of active, planned, deprecated, "
        "reference_implementation.",
        "suggested_fix": "Replace it with one of the four allowed values.",
        "read_first": ["docs/project_context.json", "PROJECT.md"],
        "smallest_command_to_rerun": _RERUN,
        "likely_fix_shape": "Set the vertical's status field to an allowed value.",
        "next_checks": [_RERUN],
        "stop_widening_condition": "Stop once every vertical status is in the allowed set.",
    },
    "project_context.invalid_integration_type": {
        "meaning": "An integration's type is not one of database, api, message_queue, llm, "
        "storage, other.",
        "suggested_fix": "Replace it with one of the six allowed categories.",
        "read_first": ["docs/project_context.json"],
        "smallest_command_to_rerun": _RERUN,
        "likely_fix_shape": "Set the integration's type field to an allowed category.",
        "next_checks": [_RERUN],
        "stop_widening_condition": "Stop once every integration type is in the allowed set.",
    },
    "project_context.missing_field": {
        "meaning": "A vertical, integration, or business_rule entry is missing a required "
        "sub-field (description, status, type, summary, vertical, ...).",
        "suggested_fix": "Add the missing sub-field as PROJECT.md and the other entries show.",
        "read_first": ["docs/project_context.json", "PROJECT.md"],
        "smallest_command_to_rerun": _RERUN,
        "likely_fix_shape": "Insert the named field with the value the section's shape expects.",
        "next_checks": [_RERUN],
        "stop_widening_condition": "Stop once every entry has all required sub-fields.",
    },
    "project_context.cross_reference_unknown": {
        "meaning": "A business_rule names a vertical that does not exist, or a vertical names a "
        "business_rule id that does not exist.",
        "suggested_fix": "Fix the reference to an existing entry, or add the missing entry.",
        "read_first": ["docs/project_context.json"],
        "smallest_command_to_rerun": _RERUN,
        "likely_fix_shape": "Correct the cross-reference, or define the missing key.",
        "next_checks": [_RERUN],
        "stop_widening_condition": "Stop once every cross-reference resolves to an existing entry.",
    },
}


# FUNCTION: get_repository_metadata_rule_playbook
# SUMMARY: Return a copy of the playbook for any rule_id this module emits, or None if unknown.
# Replaces get_skills_frontmatter_rule_playbook, get_script_paths_rule_playbook and
# get_project_context_rule_playbook — one getter, registered once in ai_query/common.py.
def get_repository_metadata_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _REPOSITORY_METADATA_RULE_PLAYBOOKS.get(rule_id)
    return None if playbook is None else dict(playbook)


# ------------------------------------------------------------------------------------------------
# Skills / commands frontmatter (formerly validate_skills_frontmatter.py)
# ------------------------------------------------------------------------------------------------

SKILL_DIRS = [ROOT_DIR / ".agents" / "skills"]
# .claude/commands/ are generated from .agents/skills/ and have their frontmatter stripped —
# only the source skills are validated.
COMMAND_DIRS: list[Path] = []
VALID_COMMAND_PREFIXES = ("make ", "uv run")


# CLASS: validate_repository_metadata.SkillFrontmatterIssue
# SUMMARY: One frontmatter problem in a SKILL.md (or, if COMMAND_DIRS is ever populated, a command).
@dataclass(slots=True)
class SkillFrontmatterIssue:
    rule_id: str
    path: Path
    field: str
    message: str
    severity: str = "error"


def _sk(rule_id: str, path: Path, field: str, message: str) -> SkillFrontmatterIssue:
    return SkillFrontmatterIssue(rule_id=rule_id, path=path, field=field, message=message)


def _parse_frontmatter(text: str) -> dict[str, object] | None:
    """Extract YAML frontmatter from markdown text. Returns None if no frontmatter found."""
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    raw = text[3:end].strip()
    result: dict[str, object] = {}
    current_key: str | None = None
    current_list: list[str] = []
    for line in raw.splitlines():
        inline_match = re.match(r"^(\w[\w_]*)\s*:\s*\[(.+)\]\s*$", line)
        if inline_match:
            if current_key and current_list:
                result[current_key] = current_list
                current_list = []
            result[inline_match.group(1)] = [
                v.strip().strip("'\"") for v in inline_match.group(2).split(",")
            ]
            current_key = None
            continue
        scalar_match = re.match(r"^(\w[\w_]*)\s*:\s*(.+)$", line)
        if scalar_match and not line.strip().endswith(":"):
            if current_key and current_list:
                result[current_key] = current_list
                current_list = []
            result[scalar_match.group(1)] = scalar_match.group(2).strip()
            current_key = None
            continue
        list_header = re.match(r"^(\w[\w_]*)\s*:\s*$", line)
        if list_header:
            if current_key and current_list:
                result[current_key] = current_list
            current_key = list_header.group(1)
            current_list = []
            continue
        item_match = re.match(r"^\s+-\s+(.+)$", line)
        if item_match and current_key:
            current_list.append(item_match.group(1).strip())
    if current_key and current_list:
        result[current_key] = current_list
    return result


def _validate_file(path: Path, is_skill: bool) -> list[SkillFrontmatterIssue]:
    """Validate a single skill or command file."""
    text = path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    if fm is None:
        return [
            _sk(
                "skills_frontmatter.missing_frontmatter",
                path,
                "frontmatter",
                "Missing YAML frontmatter.",
            )
        ]

    issues: list[SkillFrontmatterIssue] = []
    if is_skill:
        for field in ("name", "description"):
            if field not in fm:
                issues.append(
                    _sk(
                        "skills_frontmatter.missing_field",
                        path,
                        field,
                        f"Missing required field '{field}'.",
                    )
                )

    triggers = fm.get("triggers")
    if not isinstance(triggers, list) or len(triggers) == 0:
        issues.append(
            _sk(
                "skills_frontmatter.missing_field",
                path,
                "triggers",
                "Field 'triggers' must be a non-empty list.",
            )
        )

    minimal_read_set = fm.get("minimal_read_set")
    if isinstance(minimal_read_set, list):
        for entry in minimal_read_set:
            if not (ROOT_DIR / entry).exists():
                issues.append(
                    _sk(
                        "skills_frontmatter.broken_path",
                        path,
                        "minimal_read_set",
                        f"Path does not exist: {entry}",
                    )
                )

    validation_command = fm.get("validation_command")
    if isinstance(validation_command, str) and not any(
        validation_command.startswith(prefix) for prefix in VALID_COMMAND_PREFIXES
    ):
        issues.append(
            _sk(
                "skills_frontmatter.invalid_value",
                path,
                "validation_command",
                f"validation_command must start with one of: {', '.join(VALID_COMMAND_PREFIXES)}",
            )
        )

    return issues


def collect_skills_frontmatter_issues(root_dir: Path) -> list[SkillFrontmatterIssue]:
    """Scan .agents/skills/ (and .claude/commands/, currently empty) for frontmatter issues."""
    del root_dir  # SKILL_DIRS / COMMAND_DIRS are absolute via module-level ROOT_DIR.
    issues: list[SkillFrontmatterIssue] = []
    for skill_dir in SKILL_DIRS:
        if skill_dir.exists():
            for skill_file in sorted(skill_dir.rglob("SKILL.md")):
                issues.extend(_validate_file(skill_file, is_skill=True))
    for cmd_dir in COMMAND_DIRS:
        if cmd_dir.exists():
            for cmd_file in sorted(cmd_dir.glob("*.md")):
                issues.extend(_validate_file(cmd_file, is_skill=False))
    return issues


def _skills_frontmatter_issue_to_payload(issue: SkillFrontmatterIssue) -> dict[str, object]:
    relative_path = (
        str(issue.path.relative_to(ROOT_DIR)) if issue.path.is_absolute() else str(issue.path)
    )
    extra: dict[str, object] = asdict(issue)
    extra["path"] = relative_path
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category="skills_frontmatter",
        file=relative_path,
        line=1,
        message=issue.message,
        severity=issue.severity,
        playbook=get_repository_metadata_rule_playbook(issue.rule_id),
        extra=extra,
    )


# ------------------------------------------------------------------------------------------------
# Path-shaped literals inside scripts/*.py (formerly validate_script_paths.py)
# ------------------------------------------------------------------------------------------------

_SCAN_PREFIXES: tuple[str, ...] = (
    "tests/",
    "project/",
    "docs/",
    "alembic/",
    ".agents/skills/",
    "ai_context/",
    "ai_query/",
    ".github/",
    ".githooks/",
)

_PATH_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:tests|project|docs|alembic|skills|ai_context|ai_query|\.github|\.githooks|\.agents/skills)/[\w./\-]+$"
)

# ATTRIBUTE: _ALLOWLIST (frozenset[str])
# SUMMARY: Path-shaped literals that intentionally do not need to exist on disk. Add sparingly.
_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Path prefix used by validate_runtime_ownership._ENV_ALLOWLIST_PREFIXES — not a real
        # file. Covers both project/core/config.py and a future project/core/config/ split.
        "project/core/config",
    }
)


# CLASS: validate_repository_metadata.ScriptPathIssue
# SUMMARY: One path-shaped literal in scripts/*.py that does not resolve on disk.
@dataclass(slots=True)
class ScriptPathIssue:
    rule_id: str
    source_file: str
    line: int
    literal: str
    message: str
    severity: str = "error"


def _is_path_shaped(literal: str) -> bool:
    if not literal or literal in _ALLOWLIST or not literal.startswith(_SCAN_PREFIXES):
        return False
    return bool(_PATH_PATTERN.match(literal))


def _extract_string_literals(source_path: Path) -> list[tuple[str, int]]:
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    except SyntaxError:
        return []
    return [
        (node.value, getattr(node, "lineno", 1))
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def collect_script_path_issues(
    root_dir: Path,
    scripts_dir: Path | None = None,
) -> list[ScriptPathIssue]:
    """Walk scripts/*.py and return issues for path-shaped literals that do not resolve on disk."""
    base = scripts_dir if scripts_dir is not None else (root_dir / "scripts")
    if not base.exists():
        return []
    issues: list[ScriptPathIssue] = []
    for source_path in sorted(base.rglob("*.py")):
        # **LOGIC_STEP**: This file is scanned like any other. Merging three validators into one
        # turned a narrow exemption — the script-path checker skipping its own regex and allowlist
        # literals — into a blanket skip of every literal in all three, so a typo in a playbook's
        # `read_first` path stopped being reported. Nothing here needs the exemption: a bare prefix
        # like `tests/` fails _PATH_PATTERN for want of anything after the slash, and the one
        # genuine non-file, `project/core/config`, is in _ALLOWLIST.
        for literal, line in _extract_string_literals(source_path):
            if not _is_path_shaped(literal) or (root_dir / literal).exists():
                continue
            relative = source_path.relative_to(root_dir).as_posix()
            issues.append(
                ScriptPathIssue(
                    rule_id="script_paths.broken_reference",
                    source_file=relative,
                    line=line,
                    literal=literal,
                    message=(
                        f"{relative}:{line} references missing path '{literal}'. "
                        "Either fix the path, remove the reference, or add it to the allowlist."
                    ),
                )
            )
    return issues


def _script_paths_issue_to_payload(issue: ScriptPathIssue) -> dict[str, object]:
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category="script_paths",
        file=issue.source_file,
        line=issue.line,
        message=issue.message,
        severity=issue.severity,
        playbook=get_repository_metadata_rule_playbook(issue.rule_id),
        extra=asdict(issue),
    )


# ------------------------------------------------------------------------------------------------
# docs/project_context.json schema + cross-references (formerly validate_project_context.py)
# ------------------------------------------------------------------------------------------------

VALID_VERTICAL_STATUSES = {"active", "planned", "deprecated", "reference_implementation"}
VALID_INTEGRATION_TYPES = {"database", "api", "message_queue", "llm", "storage", "other"}

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

# ATTRIBUTE: TEMPLATE_PROJECT_NAME / TEMPLATE_DOMAIN (str)
# SUMMARY: The identity this file ships with while it still describes the template rather than a
# project. `is_template` is deliberately NOT in REQUIRED_TOP_LEVEL_KEYS — making it required would
# turn every existing project red the moment it pulled this update, with no migration path. Absent
# means "not decided yet", the state a project that has not read this rule is in.
# test_validate_repository_metadata.py keeps TEMPLATE_DOMAIN identical to the shipped file.
TEMPLATE_PROJECT_NAME = "Mayak"
TEMPLATE_DOMAIN = (
    "Reusable AI-friendly FastAPI backend template. The kernel ships a CompositionRoot, "
    "semantic NDJSON logging, an LLMService with mock-first ADR-003 default, and the Mayak AI "
    "tooling (query CLI, validators, doctor, skills). Verticals (RAG / agents / domain-specific "
    "services) are added on top by extending build_reference_services and wiring typed dependency "
    "aliases."
)


# CLASS: validate_repository_metadata.ProjectContextIssue
# SUMMARY: One schema or cross-reference problem in docs/project_context.json.
@dataclass(slots=True)
class ProjectContextIssue:
    rule_id: str
    field: str
    message: str
    severity: str = "error"


def _pc(rule_id: str, field: str, message: str) -> ProjectContextIssue:
    return ProjectContextIssue(rule_id=rule_id, field=field, message=message)


def _validate_top_level(data: dict[str, object]) -> list[ProjectContextIssue]:
    issues: list[ProjectContextIssue] = []
    for key, expected_type in REQUIRED_TOP_LEVEL_KEYS.items():
        if key not in data:
            issues.append(
                _pc(
                    "project_context.missing_required_field",
                    key,
                    f"Missing required field '{key}'.",
                )
            )
        elif not isinstance(data[key], expected_type):
            issues.append(
                _pc(
                    "project_context.missing_required_field",
                    key,
                    f"Field '{key}' must be {expected_type.__name__}, got {type(data[key]).__name__}.",
                )
            )
    return issues


def _validate_identity(data: dict[str, object]) -> list[ProjectContextIssue]:
    """Once the file claims to describe a real project, refuse the template's leftover identity."""
    if data.get("is_template") is not False:
        return []
    issues: list[ProjectContextIssue] = []
    if data.get("project_name") == TEMPLATE_PROJECT_NAME:
        issues.append(
            _pc(
                "project_context.template_identity_not_replaced",
                "project_name",
                f"project_name is still '{TEMPLATE_PROJECT_NAME}' while is_template is false. "
                "Set it to this project's real name.",
            )
        )
    domain = data.get("domain")
    if isinstance(domain, str) and " ".join(domain.split()) == TEMPLATE_DOMAIN:
        issues.append(
            _pc(
                "project_context.template_identity_not_replaced",
                "domain",
                "domain still describes the template while is_template is false. "
                "Replace it with what this service actually does.",
            )
        )
    return issues


# FUNCTION: _validate_entries
# SUMMARY: Shared shape check for a verticals/integrations/business_rules section: every entry
# must be an object and carry its required sub-fields. The per-section extras (an enum field, a
# cross-reference, a list-typed field) are layered on by each caller below, over the same entries.
def _validate_entries(
    entries: dict[str, object],
    prefix: str,
    kind: str,
    required: tuple[str, ...],
) -> list[ProjectContextIssue]:
    issues: list[ProjectContextIssue] = []
    for name, entry in entries.items():
        entry_prefix = f"{prefix}.{name}"
        if not isinstance(entry, dict):
            issues.append(
                _pc(
                    "project_context.missing_field",
                    entry_prefix,
                    f"{kind} entry must be an object.",
                )
            )
            continue
        for field in required:
            if field not in entry:
                issues.append(
                    _pc(
                        "project_context.missing_field",
                        f"{entry_prefix}.{field}",
                        f"Missing required field '{field}'.",
                    )
                )
    return issues


def _validate_verticals(verticals: dict[str, object]) -> list[ProjectContextIssue]:
    issues = _validate_entries(verticals, "verticals", "Vertical", ("description", "status"))
    for name, entry in verticals.items():
        if not isinstance(entry, dict):
            continue
        prefix = f"verticals.{name}"
        status = entry.get("status")
        if status is not None and status not in VALID_VERTICAL_STATUSES:
            issues.append(
                _pc(
                    "project_context.invalid_vertical_status",
                    f"{prefix}.status",
                    f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_VERTICAL_STATUSES))}.",
                )
            )
        for list_field in ("domain_entities", "business_rules"):
            if list_field in entry and not isinstance(entry[list_field], list):
                issues.append(
                    _pc(
                        "project_context.missing_field",
                        f"{prefix}.{list_field}",
                        f"Field '{list_field}' must be a list.",
                    )
                )
    return issues


def _validate_integrations(integrations: dict[str, object]) -> list[ProjectContextIssue]:
    issues = _validate_entries(integrations, "integrations", "Integration", ("type", "description"))
    for name, entry in integrations.items():
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        if entry_type is not None and entry_type not in VALID_INTEGRATION_TYPES:
            issues.append(
                _pc(
                    "project_context.invalid_integration_type",
                    f"integrations.{name}.type",
                    f"Invalid type '{entry_type}'. Must be one of: {', '.join(sorted(VALID_INTEGRATION_TYPES))}.",
                )
            )
    return issues


def _validate_business_rules(
    business_rules: dict[str, object],
    vertical_names: set[str],
) -> list[ProjectContextIssue]:
    issues = _validate_entries(
        business_rules, "business_rules", "Business rule", ("summary", "vertical")
    )
    for rule_id, entry in business_rules.items():
        if not isinstance(entry, dict):
            continue
        vertical = entry.get("vertical")
        if vertical is not None and vertical != "cross-cutting" and vertical not in vertical_names:
            issues.append(
                _pc(
                    "project_context.cross_reference_unknown",
                    f"business_rules.{rule_id}.vertical",
                    f"Vertical '{vertical}' not found in verticals section.",
                )
            )
    return issues


def _validate_cross_references(
    verticals: dict[str, object],
    business_rule_ids: set[str],
) -> list[ProjectContextIssue]:
    """Check that BR-ids referenced from verticals exist in business_rules."""
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
                    _pc(
                        "project_context.cross_reference_unknown",
                        f"verticals.{name}.business_rules",
                        f"Referenced business rule '{br_id}' not found in business_rules section.",
                    )
                )
    return issues


def collect_project_context_issues(root_dir: Path) -> list[ProjectContextIssue]:
    """Load and validate docs/project_context.json, returning all found issues.

    Cross-referencing declared vertical status against the wiring files
    (service_registration.py / router_registration.py) is deliberately not done here: status is a
    description, and scripts/validate_endpoint_wiring.py is the validator that actually checks
    wiring — one fact belongs in one place.
    """
    context_path = root_dir / "docs" / "project_context.json"
    if not context_path.exists():
        return [
            _pc(
                "project_context.invalid_json",
                "file",
                "docs/project_context.json does not exist. Run: uv run python init_project_context.py",
            )
        ]
    try:
        data = json.loads(context_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [_pc("project_context.invalid_json", "file", f"Invalid JSON: {error}")]
    if not isinstance(data, dict):
        return [_pc("project_context.invalid_json", "file", "Root must be a JSON object.")]

    issues: list[ProjectContextIssue] = []
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

    return issues


def _project_context_issue_to_payload(issue: ProjectContextIssue) -> dict[str, object]:
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category="project_context",
        file=issue.field,
        line=1,
        message=issue.message,
        severity=issue.severity,
        playbook=get_repository_metadata_rule_playbook(issue.rule_id),
        extra=asdict(issue),
    )


# ------------------------------------------------------------------------------------------------
# Combined entry point
# ------------------------------------------------------------------------------------------------

# TYPE: RepositoryMetadataIssue
# SUMMARY: The union every issue this module produces belongs to. No shared base class — each
# collector keeps its own dataclass shape, and _issue_to_payload below (and the doctor) dispatch on
# isinstance the same way.
RepositoryMetadataIssue = SkillFrontmatterIssue | ScriptPathIssue | ProjectContextIssue


def collect_repository_metadata_issues(root_dir: Path) -> list[RepositoryMetadataIssue]:
    """Run all three former validators and return their issues as one combined list."""
    return [
        *collect_skills_frontmatter_issues(root_dir),
        *collect_project_context_issues(root_dir),
        *collect_script_path_issues(root_dir),
    ]


def _issue_to_payload(issue: RepositoryMetadataIssue) -> dict[str, object]:
    if isinstance(issue, SkillFrontmatterIssue):
        return _skills_frontmatter_issue_to_payload(issue)
    if isinstance(issue, ScriptPathIssue):
        return _script_paths_issue_to_payload(issue)
    return _project_context_issue_to_payload(issue)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate skills/commands frontmatter, path-shaped literals in scripts/*.py, and "
            "docs/project_context.json — the three repository-metadata checks."
        )
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit structured machine-readable results."
    )
    args = parser.parse_args([] if argv is None else argv)

    issues = collect_repository_metadata_issues(ROOT_DIR)
    if not issues:
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            print("Repository metadata validation passed.")
        return 0

    if args.json:
        print(
            render_json(
                {"status": "error", "issues": [_issue_to_payload(issue) for issue in issues]}
            ),
            end="",
        )
        return 1

    for issue in issues:
        print(f"[{issue.rule_id}] {issue.message}")
    print(f"\n{len(issues)} issue(s) found.")
    return 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
