# FILE: tests/application/test_skill_texts_match_reality.py
# SUMMARY: Guard the factual claims the skills make about this repository, so prose cannot drift
# away from the code it instructs an agent to change.
# NOTE: Every claim covered here was measured wrong once. The skill said one table ledger and
# there are two — the unnamed one is what went red.
# The skill's completeness sweep was case-sensitive and underscore-only, so it found neither
# `ReferenceTaskORM` nor `/reference-tasks/`. And a checkout inherited memory tags nobody had told
# the reader about. Prose fails no gate on its own; these are the gate.
#
# What each test can and cannot promise, because the distinction was overstated once already:
#
# - Three tests read the skill text and fail when it stops naming something real —
#   `test_every_exact_table_set_assertion_is_named_in_the_skill`,
#   `test_the_skill_prints_a_case_insensitive_separator_agnostic_sweep`,
#   `test_step_seven_names_every_file_carrying_an_inherited_tag`. Even these check that a literal
#   is PRESENT, not that the surrounding sentence is true; a substring buried in unrelated prose
#   satisfies them. They catch a skill that has gone silent about a fact, not one that lies about it.
# - The rest never open a SKILL.md. They pin the repository facts the prose rests on — that both
#   ledgers still pin an exact set, that the detector recognises the spellings it claims to, that
#   the narrow grep really does miss files. Replacing every skill with nonsense leaves them green,
#   and that is correct: their subject is the code, not the sentence about it.
#
# No test here can verify that a sentence means what it says. That is what a reader is for.

from __future__ import annotations

import ast
import re
from pathlib import Path
from subprocess import run

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADD_VERTICAL = _REPO_ROOT / ".agents" / "skills" / "add-vertical" / "SKILL.md"
_INITIALIZE_PROJECT = _REPO_ROOT / ".agents" / "skills" / "initialize-project" / "SKILL.md"

# ATTRIBUTE: _KERNEL_PYTHON_ROOTS (tuple[str, ...])
# SUMMARY: Every directory of kernel Python, used to prove the memory tag has not come back.
_KERNEL_PYTHON_ROOTS = ("project", "scripts", "ai_context", "ai_query", "tests")

# ATTRIBUTE: _VERTICAL_ANY_SPELLING (re.Pattern[str])
# SUMMARY: The reference vertical's name in any case and with either separator.
_VERTICAL_ANY_SPELLING = re.compile(r"reference[-_]?task", re.IGNORECASE)

# ATTRIBUTE: _VERTICAL_NAIVE_SPELLING (re.Pattern[str])
# SUMMARY: The pattern the skill used to print — case-sensitive, underscore only.
_VERTICAL_NAIVE_SPELLING = re.compile(r"reference_task")


