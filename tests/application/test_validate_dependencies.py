# FILE: tests/application/test_validate_dependencies.py
# SUMMARY: Unit tests for the validator that ties runtime imports to declared dependencies.

from __future__ import annotations

import re
from pathlib import Path
from subprocess import run

import pytest

from scripts.validate_dependencies import (
    collect_dependency_issues,
    declared_distributions,
    get_dependencies_rule_playbook,
    normalize_distribution_name,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


# FUNCTION: _tracked_files
# SUMMARY: List the repository's tracked files — exactly what a clone of the template gets.
# OUTPUT: (list[str]): Repository-relative paths from the git index.
def _tracked_files() -> list[str]:
    result = run(
        ["git", "ls-files", "-z"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [name for name in result.stdout.split("\0") if name]


# CLASS: tests.application.test_validate_dependencies.TestDeclaredDistributions
# SUMMARY: Verify the declared set is read faithfully, including extras.
class TestDeclaredDistributions:
    # FUNCTION: test_direct_dependency_is_declared
    # SUMMARY: Verify a plain pinned dependency is recognized.
    @pytest.mark.unit
    def test_direct_dependency_is_declared(self) -> None:
        assert "fastapi" in declared_distributions(_REPO_ROOT)

    # FUNCTION: test_extra_expands_to_its_own_distributions
    # SUMMARY: Verify psycopg[pool] declares psycopg-pool, which the runtime imports directly.
    @pytest.mark.unit
    def test_extra_expands_to_its_own_distributions(self) -> None:
        # **LOGIC_STEP**: Without extras expansion the validator reports the entire runtime
        # database layer as undeclared, since project/ imports psycopg_pool, not psycopg.
        assert "psycopg-pool" in declared_distributions(_REPO_ROOT)

    # FUNCTION: test_dependency_group_is_included
    # SUMMARY: Verify [dependency-groups] counts as a declaration, not only [project].dependencies.
    @pytest.mark.unit
    def test_dependency_group_is_included(self) -> None:
        # **LOGIC_STEP**: pytest is named in the dev group and nowhere else. It used to be listed
        # twice — dev group and a `test` extra that duplicated it line for line — so dropping the
        # duplicate would have made the whole test toolchain read as undeclared.
        assert "pytest" in declared_distributions(_REPO_ROOT)

    # FUNCTION: test_names_are_normalized
    # SUMMARY: Verify PEP 503 normalization so spellings compare equal.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("psycopg_pool", "psycopg-pool"),
            ("Python-Dotenv", "python-dotenv"),
            ("langchain.core", "langchain-core"),
        ],
    )
    def test_names_are_normalized(self, raw: str, expected: str) -> None:
        assert normalize_distribution_name(raw) == expected


# CLASS: tests.application.test_validate_dependencies.TestRepositoryIsClean
# SUMMARY: Verify runtime code imports nothing that pyproject.toml fails to declare.
class TestRepositoryIsClean:
    # FUNCTION: test_no_undeclared_runtime_imports
    # SUMMARY: Verify every third-party import under project/ maps to a declared dependency.
    @pytest.mark.unit
    def test_no_undeclared_runtime_imports(self) -> None:
        issues = collect_dependency_issues(_REPO_ROOT)

        assert [f"{issue.path.name}:{issue.line} {issue.message}" for issue in issues] == []

    # FUNCTION: test_every_rule_has_a_playbook
    # SUMMARY: Verify both rule identifiers carry remediation guidance.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "rule_id",
        ["dependencies.undeclared_import", "dependencies.unresolvable_import"],
    )
    def test_every_rule_has_a_playbook(self, rule_id: str) -> None:
        playbook = get_dependencies_rule_playbook(rule_id)

        assert playbook is not None
        assert playbook["stop_widening_condition"]


# CLASS: tests.application.test_validate_dependencies.TestUndeclaredImportIsReported
# SUMMARY: Verify the validator actually fires on a synthetic repository.
class TestUndeclaredImportIsReported:
    # FUNCTION: test_third_party_import_without_declaration_is_reported
    # SUMMARY: Verify an installed-but-undeclared package is caught.
    @pytest.mark.unit
    def test_third_party_import_without_declaration_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "probe"\ndependencies = ["fastapi"]\n',
            encoding="utf-8",
        )
        runtime = tmp_path / "project" / "core"
        runtime.mkdir(parents=True)
        (runtime / "module.py").write_text("import orjson\n", encoding="utf-8")

        issues = collect_dependency_issues(tmp_path)

        assert [issue.rule_id for issue in issues] == ["dependencies.undeclared_import"]

    # FUNCTION: test_a_dynamically_imported_distribution_is_reported
    # SUMMARY: Verify importlib.import_module counts as an import for the declared-dependency check.
    # NOTE: The same one-line bypass this validator shared with validate_architecture.py: only
    # ast.Import/ast.ImportFrom were collected, so a package pulled in through
    # `importlib.import_module` was undeclared and unreported at once.
    @pytest.mark.unit
    def test_a_dynamically_imported_distribution_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "probe"\ndependencies = ["fastapi"]\n',
            encoding="utf-8",
        )
        runtime = tmp_path / "project" / "core"
        runtime.mkdir(parents=True)
        (runtime / "module.py").write_text(
            'import importlib\n\n_json = importlib.import_module("orjson")\n',
            encoding="utf-8",
        )

        issues = collect_dependency_issues(tmp_path)

        assert [issue.rule_id for issue in issues] == ["dependencies.undeclared_import"]
        assert "orjson" in issues[0].message

    # FUNCTION: test_a_method_named_like_an_import_is_not_reported
    # SUMMARY: Verify an unrelated object's `import_module` method is not read as an import.
    @pytest.mark.unit
    def test_a_method_named_like_an_import_is_not_reported(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "probe"\ndependencies = []\n',
            encoding="utf-8",
        )
        runtime = tmp_path / "project" / "core"
        runtime.mkdir(parents=True)
        (runtime / "module.py").write_text(
            "class Registry:\n"
            "    def import_module(self, name: str) -> object:\n"
            "        return object()\n"
            "\n"
            "\n"
            "registry = Registry()\n"
            '_loaded = registry.import_module("orjson")\n',
            encoding="utf-8",
        )

        assert collect_dependency_issues(tmp_path) == []

    # FUNCTION: test_stdlib_and_first_party_imports_are_ignored
    # SUMMARY: Verify the validator does not flag the standard library or repository packages.
    @pytest.mark.unit
    def test_stdlib_and_first_party_imports_are_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "probe"\ndependencies = []\n',
            encoding="utf-8",
        )
        runtime = tmp_path / "project" / "core"
        runtime.mkdir(parents=True)
        (runtime / "module.py").write_text(
            "import json\nimport sys\nfrom project.core import other\nfrom . import sibling\n",
            encoding="utf-8",
        )

        assert collect_dependency_issues(tmp_path) == []


