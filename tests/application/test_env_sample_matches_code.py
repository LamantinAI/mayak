# FILE: tests/application/test_env_sample_matches_code.py
# SUMMARY: Guard against .env.sample drifting from the defaults declared in the settings models.
# NOTE: This is the gate that was missing when .env.sample shipped AGENT_MAX_TOKENS=2000 against
# a code default of 4096 — the sample wins at runtime, so every project created from the template
# started with a value the code's own comment described as a bug.

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from pydantic_settings import BaseSettings

from project.core.config_runtime import GUARDS_RELAXED_BY_DEBUG, Settings
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


# FUNCTION: _debug_gated_guards_in_validate_runtime
# SUMMARY: Count the reads of `self.project.debug` inside Settings.validate_runtime.
# OUTPUT: (int): How many startup guards the flag switches off — one read per guard.
# NOTE: Every read, in whatever expression. The first version counted only `not
# self.project.debug`, and a guard written as `self.project.debug is False` — the same gate,
# one operator away — was invisible to it, so the tuple, README and the startup event all
# stayed at three while a fourth guard went undocumented. Counted on the AST, not the text: a
# comment in that method quotes the expression.
def _debug_gated_guards_in_validate_runtime() -> int:
    tree = ast.parse(textwrap.dedent(inspect.getsource(Settings.validate_runtime)))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and ast.unparse(node) == "self.project.debug"
    )


# CLASS: tests.application.test_env_sample_matches_code.TestDebugFlagIsDocumentedAsItself
# SUMMARY: Verify README and both env samples credit APP_DEBUG with the guards it relaxes, and no more.
# NOTE: README's row said "Starlette's own error page" — an effect composition_root.py removed when
# it hard-wired FastAPI(debug=False) — and .env.sample named two of the three guards the flag
# switches off. Measured on 2026-09-02. The list is read out of config_runtime.py, and the number
# of debug-gated conditions out of validate_runtime's own AST, so a fourth guard or a removed one
# fails here instead of quietly leaving the prose behind again.
class TestDebugFlagIsDocumentedAsItself:
    # FUNCTION: test_the_tuple_counts_every_guard_the_flag_gates
    # SUMMARY: Verify GUARDS_RELAXED_BY_DEBUG has one entry per `not self.project.debug` condition.
    @pytest.mark.unit
    def test_the_tuple_counts_every_guard_the_flag_gates(self) -> None:
        assert _debug_gated_guards_in_validate_runtime() == len(GUARDS_RELAXED_BY_DEBUG)

    # FUNCTION: test_readme_names_every_relaxed_guard_and_no_dead_effect
    # SUMMARY: Verify the README row lists the three variables and no longer credits the error page.
    @pytest.mark.unit
    def test_readme_names_every_relaxed_guard_and_no_dead_effect(self) -> None:
        readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
        row = next(line for line in readme.splitlines() if line.startswith("| `APP_DEBUG`"))

        for variable in GUARDS_RELAXED_BY_DEBUG:
            assert f"`{variable}`" in row
        assert "Starlette's own error page" not in row

    # FUNCTION: test_every_sample_comment_names_every_relaxed_guard
    # SUMMARY: Verify the comment above APP_DEBUG in each sample lists the variables and drops the dead claim.
    @pytest.mark.unit
    @pytest.mark.parametrize("sample", [".env.sample", "tests/functional/.env.sample"])
    def test_every_sample_comment_names_every_relaxed_guard(self, sample: str) -> None:
        comment = _sample_comment_above(_REPO_ROOT / sample, "APP_DEBUG")

        for variable in GUARDS_RELAXED_BY_DEBUG:
            assert variable in comment
        # **LOGIC_STEP**: The two sentences that described an effect the flag no longer has.
        assert "return its own traceback" not in comment
        assert "intercepts unhandled exceptions" not in comment


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


# ATTRIBUTE: _NUMBER (re.Pattern[str])
# SUMMARY: Any decimal number in a comment line.
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


# FUNCTION: _rejecting_bounds
# SUMMARY: The numeric bounds of one settings field, minus the ones a reader would never test.
# OUTPUT: (dict[str, float]): Constraint name -> value, for every ge/gt/le/lt that is not zero.
# NOTE: Zero is excluded on purpose. `gt=0` on a token count or a pool size says "a positive
# number", which is what a reader already assumes; a rule demanding that every such field spell
# out "minimum 1" would fire on documentation that is already correct, and a rule that fires on
# correct work teaches people to silence it. A non-zero bound is different: nothing about
# OPENAI_COMPATIBLE_REQUEST_TIMEOUT suggests that 1 is refused, and the refusal happens at
# startup, far from the line that chose the value.
def _rejecting_bounds(field: Any) -> dict[str, float]:
    bounds: dict[str, float] = {}
    for constraint in field.metadata:
        for name in ("ge", "gt", "le", "lt"):
            value = getattr(constraint, name, None)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if value != 0:
                bounds[name] = float(value)
    return bounds