# FUNCTION: _tracked_text_files
# SUMMARY: Every tracked file that can be read as text, which is what any sweep sees.
# OUTPUT: (list[Path]): Absolute paths.
def _tracked_text_files() -> list[Path]:
    listed = run(
        ["git", "ls-files", "-z"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [_REPO_ROOT / name for name in listed.stdout.split("\0") if name]


# FUNCTION: _files_matching
# SUMMARY: Repo-relative paths of tracked files whose text matches a pattern.
# INPUT: pattern (re.Pattern[str]): Compiled pattern to search for.
# OUTPUT: (set[str]): Repo-relative paths.
def _files_matching(pattern: re.Pattern[str]) -> set[str]:
    found: set[str] = set()
    for path in _tracked_text_files():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pattern.search(text):
            found.add(str(path.relative_to(_REPO_ROOT)))
    return found


# ATTRIBUTE: _LEDGER_SCAN_DIRECTORIES (tuple[str, ...])
# SUMMARY: Every directory a table ledger could be written in.
# `tests/functional` is included here even though the narrow validation loop never runs it: this
# scan is looking for a ledger the skill must name, and one written there would still fail a gate.
_LEDGER_SCAN_DIRECTORIES = ("tests", "scripts", "ai_context", "ai_query")

# ATTRIBUTE: _PYTHON_BLOCK (re.Pattern[str])
# SUMMARY: A fenced python block in a skill, captured without its fences.
_PYTHON_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)


# FUNCTION: _classes_defined_under_project
# SUMMARY: Map every class name the application package defines to its dotted module path.
# OUTPUT: (dict[str, str]): Class name to module, e.g. ReferenceTaskService -> project.application....
def _classes_defined_under_project() -> dict[str, str]:
    # **LOGIC_STEP**: Read with ast rather than importing everything. Importing the whole package to
    # find a name would run every module's import side effects for a documentation check.
    found: dict[str, str] = {}
    for path in sorted((_REPO_ROOT / "project").rglob("*.py")):
        module = ".".join(path.relative_to(_REPO_ROOT).with_suffix("").parts)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                found.setdefault(node.name, module)
    return found


# FUNCTION: _call_shape
# SUMMARY: Reduce a call to what a signature can be checked against: how many positionals, which keywords.
# OUTPUT: (tuple[int, list[str]] | None): Counts, or None when the call is not checkable.
def _call_shape(node: ast.Call) -> tuple[int, list[str]] | None:
    # **LOGIC_STEP**: `f(*args)` and `f(**kwargs)` hide the real shape from a static reader, so the
    # call is skipped rather than guessed at. A documentation example that needs them is rare, and
    # a false failure here would be worse than a missed one.
    if any(isinstance(argument, ast.Starred) for argument in node.args):
        return None
    if any(keyword.arg is None for keyword in node.keywords):
        return None
    return len(node.args), [keyword.arg for keyword in node.keywords if keyword.arg]


# FUNCTION: signature_mismatches_in_skill_text
# SUMMARY: Report calls in a skill's python examples that the real code would reject.
# INPUT: text (str): Full markdown text of a skill.
# INPUT: classes (dict[str, type] | None): Class map to check against; the application package's
#        own classes when omitted. Tests pass their own so they do not depend on a vertical that
#        every project is told to delete.
# OUTPUT: (list[str]): One human-readable line per mismatch; empty when every example would run.
# NOTE: Measured on 10.08.2026. .agents/skills/add-vertical shipped `service.list_tasks(limit=7)` while the
# method requires `status`, called `repository.list` where the port declares `list_by_status`, and
# `service.create` where the service defines `create_task`. The example had never been executed —
# not "had rotted". Everything else in this module checks that the prose NAMES a real thing; this
# checks that the code it shows would actually run.
def signature_mismatches_in_skill_text(
    text: str, classes: dict[str, type] | None = None
) -> list[str]:
    from importlib import import_module

    resolved: dict[str, type] = dict(classes or {})
    known = {} if classes is not None else _classes_defined_under_project()
    problems: list[str] = []

    for block in _PYTHON_BLOCK.findall(text):
        try:
            tree = ast.parse(block)
        except SyntaxError:
            # **LOGIC_STEP**: Examples are fragments — a method body without its class parses fine,
            # but a partial statement does not. An unparseable block is a documentation choice, not
            # a signature error, and belongs to a reader rather than to this check.
            continue

        bound: dict[str, type] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target, value = node.targets[0], node.value
            if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
                continue
            if not isinstance(value.func, ast.Name):
                continue
            if value.func.id in resolved:
                cls = resolved[value.func.id]
            elif value.func.id in known:
                try:
                    cls = getattr(import_module(known[value.func.id]), value.func.id)
                except (ImportError, AttributeError):
                    continue
            else:
                continue
            bound[target.id] = cls
            problems.extend(_mismatch(cls, "__init__", value, f"{value.func.id}(...)"))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if not isinstance(owner, ast.Name) or owner.id not in bound:
                continue
            cls = bound[owner.id]
            problems.extend(
                _mismatch(cls, node.func.attr, node, f"{owner.id}.{node.func.attr}(...)")
            )

    return problems


# FUNCTION: _mismatch
# SUMMARY: Compare one call against one method of a real class.
# OUTPUT: (list[str]): Empty when the call would bind, one line when it would not.
def _mismatch(cls: type, method_name: str, node: ast.Call, rendered: str) -> list[str]:
    from inspect import signature

    method = getattr(cls, method_name, None)
    if method is None:
        return [f"{rendered}: {cls.__name__} has no attribute {method_name!r}"]
    if not callable(method):
        return []
    shape = _call_shape(node)
    if shape is None:
        return []
    positional, keywords = shape
    try:
        parameters = signature(method)
    except (TypeError, ValueError):
        return []
    # **LOGIC_STEP**: The signature is read off the class, so `self` is still in it. A stand-in is
    # supplied for the instance the example calls the method on.
    takes_self = next(iter(parameters.parameters), None) in {"self", "cls"}
    arguments: list[object] = [None] * (positional + (1 if takes_self else 0))
    try:
        parameters.bind(*arguments, **{name: None for name in keywords})
    except TypeError as error:
        return [f"{rendered}: {error}"]
    return []


# FUNCTION: _exact_table_set_assertions
# SUMMARY: Find tests that compare the ORM metadata's table names against a fixed collection.
# INPUT: path (Path): Test module to parse.
# OUTPUT: (list[str]): Names of the test functions that make such an assertion.
def _exact_table_set_assertions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for statement in ast.walk(node):
            if not isinstance(statement, ast.Compare):
                continue
            if not any(isinstance(operator, ast.Eq) for operator in statement.ops):
                continue
            # **LOGIC_STEP**: An exact-set ledger is an equality against metadata.tables, whichever
            # side each operand is on. The first version of this detector also demanded a literal
            # `{` in the unparsed text, which made three idiomatic spellings invisible:
            # `== EXPECTED_TABLES` (a named constant), `== set([...])`, and
            # `sorted(...) == [...]`. Equality alone is the right signal — a membership check
            # (`in metadata.tables`) is deliberately not one, because it stays green when a table
            # is added, which is the case these ledgers exist to catch.
            if "metadata.tables" in ast.unparse(statement):
                names.append(node.name)
                break
    return names


# CLASS: tests.application.test_skill_texts_match_reality.TestAddVerticalNamesEveryTableLedger
# SUMMARY: Verify the skill names every test a new table has to be registered in.
class TestAddVerticalNamesEveryTableLedger:
    # FUNCTION: test_every_exact_table_set_assertion_is_named_in_the_skill
    # SUMMARY: Verify no ledger exists that the skill fails to mention.
    @pytest.mark.unit
    def test_every_exact_table_set_assertion_is_named_in_the_skill(self) -> None:
        # **LOGIC_STEP**: The skill promises to prevent "the gate fails on a file you never
        # opened". It listed one ledger; the second one, TestORMRegistry, is the one that
        # actually goes red the first time a new table is added.
        skill_text = _ADD_VERTICAL.read_text(encoding="utf-8")
        unnamed: list[str] = []

        # **LOGIC_STEP**: The whole tree, not two directories. A ledger under tests/functional or
        # in a script would still turn a gate red, and the first version of this scan could not
        # have seen it — the failure mode this test exists to prevent is precisely "the gate fails
        # in a file nobody told you about".
        for directory in _LEDGER_SCAN_DIRECTORIES:
            for path in sorted((_REPO_ROOT / directory).rglob("*.py")):
                if not _exact_table_set_assertions(path):
                    continue
                if path.name not in skill_text:
                    unnamed.append(str(path.relative_to(_REPO_ROOT)))

        assert unnamed == [], (
            "these tests pin the exact table set but .agents/skills/add-vertical never names them: "
            + ", ".join(unnamed)
        )

    # FUNCTION: test_the_detector_recognises_every_idiomatic_ledger_spelling
    # SUMMARY: Verify the scan is not defeated by a named constant, a set() call, or a sorted list.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "assertion",
        [
            'assert set(Base.metadata.tables.keys()) == {"a"}',
            "assert set(Base.metadata.tables.keys()) == EXPECTED_TABLES",
            'assert Base.metadata.tables.keys() == set(["a"])',
            'assert sorted(Base.metadata.tables.keys()) == ["a"]',
            'assert {"a"} == set(Base.metadata.tables)',
        ],
    )
    def test_the_detector_recognises_every_idiomatic_ledger_spelling(
        self, assertion: str, tmp_path: Path
    ) -> None:
        # **LOGIC_STEP**: The detector once required a literal `{` in the unparsed text, so three
        # of these five spellings were invisible and a third ledger written that way would have
        # slipped past the guard above without anyone noticing.
        module = tmp_path / "test_probe.py"
        module.write_text(f"def test_ledger() -> None:\n    {assertion}\n", encoding="utf-8")

        assert _exact_table_set_assertions(module) == ["test_ledger"]

    # FUNCTION: test_the_detector_ignores_a_membership_check
    # SUMMARY: Verify a non-ledger assertion is not mistaken for one.
    @pytest.mark.unit
    def test_the_detector_ignores_a_membership_check(self, tmp_path: Path) -> None:
        # **LOGIC_STEP**: `in metadata.tables` stays green when a table is added, so it is not a
        # ledger and naming it in the skill would be wrong. Dropping the `{` requirement must not
        # widen the detector this far.
        module = tmp_path / "test_probe.py"
        module.write_text(
            'def test_membership() -> None:\n    assert "a" in Base.metadata.tables\n',
            encoding="utf-8",
        )

        assert _exact_table_set_assertions(module) == []

    # FUNCTION: test_the_one_ledger_still_pins_an_exact_set
    # SUMMARY: Verify the single ledger the skill names has not quietly become a membership check.
    # NOTE: This was parametrised over two paths until the duplicate in
    # tests/infrastructure/test_persistence_models.py was removed. One ledger is the point: the
    # second copy is what made a project's first table go red twice in a row.
    @pytest.mark.unit
    def test_the_one_ledger_still_pins_an_exact_set(self) -> None:
        assert _exact_table_set_assertions(
            _REPO_ROOT / "tests/application/test_validate_migrations.py"
        )

    # FUNCTION: test_no_second_ledger_has_appeared
    # SUMMARY: Verify the duplicate has not grown back somewhere else in the tree.
    @pytest.mark.unit
    def test_no_second_ledger_has_appeared(self) -> None:
        # **LOGIC_STEP**: The scan below is the same one the skill-naming test uses, so a ledger
        # added in any directory is seen. Removing the duplicate is only worth doing if a third
        # one cannot quietly take its place.
        carriers = [
            str(path.relative_to(_REPO_ROOT))
            for directory in _LEDGER_SCAN_DIRECTORIES
            for path in sorted((_REPO_ROOT / directory).rglob("*.py"))
            if _exact_table_set_assertions(path)
        ]

        # **LOGIC_STEP**: The count, not the path. A project is free to keep its ledger wherever it
        # likes — what cost two projects a red gate twice each was having the same sentence in two
        # files at once, and that is what this pins.
        assert len(carriers) == 1, (
            "the exact table set must be pinned in exactly one place; found "
            f"{len(carriers)}: " + ", ".join(carriers)
        )