# CLASS: tests.application.test_validate_dependencies.TestShippedCommandsMatchPyproject
# SUMMARY: Guard the commands the template tells people to run against what pyproject actually
#          declares. This exists because removing the duplicate `test` extra left
#          `uv sync --frozen --extra test` behind in dev_setup.sh — the first script a new
#          project runs — and every gate stayed green, because no gate executes that script.
class TestShippedCommandsMatchPyproject:
    # FUNCTION: test_every_extra_a_shipped_command_asks_for_is_declared
    # SUMMARY: Verify no tracked file asks uv for an extra pyproject does not define.
    @pytest.mark.unit
    def test_every_extra_a_shipped_command_asks_for_is_declared(self) -> None:
        # **LOGIC_STEP**: The extras table is optional in pyproject; absent means "no extras",
        # and `uv sync --extra anything` then fails outright rather than degrading quietly.
        pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        declared: set[str] = set()
        in_extras = False
        for raw in pyproject.splitlines():
            line = raw.strip()
            if line.startswith("[") and line.endswith("]") and "=" not in line:
                in_extras = line == "[project.optional-dependencies]"
                continue
            match = re.match(r"^([A-Za-z0-9_-]+)\s*=\s*\[", line)
            if in_extras and match:
                declared.add(match.group(1))

        this_file = Path(__file__).resolve()
        offenders: list[str] = []
        for name in _tracked_files():
            path = _REPO_ROOT / name
            if path.resolve() == this_file or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            # **LOGIC_STEP**: Comment lines are skipped, because the explanation of why an extra
            # was removed necessarily spells the flag out — the same trap bandit's `nosec` marker
            # sets, where prose about a suppression suppresses. Only executable lines count.
            for raw in text.splitlines():
                if raw.lstrip().startswith("#"):
                    continue
                for requested in re.findall(r"--extra[= ]([A-Za-z0-9_-]+)", raw):
                    if requested not in declared:
                        offenders.append(f"{name}: --extra {requested}")

        assert not offenders, "command asks for an undeclared extra: " + ", ".join(offenders)


# CLASS: tests.application.test_validate_dependencies.TestFunctionalRunnerVersionsAreAligned
# SUMMARY: Guard the second requirements file against drifting from pyproject.
# NOTE: tests/functional/requirements.txt installs the runner inside the Docker test image, so it
# is the one dependency list `uv lock` does not govern. It had drifted a major version behind on
# both pytest and pytest-asyncio — the same suite could then behave differently depending on which
# runner invoked it, and nothing said so.
class TestFunctionalRunnerVersionsAreAligned:
    # FUNCTION: test_shared_packages_are_pinned_to_the_same_version
    # SUMMARY: Verify every package named in both files carries the same pin.
    @pytest.mark.unit
    def test_shared_packages_are_pinned_to_the_same_version(self) -> None:
        def pins(lines: list[str]) -> dict[str, str]:
            found: dict[str, str] = {}
            for line in lines:
                match = re.match(
                    r'^\s*"?([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==([0-9][^"\s]*)"?,?\s*$', line
                )
                if match:
                    found[normalize_distribution_name(match.group(1))] = match.group(2)
            return found

        pyproject_pins = pins(
            (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        )
        runner_pins = pins(
            (_REPO_ROOT / "tests" / "functional" / "requirements.txt")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        shared = set(pyproject_pins) & set(runner_pins)

        # **LOGIC_STEP**: A package the runner needs and the project does not (backoff) is fine —
        # only the overlap has to agree, because only the overlap can disagree.
        assert shared, "the two dependency lists no longer overlap — check the parser, not the pins"
        disagreements = {
            name: (pyproject_pins[name], runner_pins[name])
            for name in sorted(shared)
            if pyproject_pins[name] != runner_pins[name]
        }

        assert disagreements == {}
