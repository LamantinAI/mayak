# FILE: validate_cbm.py
# SUMMARY: Repository utility that validates required Code-Base Markup annotations in production Python files.

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ai_context.validator_contract import build_validator_issue_payload


# ATTRIBUTE: CLASS_TAGS (set[str])
# SUMMARY: Allowed CBM tags for class-like definitions.
CLASS_TAGS = {"CLASS", "DATACLASS", "ENUM"}


# DATACLASS: validate_cbm.ValidationIssue
# SUMMARY: Represents a single CBM validation problem in a Python source file.
@dataclass(slots=True)
class ValidationIssue:
    # ATTRIBUTE: path (Path)
    # SUMMARY: File containing the validation issue.
    path: Path

    # ATTRIBUTE: line (int)
    # SUMMARY: 1-based line number associated with the issue.
    line: int

    # ATTRIBUTE: message (str)
    # SUMMARY: Human-readable description of the problem.
    message: str


# FUNCTION: _is_production_python_file
# SUMMARY: Check whether a file is within the strict CBM validation scope.
# OUTPUT: (bool): True when the file must satisfy strict CBM validation.
def _is_production_python_file(path: Path) -> bool:
    parts = path.parts
    return len(parts) >= 2 and parts[0] == "project" and path.suffix == ".py"


# FUNCTION: _read_comment_block
# SUMMARY: Read the contiguous CBM comment block immediately above a definition.
# INPUT: start_line (int): 1-based line number where the definition or decorator block starts.
# OUTPUT: (list[str]): Comment block lines without leading '# ' prefixes.
def _read_comment_block(lines: list[str], start_line: int) -> list[str]:
    block: list[str] = []
    cursor = start_line - 2
    while cursor >= 0:
        stripped = lines[cursor].strip()
        if not stripped:
            if block:
                break
            cursor -= 1
            continue
        if not stripped.startswith("#"):
            break
        block.append(stripped[1:].strip())
        cursor -= 1
    block.reverse()
    return block


# FUNCTION: _read_attribute_comment_block
# SUMMARY: Read only the contiguous CBM comment block immediately above an attribute assignment.
# INPUT: start_line (int): 1-based line number where the assignment starts.
# OUTPUT: (list[str]): Contiguous comment block lines without leading '# ' prefixes.
def _read_attribute_comment_block(lines: list[str], start_line: int) -> list[str]:
    # **LOGIC_STEP**: Stop at the first blank line so file or class headers do not satisfy attribute metadata.
    block: list[str] = []
    cursor = start_line - 2
    while cursor >= 0:
        stripped = lines[cursor].strip()
        if not stripped:
            break
        if not stripped.startswith("#"):
            break
        block.append(stripped[1:].strip())
        cursor -= 1
    block.reverse()
    return block


# FUNCTION: _read_file_header_block
# SUMMARY: Read the initial comment block from the top of a Python file.
# OUTPUT: (list[str]): Leading comment block lines without leading '# ' prefixes.
def _read_file_header_block(lines: list[str]) -> list[str]:
    block: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if block:
                break
            continue
        if not stripped.startswith("#"):
            break
        block.append(stripped[1:].strip())
    return block


# FUNCTION: _tag_present
# SUMMARY: Check whether a comment block contains one of the required CBM tags.
# OUTPUT: (bool): True if any required tag is present.
def _tag_present(block: list[str], tags: set[str]) -> bool:
    return any(any(line.startswith(f"{tag}:") for tag in tags) for line in block)


# FUNCTION: _summary_present
# SUMMARY: Check whether a comment block contains a CBM summary tag.
# OUTPUT: (bool): True if the block contains a summary tag.
def _summary_present(block: list[str]) -> bool:
    return any(line.startswith("SUMMARY:") for line in block)