# CLASS: tests.application.test_skill_texts_match_reality._ProbeService
# SUMMARY: Stand-in for a service, so the checker's own tests survive deleting the reference vertical.
# NOTE: The first version of these tests called ReferenceTaskService by name. Delete the reference
# vertical — which the skill tells every reader to do — and three kernel tests go red in a file the
# developer never opened. That is the exact failure class this module exists to prevent,
# reintroduced by the module itself.
class _ProbeService:
    # FUNCTION: __init__
    # SUMMARY: Accept a collaborator, the way a real service does.
    def __init__(self, repository: object) -> None:
        self._repository = repository

    # FUNCTION: list_items
    # SUMMARY: Require a status the caller must name, and accept an optional limit.
    def list_items(self, status: str, limit: int = 50) -> list[str]:
        return [status] * limit


# CLASS: tests.application.test_skill_texts_match_reality.TestSkillExamplesWouldRun
# SUMMARY: Verify every method call in a skill's python examples matches the real signature.
class TestSkillExamplesWouldRun:
    # FUNCTION: test_every_shipped_skill_example_matches_the_real_signatures
    # SUMMARY: Verify no skill shows a call the code under it would reject.
    @pytest.mark.unit
    def test_every_shipped_skill_example_matches_the_real_signatures(self) -> None:
        problems: list[str] = []
        for path in sorted((_REPO_ROOT / ".agents" / "skills").rglob("SKILL.md")):
            for line in signature_mismatches_in_skill_text(path.read_text(encoding="utf-8")):
                problems.append(f"{path.relative_to(_REPO_ROOT)}: {line}")

        assert problems == [], "skill examples that would not run:\n" + "\n".join(problems)

    # FUNCTION: test_a_missing_required_argument_is_reported
    # SUMMARY: Verify the exact defect measured on 10.08.2026 is caught.
    @pytest.mark.unit
    def test_a_missing_required_argument_is_reported(self) -> None:
        # **LOGIC_STEP**: list_items(self, status, limit=50). The example below omits `status` —
        # the shape of what shipped in .agents/skills/add-vertical and what nothing caught.
        text = "```python\nservice = ProbeService(repository)\nservice.list_items(limit=7)\n```\n"

        problems = signature_mismatches_in_skill_text(text, {"ProbeService": _ProbeService})

        assert len(problems) == 1
        assert "list_items" in problems[0]
        assert "status" in problems[0]

    # FUNCTION: test_a_method_that_does_not_exist_is_reported
    # SUMMARY: Verify a renamed method in an example is caught.
    @pytest.mark.unit
    def test_a_method_that_does_not_exist_is_reported(self) -> None:
        text = "```python\nservice = ProbeService(repository)\nservice.create(title='x')\n```\n"

        problems = signature_mismatches_in_skill_text(text, {"ProbeService": _ProbeService})

        assert len(problems) == 1
        assert "no attribute 'create'" in problems[0]

    # FUNCTION: test_a_correct_example_is_accepted
    # SUMMARY: Verify a call that would really run raises no complaint.
    @pytest.mark.unit
    def test_a_correct_example_is_accepted(self) -> None:
        text = (
            "```python\n"
            "service = ProbeService(repository)\n"
            'service.list_items(status="pending", limit=7)\n'
            "```\n"
        )

        assert signature_mismatches_in_skill_text(text, {"ProbeService": _ProbeService}) == []

    # FUNCTION: test_a_name_the_checker_does_not_know_is_skipped
    # SUMMARY: Verify a mock or a third-party object never produces a false failure.
    @pytest.mark.unit
    def test_a_name_the_checker_does_not_know_is_skipped(self) -> None:
        # **LOGIC_STEP**: `repository` is an AsyncMock in every prescribed example, and any
        # attribute on it is legitimate. Only names bound to a known class are checked, which is
        # what keeps this from failing on the mocks the examples are built from.
        text = (
            "```python\n"
            "repository = AsyncMock()\n"
            "repository.anything_at_all.assert_awaited_once_with(1, 2, 3)\n"
            "```\n"
        )

        assert signature_mismatches_in_skill_text(text, {"ProbeService": _ProbeService}) == []

    # FUNCTION: test_a_fragment_that_does_not_parse_is_skipped
    # SUMMARY: Verify a partial snippet is a reader's problem, not a failure.
    @pytest.mark.unit
    def test_a_fragment_that_does_not_parse_is_skipped(self) -> None:
        assert signature_mismatches_in_skill_text("```python\nfor item in\n```\n") == []


