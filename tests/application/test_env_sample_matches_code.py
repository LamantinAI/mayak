# FILE: tests/application/test_env_sample_matches_code.py
# SUMMARY: Guard against .env.sample drifting from the defaults declared in the settings models.
# NOTE: This is the gate that was missing when .env.sample shipped AGENT_MAX_TOKENS=2000 against
# a code default of 4096 — the sample wins at runtime, so every project created from the template
# started with a value the code's own comment described as a bug.

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from pydantic_settings import BaseSettings

from project.core.config_settings_core import (
    LLMSettings,
    PostgresSettings,
    ProjectSettings,
    ServerSettings,
)
from project.core.config_settings_agent import AgentSettings
from project.core.config_settings_observability import ObservabilitySettings

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ATTRIBUTE: _SETTINGS_MODELS (tuple[type[BaseSettings], ...])
# SUMMARY: Every settings model whose env_prefix appears in .env.sample.
_SETTINGS_MODELS: tuple[type[BaseSettings], ...] = (
    ProjectSettings,
    ServerSettings,
    PostgresSettings,
    LLMSettings,
    AgentSettings,
    ObservabilitySettings,
)

# ATTRIBUTE: _INTENTIONAL_OVERRIDES (frozenset[str])
# SUMMARY: Keys whose sample value is deliberately not the code default.
_INTENTIONAL_OVERRIDES = frozenset(
    {
        # Placeholders a human must replace before the service can start.
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "OPENAI_COMPATIBLE_API_KEY",
        "OPENAI_COMPATIBLE_BASE_URL",
        "OPENAI_COMPATIBLE_MODEL",
        # Local-development conveniences that differ from the production-safe default.
        "APP_NAME",
        "SERVER_HOST",
        "APP_LOG_DIR",
        # Environment-shaped values: the container network names its own hosts, and the
        # functional stack has no outbound network for a probe-style readiness check.
        "POSTGRES_HOST",
        "AGENT_LLM_READINESS_CHECK_MODE",
        "SERVER_CORS_ORIGINS",
    }
)


# FUNCTION: _comparable
# SUMMARY: Render a value in a form that compares equal across JSON and Python spellings.
# INPUT: value (Any): Raw value from the sample file or from a field default.
# OUTPUT: (str): Canonical text form.
def _comparable(value: Any) -> str:
    # **LOGIC_STEP**: Unwrap SecretStr before stringifying. str(SecretStr('')) is '**********',
    # so without this a documented empty default compares unequal to an empty code default and
    # the guard reports three mismatches that are not mismatches.
    unwrapped = value.get_secret_value() if isinstance(value, SecretStr) else value
    text = str(unwrapped).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    try:
        return json.dumps(json.loads(text.replace("'", '"')), sort_keys=True)
    except (json.JSONDecodeError, ValueError):
        return text.lower() if text.lower() in {"true", "false"} else text


# FUNCTION: _env_prefix
# SUMMARY: Read the env prefix a settings model declares.
# OUTPUT: (str): Prefix such as "APP_", or an empty string when the model declares none.
def _env_prefix(model: type[BaseSettings]) -> str:
    return str(model.model_config.get("env_prefix", ""))


# FUNCTION: _env_key
# SUMMARY: Resolve the environment variable name pydantic-settings actually reads for a field.
# INPUT: prefix (str): The model's env_prefix.
# INPUT: field_name (str): Python attribute name.
# INPUT: field (Any): The FieldInfo, consulted for validation_alias.
# OUTPUT: (str): Upper-case variable name.
def _env_key(prefix: str, field_name: str, field: Any) -> str:
    # **LOGIC_STEP**: A validation_alias replaces the whole name, prefix included. Building the
    # key from field_name alone invented DEEP_TRACE_ENABLED, which appears in no file, while the
    # variable that does exist — ENABLE_FULL_TRACE — was never compared against anything.
    alias = field.validation_alias
    if isinstance(alias, str):
        return alias.upper()
    choices = getattr(alias, "choices", None)
    if choices:
        first = next((choice for choice in choices if isinstance(choice, str)), None)
        if first is not None:
            return first.upper()
    return f"{prefix}{field_name}".upper()