# FUNCTION: _requires_function_metadata
# SUMMARY: Report whether a function name falls under the strict-core CBM policy.
# OUTPUT: (bool): True when the function must declare FUNCTION and SUMMARY metadata.
def _requires_function_metadata(function_name: str) -> bool:
    # **LOGIC_STEP**: Require CBM for public call surfaces and constructors, but treat private helpers as optional detail.
    return function_name == "__init__" or not function_name.startswith("_")


# FUNCTION: _find_tag_value
# SUMMARY: Extract the payload for the first matching CBM tag in a comment block.
# OUTPUT: (tuple[str, str] | None): Matching tag name and raw payload, or None when absent.
def _find_tag_value(block: list[str], tags: set[str]) -> tuple[str, str] | None:
    for line in block:
        for tag in tags:
            prefix = f"{tag}:"
            if line.startswith(prefix):
                return tag, line[len(prefix) :].strip()
    return None


# FUNCTION: _extract_named_tag_identifier
# SUMMARY: Normalize the identifier encoded in a class, function, or attribute CBM tag payload.
# INPUT: raw_value (str): Raw CBM tag payload after the tag prefix.
# OUTPUT: (str | None): Final identifier segment or None when the payload is empty.
def _extract_named_tag_identifier(raw_value: str) -> str | None:
    candidate = raw_value.strip()
    if not candidate:
        return None
    for separator in ("(", ":", " "):
        candidate = candidate.split(separator, 1)[0]
    if "/" in candidate:
        candidate = candidate.rsplit("/", 1)[-1]
    if "." in candidate:
        candidate = candidate.rsplit(".", 1)[-1]
    return candidate or None


# FUNCTION: _validate_named_tag
# SUMMARY: Validate that a CBM tag payload points at the same identifier as the AST node being checked.
# INPUT: block (list[str]): Comment block lines associated with a definition or assignment.
# INPUT: subject (str): Human-readable node kind used in error messages.
# OUTPUT: (tuple[str, str] | None): Mismatched tag name and parsed identifier, otherwise None.
def _validate_named_tag(
    block: list[str],
    tags: set[str],
    expected_name: str,
    subject: str,
) -> tuple[str, str] | None:
    del subject
    tag_entry = _find_tag_value(block, tags)
    if tag_entry is None:
        return None
    tag_name, raw_value = tag_entry
    actual_name = _extract_named_tag_identifier(raw_value)
    if actual_name is None or actual_name == expected_name:
        return None
    return tag_name, actual_name


# FUNCTION: _find_tag_line
# SUMMARY: Locate the first matching named CBM tag line in the contiguous comment block above a definition.
# INPUT: lines (list[str]): Full source file split into lines without trailing newlines.
# INPUT: start_line (int): 1-based line number where the definition or decorator block starts.
# OUTPUT: (tuple[int, str, str] | None): Zero-based line index, tag name, and raw payload when found.
def _find_tag_line(
    lines: list[str],
    start_line: int,
    tags: set[str],
) -> tuple[int, str, str] | None:
    block_started = False
    cursor = start_line - 2
    while cursor >= 0:
        stripped = lines[cursor].strip()
        if not stripped:
            if block_started:
                break
            cursor -= 1
            continue
        if not stripped.startswith("#"):
            break
        block_started = True
        comment = stripped[1:].strip()
        for tag in tags:
            prefix = f"{tag}:"
            if comment.startswith(prefix):
                return cursor, tag, comment[len(prefix) :].strip()
        cursor -= 1
    return None


# FUNCTION: _rewrite_named_tag_payload
# SUMMARY: Replace the identifier portion of a named CBM tag payload while preserving module-like prefixes and suffix notes.
# INPUT: raw_value (str): Existing CBM tag payload after the tag prefix.
def _rewrite_named_tag_payload(raw_value: str, expected_name: str) -> str:
    candidate = raw_value.strip()
    if not candidate:
        return expected_name

    separator_indexes = [
        candidate.find(separator) for separator in ("(", ":", " ") if separator in candidate
    ]
    split_index = min(separator_indexes) if separator_indexes else len(candidate)
    head = candidate[:split_index]
    suffix = candidate[split_index:]

    if "/" in head:
        prefix, _separator, _tail = head.rpartition("/")
        updated_head = f"{prefix}/{expected_name}" if prefix else expected_name
    elif "." in head:
        prefix, _separator, _tail = head.rpartition(".")
        updated_head = f"{prefix}.{expected_name}" if prefix else expected_name
    else:
        updated_head = expected_name
    return f"{updated_head}{suffix}"