# CLASS: tests.application.test_skill_texts_match_reality.TestDeletionSweepFindsEverything
# SUMMARY: Verify the completeness sweep the skill prints cannot miss a spelling of the vertical.
class TestDeletionSweepFindsEverything:
    # FUNCTION: test_the_skill_prints_a_case_insensitive_separator_agnostic_sweep
    # SUMMARY: Verify the runnable command is not the narrow one that missed two files.
    @pytest.mark.unit
    def test_the_skill_prints_a_case_insensitive_separator_agnostic_sweep(self) -> None:
        # **LOGIC_STEP**: Only fenced blocks are checked. The prose quotes the narrow command
        # inline, on purpose, to say what not to run — scanning prose would fail on the very
        # sentence that documents the fix.
        fenced = "\n".join(
            re.findall(r"```[a-z]*\n(.*?)```", _ADD_VERTICAL.read_text("utf-8"), re.DOTALL)
        )
        commands = re.findall(r"git grep [^\n]*reference[^\n]*", fenced)

        assert commands, "the deletion section prints no runnable sweep command at all"
        for command in commands:
            flags = command.split()[2]
            assert flags.startswith("-") and "i" in flags, f"case-sensitive sweep: {command}"
            assert "reference[-_]?task" in command, f"separator-bound sweep: {command}"

    # FUNCTION: test_the_narrow_pattern_really_does_miss_files
    # SUMMARY: Verify the reason the command was widened still holds in this tree.
    @pytest.mark.unit
    def test_the_narrow_pattern_really_does_miss_files(self) -> None:
        # **LOGIC_STEP**: This is the measurement the skill's prose rests on. If a future cleanup
        # makes every spelling uniform, this test fails and the prose should be softened rather
        # than the sweep re-narrowed.
        missed = _files_matching(_VERTICAL_ANY_SPELLING) - _files_matching(_VERTICAL_NAIVE_SPELLING)

        assert missed, "no file is spelled in a way the narrow pattern misses any more"