# FUNCTION: _code_defaults
# SUMMARY: Map every environment variable name to the default its settings field declares.
# OUTPUT: (dict[str, Any]): Environment variable name to default value.
def _code_defaults() -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for model in _SETTINGS_MODELS:
        prefix = _env_prefix(model)
        for field_name, field in model.model_fields.items():
            # **LOGIC_STEP**: Only a field with no default at all is skipped. `default=None` used
            # to be skipped too, which quietly took every optional setting out of the guard's
            # reach — the exemption list could not restore what never entered the map.
            if repr(field.default) == "PydanticUndefined":
                continue
            defaults[_env_key(prefix, field_name, field)] = field.default
    return defaults


# FUNCTION: _sample_assignments
# SUMMARY: Parse KEY=VALUE assignments out of an env sample file.
# INPUT: path (Path): File to read.
# OUTPUT: (dict[str, str]): Assignment map with comments and blank lines removed.
def _sample_assignments(path: Path) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        assignments[key.strip()] = value.strip().strip("\"'")
    return assignments


# CLASS: tests.application.test_env_sample_matches_code.TestEnvSampleMatchesCodeDefaults
# SUMMARY: Verify the shipped sample never quietly contradicts a settings default.
class TestEnvSampleMatchesCodeDefaults:
    # FUNCTION: test_sample_values_match_settings_defaults
    # SUMMARY: Verify every sample assignment either equals the code default or is an allowed override.
    @pytest.mark.unit
    def test_sample_values_match_settings_defaults(self) -> None:
        defaults = _code_defaults()
        sample = _sample_assignments(_REPO_ROOT / ".env.sample")

        mismatches = []
        for key, sample_value in sample.items():
            if key in _INTENTIONAL_OVERRIDES or key not in defaults:
                continue
            expected = defaults[key]
            # **LOGIC_STEP**: `KEY=` is how an env file spells "no value", which is what a None
            # default means. Comparing it against the text "None" would report every optional
            # setting as drift.
            if expected is None and sample_value == "":
                continue
            expected_text = _comparable(expected)
            if _comparable(sample_value) != expected_text:
                mismatches.append(f"{key}: sample={sample_value!r} code_default={expected_text!r}")

        assert mismatches == []

    # FUNCTION: test_functional_sample_agrees_with_root_sample
    # SUMMARY: Verify the two shipped samples do not disagree on a shared key.
    @pytest.mark.unit
    def test_functional_sample_agrees_with_root_sample(self) -> None:
        root = _sample_assignments(_REPO_ROOT / ".env.sample")
        functional = _sample_assignments(_REPO_ROOT / "tests" / "functional" / ".env.sample")

        shared = set(root) & set(functional) - _INTENTIONAL_OVERRIDES
        disagreements = [
            f"{key}: root={root[key]!r} functional={functional[key]!r}"
            for key in sorted(shared)
            if root[key] != functional[key]
        ]

        assert disagreements == []