# FUNCTION: _replace_tag_line
# SUMMARY: Rebuild a CBM tag comment line with the updated tag payload while preserving indentation.
# INPUT: line (str): Original source line containing the CBM tag.
# INPUT: payload (str): Updated payload to write after the tag prefix.
def _replace_tag_line(line: str, tag_name: str, payload: str) -> str:
    hash_index = line.index("#")
    indent = line[:hash_index]
    has_space_after_hash = hash_index + 1 < len(line) and line[hash_index + 1] == " "
    separator = " " if has_space_after_hash else ""
    return f"{indent}#{separator}{tag_name}: {payload}"


# FUNCTION: _apply_named_tag_fix
# SUMMARY: Update a mismatched class-like or function CBM tag in place when the tag already exists.
# INPUT: lines (list[str]): Full source file split into lines without trailing newlines.
# INPUT: start_line (int): 1-based line number where the definition or decorator block starts.
# INPUT: tags (set[str]): Allowed tag names for the definition being fixed.
# INPUT: expected_name (str): Actual AST identifier name that the CBM tag should reference.
# OUTPUT: (bool): True when a tag line was rewritten.
def _apply_named_tag_fix(
    lines: list[str],
    start_line: int,
    tags: set[str],
    expected_name: str,
) -> bool:
    tag_line = _find_tag_line(lines, start_line, tags)
    if tag_line is None:
        return False

    line_index, tag_name, raw_value = tag_line
    actual_name = _extract_named_tag_identifier(raw_value)
    if actual_name == expected_name:
        return False

    lines[line_index] = _replace_tag_line(
        line=lines[line_index],
        tag_name=tag_name,
        payload=_rewrite_named_tag_payload(raw_value, expected_name),
    )
    return True