# CLASS: tests.application.test_skill_texts_match_reality.TestTheMemoryTagStaysGone
# SUMMARY: Verify no `# MEM:` tag reappears in the kernel, and no text tells a project to write one.
# NOTE: The integration these tags belonged to was removed on 2026-08-11. It had produced two tags
# in the whole kernel, both about the kernel's own line budget, and the reference vertical an agent
# copies carried none — so the shape was never demonstrated where it would be imitated. Worse, a
# tag points into an external store, and a checkout without access to that store turns red on its
# first ordinary edit with no working way out. One tag back brings the whole apparatus back, so
# this is the guard.
class TestTheMemoryTagStaysGone:
    # FUNCTION: test_no_kernel_file_carries_a_memory_tag
    # SUMMARY: Verify the tag is absent from every Python file in the kernel.
    @pytest.mark.unit
    def test_no_kernel_file_carries_a_memory_tag(self) -> None:
        carriers = sorted(
            str(path.relative_to(_REPO_ROOT))
            for root in _KERNEL_PYTHON_ROOTS
            for path in (_REPO_ROOT / root).rglob("*.py")
            if any(
                line.lstrip().startswith("# MEM:")
                for line in path.read_text(encoding="utf-8").splitlines()
            )
        )

        assert carriers == [], (
            "a `# MEM:` tag is back in the kernel: "
            + ", ".join(carriers)
            + ". The snapshot, anchors and rule ids that made it mean anything were removed; "
            "the tag alone points at nothing. Put the reason in a comment instead."
        )

    # FUNCTION: test_no_removed_component_is_still_referenced
    # SUMMARY: Verify no prose still sends the reader to a file or command that no longer exists.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "phantom",
        [
            "make refresh-memory-refs",
            ".agents/skills/memory-links",
            "sync_memory_refs",
            "docs/memory_refs.json",
        ],
    )
    def test_no_removed_component_is_still_referenced(self, phantom: str) -> None:
        # **LOGIC_STEP**: This test file names them itself, so it is the one exemption.
        listed = run(
            ["git", "grep", "-l", "--fixed-strings", phantom],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        offenders = [
            name
            for name in listed.stdout.split()
            if name != "tests/application/test_skill_texts_match_reality.py"
        ]

        assert offenders == [], f"`{phantom}` no longer exists but is still named in: {offenders}"


# ATTRIBUTE: _AGENT_FACING_DOCUMENTS (tuple[Path, ...])
# SUMMARY: Every document an agent is told to read before it runs a command.
_AGENT_FACING_DOCUMENTS: tuple[Path, ...] = (
    _REPO_ROOT / "README.md",
    _REPO_ROOT / "CLAUDE.md",
    _REPO_ROOT / "AGENTS.md",
    _REPO_ROOT / "docs" / "agent_rules.md",
    _ADD_VERTICAL,
    _INITIALIZE_PROJECT,
)

# ATTRIBUTE: _MAKE_IN_BACKTICKS (re.Pattern[str])
# SUMMARY: A `make <target>` written inline, which is how prose names a command.
_MAKE_IN_BACKTICKS: re.Pattern[str] = re.compile(r"`make ([a-z][a-z0-9-]*)")

# ATTRIBUTE: _MAKE_IN_A_BLOCK (re.Pattern[str])
# SUMMARY: A `make <target>` opening a line, which is how a fenced example names one.
_MAKE_IN_A_BLOCK: re.Pattern[str] = re.compile(r"^make ([a-z][a-z0-9-]*)", re.MULTILINE)


# FUNCTION: _targets_declared_in_the_makefile
# SUMMARY: Read the rule names the Makefile actually defines.
# OUTPUT: (set[str]): Every target that can be invoked.
def _targets_declared_in_the_makefile() -> set[str]:
    text = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    return {match.group(1) for match in re.finditer(r"^([A-Za-z0-9_.-]+):", text, re.MULTILINE)}


# FUNCTION: _targets_named_in_prose
# SUMMARY: Collect every make target the agent-facing documents tell a reader to run.
# OUTPUT: (dict[str, list[str]]): Target name → the documents naming it.
def _targets_named_in_prose() -> dict[str, list[str]]:
    named: dict[str, list[str]] = {}
    for document in _AGENT_FACING_DOCUMENTS:
        text = document.read_text(encoding="utf-8")
        found = {match.group(1) for match in _MAKE_IN_BACKTICKS.finditer(text)}
        found |= {match.group(1) for match in _MAKE_IN_A_BLOCK.finditer(text)}
        for target in found:
            named.setdefault(target, []).append(document.name)
    return named


# CLASS: tests.application.test_skill_texts_match_reality.TestEveryCommandTheDocumentsNameExists
# SUMMARY: Verify no document tells an agent to run a make target this repository does not define.
# NOTE: `make refresh-commands` outlived the target it named. It sat in `docs/agent_rules.md` and,
# through generation, in both wrappers and one skill — four places, all wrong, none of them failing
# anything. An agent following the instruction gets "No rule to make target", which reads as a
# broken checkout rather than a stale sentence.
class TestEveryCommandTheDocumentsNameExists:
    # FUNCTION: test_no_document_names_a_target_the_makefile_does_not_define
    # SUMMARY: Verify every `make X` in the agent-facing documents resolves to a real rule.
    @pytest.mark.unit
    def test_no_document_names_a_target_the_makefile_does_not_define(self) -> None:
        declared = _targets_declared_in_the_makefile()

        missing = {
            target: documents
            for target, documents in _targets_named_in_prose().items()
            if target not in declared
        }

        assert missing == {}, f"named in prose but absent from the Makefile: {missing}"

    # FUNCTION: test_the_reader_finds_the_commands_the_documents_actually_carry
    # SUMMARY: Verify the collector is not returning an empty set, which would make the guard vacuous.
    # **LOGIC_STEP**: The test above passes for a reader that finds nothing at all. Naming three
    # commands every document repeats is what separates "no broken target" from "no target read".
    @pytest.mark.unit
    @pytest.mark.parametrize("command", ["quality-gates", "init-project", "refresh-agent-docs"])
    def test_the_reader_finds_the_commands_the_documents_actually_carry(self, command: str) -> None:
        assert command in _targets_named_in_prose()