# FUNCTION: _flag_gating_the_log_file
# SUMMARY: Read out of the launcher which settings attribute actually decides whether a log file is written.
# OUTPUT: (str): Attribute name, e.g. "full_trace_enabled".
def _flag_gating_the_log_file() -> str:
    tree = ast.parse((_REPO_ROOT / "project" / "launcher" / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        body_calls = {
            child.func.id
            for child in ast.walk(ast.Module(body=node.body, type_ignores=[]))
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
        }
        if "create_run_log_path" not in body_calls:
            continue
        # **LOGIC_STEP**: The condition is an attribute chain; its last name is the settings field.
        assert isinstance(node.test, ast.Attribute), f"unexpected gate: {ast.unparse(node.test)}"
        return node.test.attr
    raise AssertionError("no branch in project/launcher/main.py calls create_run_log_path")


# FUNCTION: _sample_comment_above
# SUMMARY: Collect the contiguous comment lines immediately above an assignment in an env sample.
# INPUT: path (Path): File to read.
# INPUT: key (str): Assignment key, e.g. "APP_LOG_DIR".
# OUTPUT: (str): The comment block, newline-joined.
def _sample_comment_above(path: Path, key: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith(f"{key}="))
    comment: list[str] = []
    while index > 0 and lines[index - 1].lstrip().startswith("#"):
        index -= 1
        comment.insert(0, lines[index])
    return "\n".join(comment)


# CLASS: tests.application.test_env_sample_matches_code.TestLogFileGateIsDocumentedAsItself
# SUMMARY: Verify everything describing APP_LOG_DIR names the flag that really creates the file.
# NOTE: Three places — .env.sample, the CBM comment, and the Pydantic description visible in the
# settings schema — all said the log file appears "in debug mode". It does not: the launcher gates
# it on ENABLE_FULL_TRACE, deliberately separated from APP_DEBUG so an eval stand can collect
# traces without reload and verbose stdout. A reader following the documentation set APP_DEBUG=true
# and got no file. The flag is read out of the launcher here, so moving the gate breaks this test
# rather than quietly re-creating the same lie.
class TestLogFileGateIsDocumentedAsItself:
    # FUNCTION: test_documentation_names_the_flag_the_launcher_checks
    # SUMMARY: Verify the field description and the sample comment name the real gate, not APP_DEBUG.
    @pytest.mark.unit
    def test_documentation_names_the_flag_the_launcher_checks(self) -> None:
        gate_field = _flag_gating_the_log_file()
        gate_variable = _env_key(
            _env_prefix(ObservabilitySettings),
            gate_field,
            ObservabilitySettings.model_fields[gate_field],
        )
        description = str(ProjectSettings.model_fields["log_dir"].description)
        sample_comment = _sample_comment_above(_REPO_ROOT / ".env.sample", "APP_LOG_DIR")

        assert gate_variable in description
        assert gate_variable in sample_comment
        # **LOGIC_STEP**: "debug mode" alone, with no mention of the real gate, is the exact
        # wording that misdirected the reader; the sample may still contrast the two flags.
        assert "debug mode" not in description.lower()


# CLASS: tests.application.test_env_sample_matches_code.TestReadmePythonVersions
# SUMMARY: Verify the hand-written README agrees with the files that own the version numbers.
class TestReadmePythonVersions:
    # FUNCTION: test_readme_states_the_declared_versions
    # SUMMARY: Verify README's Python line matches pyproject.toml and .python-version.
    @pytest.mark.unit
    def test_readme_states_the_declared_versions(self) -> None:
        # **LOGIC_STEP**: README is prose, not generated, so nothing else would notice a version
        # bump leaving it stale.
        pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        minimum = pyproject.split('requires-python = ">=', 1)[1].split('"', 1)[0]
        toolchain = (_REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip()
        readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")

        assert f"Python {minimum}+" in readme
        assert f"`{toolchain}`" in readme
        assert f"`{minimum}`" in readme


# ATTRIBUTE: _README_ROW (re.Pattern[str])
# SUMMARY: A three-cell README table row whose first and last cells are single backticked tokens.
_README_ROW = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|[^|]*\|\s*`([^`|]*)`\s*\|\s*$")


# CLASS: tests.application.test_env_sample_matches_code.TestReadmeDocumentsRealDefaults
# SUMMARY: Verify README's "Default" column states the value the code actually declares.
class TestReadmeDocumentsRealDefaults:
    # FUNCTION: test_readme_default_column_matches_settings
    # SUMMARY: Verify every documented default equals the settings field's default.
    @pytest.mark.unit
    def test_readme_default_column_matches_settings(self) -> None:
        # **LOGIC_STEP**: README carried `AGENT_MAX_TOKENS | 2000` for three months against a
        # code default of 4096 — including through a docs pass that fixed the same number in
        # .env.sample and missed it here, because .env.sample had a gate and README did not.
        # Rows whose variable is not a settings field, or whose default cell is not a single
        # token, are skipped: this checks the claims it can check rather than demanding a shape.
        defaults = _code_defaults()
        readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")

        mismatches = []
        for line in readme.splitlines():
            match = _README_ROW.match(line)
            if match is None:
                continue
            key, documented = match.group(1), match.group(2)
            if key not in defaults:
                continue
            expected = _comparable(defaults[key])
            if _comparable(documented) != expected:
                mismatches.append(f"{key}: readme={documented!r} code_default={expected!r}")

        assert mismatches == []


# CLASS: tests.application.test_env_sample_matches_code.TestReadmeListsEverySkill
# SUMMARY: Verify the README skills table does not silently omit a shipped skill.
class TestReadmeListsEverySkill:
    # FUNCTION: test_every_skill_directory_appears_in_readme
    # SUMMARY: Verify each directory under .agents/skills/ is named in README.
    @pytest.mark.unit
    def test_every_skill_directory_appears_in_readme(self) -> None:
        # **LOGIC_STEP**: The table listed seven of nine after add-vertical and one now-removed skill were
        # added — the two newest and least known, which is exactly when a table is worth reading.
        skills = sorted(p.name for p in (_REPO_ROOT / ".agents" / "skills").iterdir() if p.is_dir())
        readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")

        missing = [name for name in skills if f"`{name}`" not in readme]

        assert missing == []