# FUNCTION: _definition_start_line
# SUMMARY: Compute the first line of a definition including decorators.
# OUTPUT: (int): 1-based line number for comment lookup.
def _definition_start_line(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        return min(decorator.lineno for decorator in decorators)
    return getattr(node, "lineno")


# FUNCTION: _attribute_name_from_target
# SUMMARY: Extract a stable attribute-like name from an assignment target when possible.
# OUTPUT: (str | None): Attribute name or None when the target shape is unsupported.
def _attribute_name_from_target(target: ast.expr) -> str | None:
    # **LOGIC_STEP**: Accept simple names and dotted class attributes while rejecting destructuring targets.
    if isinstance(target, ast.Name):
        return target.id
    return None


# FUNCTION: _is_typevar_assignment
# SUMMARY: Report whether an assignment creates a TypeVar-style helper that should not require ATTRIBUTE metadata.
# OUTPUT: (bool): True when the assignment is a TypeVar helper declaration.
def _is_typevar_assignment(node: ast.Assign | ast.AnnAssign) -> bool:
    # **LOGIC_STEP**: Inspect the assigned value and skip TypeVar declarations used only for typing.
    value = node.value
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    if isinstance(func, ast.Name):
        return func.id == "TypeVar"
    if isinstance(func, ast.Attribute):
        return func.attr == "TypeVar"
    return False


# FUNCTION: _is_type_alias_annotation
# SUMMARY: Report whether an annotated assignment declares a TypeAlias and should be skipped.
# OUTPUT: (bool): True when the node is a TypeAlias declaration.
def _is_type_alias_annotation(node: ast.Assign | ast.AnnAssign) -> bool:
    # **LOGIC_STEP**: Detect explicit TypeAlias annotations from typing.
    if not isinstance(node, ast.AnnAssign):
        return False
    annotation = node.annotation
    if isinstance(annotation, ast.Name):
        return annotation.id == "TypeAlias"
    if isinstance(annotation, ast.Attribute):
        return annotation.attr == "TypeAlias"
    return False


# FUNCTION: _iter_attribute_nodes
# SUMMARY: Yield assignment nodes that represent module-level or class-level attributes requiring CBM metadata.
def _iter_attribute_nodes(
    body: list[ast.stmt],
) -> list[tuple[str, ast.Assign | ast.AnnAssign]]:
    # **LOGIC_STEP**: Collect only simple single-target assignments that can be mapped to a stable attribute name.
    attributes: list[tuple[str, ast.Assign | ast.AnnAssign]] = []
    for statement in body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            name = _attribute_name_from_target(statement.targets[0])
            if (
                name is not None
                and not (name.startswith("__") and name.endswith("__"))
                and not _is_typevar_assignment(statement)
            ):
                attributes.append((name, statement))
        elif isinstance(statement, ast.AnnAssign):
            name = _attribute_name_from_target(statement.target)
            if (
                name is not None
                and not (name.startswith("__") and name.endswith("__"))
                and not _is_type_alias_annotation(statement)
                and not _is_typevar_assignment(statement)
            ):
                attributes.append((name, statement))
    return attributes


# FUNCTION: _is_enum_class
# SUMMARY: Report whether a class definition is an Enum-like type whose members should not be validated as attributes.
# OUTPUT: (bool): True when the class inherits from Enum.
def _is_enum_class(node: ast.ClassDef) -> bool:
    # **LOGIC_STEP**: Detect Enum inheritance by inspecting base class names.
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "Enum":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Enum":
            return True
    return False


# FUNCTION: _enclosing_function
# SUMMARY: Locate the innermost function definition that encloses a given source line.
# INPUT: line_number (int): 1-based source line number to locate.
# OUTPUT: (ast.FunctionDef | ast.AsyncFunctionDef | None): Enclosing function node or None when the line is outside any function body.
def _enclosing_function(
    function_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef],
    line_number: int,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    matches = [
        node
        for node in function_nodes
        if node.lineno <= line_number <= getattr(node, "end_lineno", node.lineno)
    ]
    if not matches:
        return None
    return max(matches, key=lambda node: node.lineno)


# FUNCTION: validate_python_source
# SUMMARY: Validate module, class, and function CBM annotations for a single Python file.
def validate_python_source(path: Path) -> list[ValidationIssue]:
    # **LOGIC_STEP**: Read & parse source under guarded exceptions so a broken file
    # surfaces as a structured ValidationIssue rather than a raw Python traceback.
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        return [
            ValidationIssue(
                path=path,
                line=1,
                message=f"UnicodeDecodeError while reading source: {error.reason}",
            )
        ]
    lines = source.splitlines()
    issues: list[ValidationIssue] = []

    # **LOGIC_STEP**: Reject module docstrings because strict CBM files must use comment-based metadata.
    if source.lstrip().startswith('"""') or source.lstrip().startswith("'''"):
        issues.append(
            ValidationIssue(
                path=path,
                line=1,
                message="Module docstrings are not allowed in strict CBM files",
            )
        )

    file_block = _read_file_header_block(lines)
    if not _tag_present(file_block, {"FILE"}):
        issues.append(ValidationIssue(path=path, line=1, message="Missing '# FILE:' tag"))
    if not _summary_present(file_block):
        issues.append(ValidationIssue(path=path, line=1, message="Missing file '# SUMMARY:' tag"))

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        issues.append(
            ValidationIssue(
                path=path,
                line=error.lineno or 1,
                message=f"SyntaxError while parsing source: {error.msg}",
            )
        )
        return issues
    function_nodes = [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    for line_number, line in enumerate(lines, start=1):
        if "**LOGIC_STEP**:" not in line:
            continue
        enclosing_function = _enclosing_function(function_nodes, line_number)
        if enclosing_function is None:
            issues.append(
                ValidationIssue(
                    path=path,
                    line=line_number,
                    message="'# **LOGIC_STEP**:' must appear inside a function body",
                )
            )

    for attribute_name, attribute_node in _iter_attribute_nodes(list(tree.body)):
        block = _read_attribute_comment_block(lines, attribute_node.lineno)
        if not _tag_present(block, {"ATTRIBUTE"}):
            continue
        mismatch = _validate_named_tag(
            block=block,
            tags={"ATTRIBUTE"},
            expected_name=attribute_name,
            subject="Attribute",
        )
        if mismatch is not None:
            tag_name, actual_name = mismatch
            issues.append(
                ValidationIssue(
                    path=path,
                    line=attribute_node.lineno,
                    message=(
                        f"Attribute '{attribute_name}' has mismatched '# {tag_name}:' "
                        f"tag name '{actual_name}'"
                    ),
                )
            )
        if not _summary_present(block):
            issues.append(
                ValidationIssue(
                    path=path,
                    line=attribute_node.lineno,
                    message=(
                        f"Attribute '{attribute_name}' includes '# ATTRIBUTE:' but is missing "
                        "'# SUMMARY:'"
                    ),
                )
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            start_line = _definition_start_line(node)
            block = _read_comment_block(lines, start_line)
            if not _tag_present(block, CLASS_TAGS):
                issues.append(
                    ValidationIssue(
                        path=path,
                        line=start_line,
                        message=f"Class '{node.name}' is missing a class-level CBM tag",
                    )
                )
            mismatch = _validate_named_tag(
                block=block,
                tags=CLASS_TAGS,
                expected_name=node.name,
                subject="Class",
            )
            if mismatch is not None:
                tag_name, actual_name = mismatch
                issues.append(
                    ValidationIssue(
                        path=path,
                        line=start_line,
                        message=(
                            f"Class '{node.name}' has mismatched '# {tag_name}:' "
                            f"tag name '{actual_name}'"
                        ),
                    )
                )
            if not _summary_present(block):
                issues.append(
                    ValidationIssue(
                        path=path,
                        line=start_line,
                        message=f"Class '{node.name}' is missing '# SUMMARY:'",
                    )
                )
            if not _is_enum_class(node):
                for attribute_name, attribute_node in _iter_attribute_nodes(list(node.body)):
                    block = _read_attribute_comment_block(lines, attribute_node.lineno)
                    if not _tag_present(block, {"ATTRIBUTE"}):
                        continue
                    mismatch = _validate_named_tag(
                        block=block,
                        tags={"ATTRIBUTE"},
                        expected_name=attribute_name,
                        subject="Attribute",
                    )
                    if mismatch is not None:
                        tag_name, actual_name = mismatch
                        issues.append(
                            ValidationIssue(
                                path=path,
                                line=attribute_node.lineno,
                                message=(
                                    f"Attribute '{node.name}.{attribute_name}' has mismatched "
                                    f"'# {tag_name}:' tag name '{actual_name}'"
                                ),
                            )
                        )
                    if not _summary_present(block):
                        issues.append(
                            ValidationIssue(
                                path=path,
                                line=attribute_node.lineno,
                                message=(
                                    f"Attribute '{node.name}.{attribute_name}' includes "
                                    "'# ATTRIBUTE:' but is missing '# SUMMARY:'"
                                ),
                            )
                        )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start_line = _definition_start_line(node)
            block = _read_comment_block(lines, start_line)
            if not _requires_function_metadata(node.name):
                if not _tag_present(block, {"FUNCTION"}):
                    continue
            if not _tag_present(block, {"FUNCTION"}):
                issues.append(
                    ValidationIssue(
                        path=path,
                        line=start_line,
                        message=f"Function '{node.name}' is missing '# FUNCTION:'",
                    )
                )
            mismatch = _validate_named_tag(
                block=block,
                tags={"FUNCTION"},
                expected_name=node.name,
                subject="Function",
            )
            if mismatch is not None:
                tag_name, actual_name = mismatch
                issues.append(
                    ValidationIssue(
                        path=path,
                        line=start_line,
                        message=(
                            f"Function '{node.name}' has mismatched '# {tag_name}:' "
                            f"tag name '{actual_name}'"
                        ),
                    )
                )
            if not _summary_present(block):
                issues.append(
                    ValidationIssue(
                        path=path,
                        line=start_line,
                        message=f"Function '{node.name}' is missing '# SUMMARY:'",
                    )
                )

    return issues


# FUNCTION: fix_python_source
# SUMMARY: Rewrite mismatched class-like and function CBM tag names in a single Python source file.
# OUTPUT: (bool): True when the file content changed.
def fix_python_source(path: Path) -> bool:
    # **LOGIC_STEP**: Skip files that cannot be read/parsed; validate run will surface the SyntaxError/UnicodeDecodeError as a structured ValidationIssue.
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    updated = False

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            start_line = _definition_start_line(node)
            updated = (
                _apply_named_tag_fix(
                    lines=lines,
                    start_line=start_line,
                    tags=CLASS_TAGS,
                    expected_name=node.name,
                )
                or updated
            )
            continue

        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start_line = _definition_start_line(node)
        block = _read_comment_block(lines, start_line)
        if not _requires_function_metadata(node.name) and not _tag_present(block, {"FUNCTION"}):
            continue
        updated = (
            _apply_named_tag_fix(
                lines=lines,
                start_line=start_line,
                tags={"FUNCTION"},
                expected_name=node.name,
            )
            or updated
        )

    if not updated:
        return False

    normalized_source = "\n".join(lines)
    if source.endswith("\n"):
        normalized_source = f"{normalized_source}\n"
    path.write_text(normalized_source, encoding="utf-8")
    return True


# FUNCTION: default_repo_root
# SUMMARY: Resolve the repository root this script validates by default.
# OUTPUT: (Path): Directory containing the project/ package, two levels above this file.
def default_repo_root() -> Path:
    # **LOGIC_STEP**: This file lives in scripts/, so the root is two levels up. A single
    # .parent pointed the walk at scripts/ itself, where no relative path starts with
    # "project/", so the production-file filter matched nothing and the validator
    # reported success for every input. Guarded by tests/application/test_validate_cbm.py.
    return Path(__file__).resolve().parent.parent


# FUNCTION: collect_validation_issues
# SUMMARY: Validate all strict-scope production Python files in the repository.
def collect_validation_issues(repo_root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path in sorted(repo_root.rglob("*.py")):
        relative_path = path.relative_to(repo_root)
        if not _is_production_python_file(relative_path):
            continue
        issues.extend(validate_python_source(path))
    return issues


# FUNCTION: _fix_validation_sources
# SUMMARY: Apply CBM tag-name autofixes across all strict-scope production Python files in the repository.
def _fix_validation_sources(repo_root: Path) -> list[Path]:
    updated_paths: list[Path] = []
    for path in sorted(repo_root.rglob("*.py")):
        relative_path = path.relative_to(repo_root)
        if not _is_production_python_file(relative_path):
            continue
        if fix_python_source(path):
            updated_paths.append(path)
    return updated_paths


# ATTRIBUTE: _CBM_RULE_MAP (dict[str, tuple[str, str]])
# SUMMARY: Maps message keyword patterns to (rule_id, suggested_fix) pairs for JSON output.
_CBM_RULE_MAP: list[tuple[str, str, str]] = [
    (
        "Module docstrings are not allowed",
        "cbm.no_module_docstring",
        "Delete the docstring and put the same sentence after '# FILE:' as '# SUMMARY:'.",
    ),
    (
        "Missing '# FILE:'",
        "cbm.missing_file_tag",
        "Open the file and write '# FILE: <its own path>' on line one, '# SUMMARY:' on line two.",
    ),
    (
        "Missing file '# SUMMARY:'",
        "cbm.missing_file_summary",
        "The '# FILE:' line is there but says nothing about the file. Follow it with '# SUMMARY:'.",
    ),
    (
        "**LOGIC_STEP**:' must appear inside",
        "cbm.logic_step_outside_function",
        "This marker explains a step of an algorithm, so it belongs among the statements that "
        "perform it. Above a class or at module level, use '# NOTE:' instead.",
    ),
    (
        "is missing a class-level CBM tag",
        "cbm.missing_class_tag",
        "Put '# CLASS: <module.path.ClassName>' and '# SUMMARY:' directly above the class, "
        "under any decorators.",
    ),
    (
        "is missing '# FUNCTION:'",
        "cbm.missing_function_tag",
        "Put '# FUNCTION: <name>' and '# SUMMARY:' directly above the def, under any decorators.",
    ),
    (
        "is missing '# SUMMARY:'",
        "cbm.missing_summary",
        "The block above this definition names it but never says what it is for. Add '# SUMMARY:'.",
    ),
    (
        "has mismatched",
        "cbm.mismatched_tag",
        "The name in the tag and the name in the code have drifted apart. `--fix` rewrites the "
        "tag to follow the code, which is right whenever the code was renamed deliberately.",
    ),
    (
        "but is missing '# SUMMARY:'",
        "cbm.attribute_missing_summary",
        "An '# ATTRIBUTE:' line names a field and its type. Say on the next line what it holds.",
    ),
    (
        "SyntaxError while parsing",
        "cbm.syntax_error",
        "Nothing can be checked until the file parses. Fix the syntax at the reported line first.",
    ),
    (
        "UnicodeDecodeError while reading",
        "cbm.read_error",
        "The bytes are not valid UTF-8. Re-save the file in that encoding and run again.",
    ),
]


# FUNCTION: classify_issue
# SUMMARY: Derive a stable rule_id and suggested fix from a ValidationIssue message. Public so doctor_ai_context.py can route CBM issues to the correct rule_id instead of hard-coding a single literal.
# OUTPUT: (tuple[str, str]): (rule_id, suggested_fix) pair.
def classify_issue(message: str) -> tuple[str, str]:
    for keyword, rule_id, fix in _CBM_RULE_MAP:
        if keyword in message:
            return rule_id, fix
    return (
        "cbm.unknown",
        "No remediation is registered for this message. Read the reported line and, if the "
        "message is a new one, add it to _CBM_RULE_MAP.",
    )


# FUNCTION: classify_issue_rule_id
# SUMMARY: Convenience wrapper that returns only the rule_id derived from a ValidationIssue message. Public so doctor_ai_context.py can route CBM issues to the correct rule_id without unpacking tuples.
# OUTPUT: (str): Stable rule_id matching one of _CBM_RULE_MAP entries or 'cbm.unknown'.
def classify_issue_rule_id(message: str) -> str:
    rule_id, _ = classify_issue(message)
    return rule_id


# FUNCTION: _issue_to_json
# SUMMARY: Convert a ValidationIssue to a JSON-serialisable dict with remediation guidance.
# INPUT: repo_root (Path): Repository root for relative path computation.
# OUTPUT: (dict): JSON-friendly dict with rule_id, file, line, message, and guidance fields.
def _issue_to_json(issue: ValidationIssue, repo_root: Path) -> dict:
    rule_id, suggested_fix = classify_issue(issue.message)
    relative_path = str(issue.path.relative_to(repo_root))
    playbook = get_cbm_rule_playbook(rule_id)
    entry = build_validator_issue_payload(
        rule_id=rule_id,
        category="cbm",
        file=relative_path,
        line=issue.line,
        message=issue.message,
        playbook=playbook,
    )
    # **LOGIC_STEP**: Preserve the classify_issue-derived suggested_fix as the primary source —
    # it carries the autofix-aware wording (e.g. mismatched_tag's --fix hint) — while still
    # sourcing read_first/next_commands/stop_widening_condition from the shared rule playbook.
    entry["suggested_fix"] = suggested_fix
    if rule_id == "cbm.mismatched_tag":
        entry["next_commands"] = ["uv run python scripts/validate_cbm.py --fix"]
    return entry


# FUNCTION: get_cbm_rule_playbook
# SUMMARY: Return a failure-playbook dict for a given CBM rule_id, used by query_ai_context.py.
# OUTPUT: (dict | None): Playbook dict or None if the rule is not a CBM rule.
def get_cbm_rule_playbook(rule_id: str) -> dict | None:
    if not rule_id.startswith("cbm."):
        return None
    fix_map: dict[str, str] = {}
    for _keyword, rid, fix in _CBM_RULE_MAP:
        fix_map[rid] = fix
    # **LOGIC_STEP**: The prefix is not enough. Until 2026-08-14 any `cbm.<anything>` produced a
    # full, plausible playbook — `failure rule cbm.this_does_not_exist` answered with the generic
    # meaning and exit 0, while every other rule family answered "Unknown failure rule ID" and
    # exit 1. An agent that mistyped a rule id, or invented one from a half-remembered message,
    # got a confident answer about a rule that does not exist.
    # **LOGIC_STEP**: `cbm.unknown` is a real answer from classify_issue — the fallback for a
    # message no keyword matched — so it keeps its playbook; anything else outside the map does not.
    if rule_id not in fix_map and rule_id != "cbm.unknown":
        return None
    suggested_fix = fix_map.get(rule_id, "Fix the reported CBM annotation issue.")
    autofix_note = ""
    if rule_id == "cbm.mismatched_tag":
        autofix_note = (
            " Alternatively, run 'uv run python scripts/validate_cbm.py --fix' to auto-correct."
        )
    return {
        "meaning": (
            "A production Python module is missing or has incorrect Code-Base Markup "
            "annotations. CBM markers help AI agents and developers navigate the codebase."
        ),
        "suggested_fix": suggested_fix + autofix_note,
        "read_first": ["CLAUDE.md"],
        "smallest_command_to_rerun": "uv run python scripts/validate_cbm.py",
        "likely_fix_shape": ("Add or correct the CBM comment tag above the reported definition."),
        "next_checks": [
            "uv run python scripts/validate_cbm.py",
            "make quality-gates",
        ],
        "stop_widening_condition": ("Stop once validate_cbm.py passes with no reported issues."),
    }


# FUNCTION: main
# SUMMARY: Run strict CBM validation and return a process exit code.
# INPUT: argv (Sequence[str] | None): Optional CLI arguments supporting autofix and JSON modes.
# OUTPUT: (int): Zero when validation succeeds, non-zero otherwise.
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate strict CBM annotations and optionally autofix mismatched tag names."
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Autofix mismatched '# FUNCTION:' and class-like tag names before validating.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output with remediation guidance.",
    )
    # **LOGIC_STEP**: Default to an empty argv when called directly from tests so that
    # pytest's own argv does not leak into argparse via sys.argv[1:].
    args = parser.parse_args([] if argv is None else argv)

    repo_root = default_repo_root()
    fixed_paths: list[Path] = []
    if args.fix:
        fixed_paths = _fix_validation_sources(repo_root)
    issues = collect_validation_issues(repo_root)

    if args.json:
        payload = {
            "status": "ok" if not issues else "error",
            "fixed_files": len(fixed_paths),
            "issues": [_issue_to_json(i, repo_root) for i in issues],
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1 if issues else 0

    if not issues:
        if fixed_paths:
            print(f"CBM validation passed after fixing {len(fixed_paths)} file(s).")
        else:
            print("CBM validation passed.")
        return 0

    for issue in issues:
        relative_path = issue.path.relative_to(repo_root)
        print(f"{relative_path}:{issue.line}: {issue.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