# FUNCTION: _documented_bounds
# SUMMARY: Every number written in the .env.sample comment block above one variable.
def _documented_bounds(path: Path, key: str) -> set[float]:
    return {float(match.group()) for match in _NUMBER.finditer(_sample_comment_above(path, key))}


# CLASS: tests.application.test_env_sample_matches_code.TestASampleSaysWhichValuesAreRefused
# SUMMARY: Verify a bound that rejects plausible values is named where the value is chosen.
# NOTE: OPENAI_COMPATIBLE_REQUEST_TIMEOUT carries `ge=5` and the sample said only "in seconds", so
# a 2-second timeout — an ordinary thing to want while testing a retry path — was accepted by the
# file, refused by Pydantic, and reported as a validation error during startup with no pointer
# back to the sample that suggested it. The bound is read out of the model here, so raising or
# lowering it in the code turns this test red until the sample agrees, rather than leaving a
# second copy of the number to drift.
class TestASampleSaysWhichValuesAreRefused:
    # FUNCTION: test_every_non_zero_bound_is_named_in_the_sample_comment
    # SUMMARY: Verify each ge/gt/le/lt that is not zero appears above its variable in .env.sample.
    @pytest.mark.unit
    def test_every_non_zero_bound_is_named_in_the_sample_comment(self) -> None:
        sample = _REPO_ROOT / ".env.sample"
        present = _sample_assignments(sample)
        undocumented: list[str] = []

        for model in _SETTINGS_MODELS:
            prefix = _env_prefix(model)
            for field_name, field in model.model_fields.items():
                key = _env_key(prefix, field_name, field)
                if key not in present:
                    continue
                bounds = _rejecting_bounds(field)
                if not bounds:
                    continue
                written = _documented_bounds(sample, key)
                for name, value in bounds.items():
                    if value not in written:
                        undocumented.append(f"{key}: {name}={value:g}")

        assert not undocumented, "these bounds refuse values the sample invites: " + ", ".join(
            sorted(undocumented)
        )


# ATTRIBUTE: _NUMBER_WORDS (dict[str, int])
# SUMMARY: Spelled-out counts README uses in prose, as far as any count here plausibly reaches.
_NUMBER_WORDS = {
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


# CLASS: tests.application.test_env_sample_matches_code.TestReadmeCountsTheValidatorsThatExist
# SUMMARY: Verify the validator count README states matches the number of validators on disk.
# NOTE: README says it twice, in prose, spelled out. Nothing regenerates README, so adding or
# removing a validator left both sentences confidently wrong — and this is the number a reader
# uses to decide whether the template's tax is worth paying, which the section above it now asks
# them to weigh.
class TestReadmeCountsTheValidatorsThatExist:
    # FUNCTION: test_every_stated_validator_count_matches_the_scripts_directory
    # SUMMARY: Verify each "<number> validators" in README equals the count of scripts/validate_*.py.
    @pytest.mark.unit
    def test_every_stated_validator_count_matches_the_scripts_directory(self) -> None:
        actual = len(list((_REPO_ROOT / "scripts").glob("validate_*.py")))
        readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
        counted: list[str] = []
        wrong: list[str] = []
        for match in re.finditer(r"\b([A-Za-z0-9-]+) validators\b", readme):
            word = match.group(1).lower()
            # **LOGIC_STEP**: Only a word that IS a count is checked. "the validators", "custom
            # validators" and "community validators" claim no number, and failing on them would
            # make an accurate sentence a red gate — the way to get a rule deleted. A count
            # written in a spelling this map does not hold is invisible instead, which the
            # assertion below catches as long as it is the only one in the file.
            value = int(word) if word.isdigit() else _NUMBER_WORDS.get(word)
            if value is None:
                continue
            counted.append(word)
            if value != actual:
                wrong.append(word)

        assert counted, (
            "README states no validator count this test can read. Either it stopped naming one, "
            f"or it names one in a spelling missing from _NUMBER_WORDS; scripts/ holds {actual}."
        )
        assert wrong == [], f"README says {wrong} validators; scripts/ holds {actual}."
