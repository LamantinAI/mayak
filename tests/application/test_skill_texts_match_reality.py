# FILE: tests/application/test_skill_texts_match_reality.py
# SUMMARY: Guard the factual claims the skills make about this repository, so prose cannot drift
# away from the code it instructs an agent to change.
# NOTE: Some tests read a SKILL.md and fail when it stops naming something real — they check that a
# literal is PRESENT, not that the surrounding sentence is true. The rest never open a SKILL.md at
# all: they pin the repository facts the prose rests on (both ledgers still pin an exact set, the
# detector recognises the spellings it claims to, the narrow grep really does miss files). No test
# here can verify that a sentence means what it says — that is what a reader is for.

from __future__ import annotations

import ast
import re
from pathlib import Path
from subprocess import run

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADD_VERTICAL = _REPO_ROOT / ".agents" / "skills" / "add-vertical" / "SKILL.md"
_INITIALIZE_PROJECT = _REPO_ROOT / ".agents" / "skills" / "initialize-project" / "SKILL.md"
_ADR_007 = _REPO_ROOT / "docs" / "adr" / "ADR-007-autocommit-and-explicit-transactions.md"

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
def _tracked_text_files() -> list[Path]:
    listed = run(
        ["git", "ls-files", "-z"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [_REPO_ROOT / name for name in listed.stdout.split("\0") if name]


# FUNCTION: _files_matching
# SUMMARY: Repo-relative paths of tracked files whose text matches a pattern.
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
    # **LOGIC_STEP**: ast, not import — importing the whole package would run side effects.
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
    # **LOGIC_STEP**: `f(*args)`/`f(**kwargs)` hide the real shape, so skip rather than guess.
    if any(isinstance(argument, ast.Starred) for argument in node.args):
        return None
    if any(keyword.arg is None for keyword in node.keywords):
        return None
    return len(node.args), [keyword.arg for keyword in node.keywords if keyword.arg]


# FUNCTION: signature_mismatches_in_skill_text
# SUMMARY: Report calls in a skill's python examples that the real code would reject.
# INPUT: classes (dict[str, type] | None): Class map to check against; the application package's
#        own classes when omitted — tests pass their own so they do not depend on a vertical every
#        project is told to delete.
# OUTPUT: (list[str]): One human-readable line per mismatch; empty when every example would run.
# NOTE: Everything else in this module checks that the prose NAMES a real thing; this checks that
# the code it shows would actually run.
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
            # **LOGIC_STEP**: An unparseable fragment is a documentation choice, not a signature
            # error — leave it to a reader rather than this check.
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
    # **LOGIC_STEP**: `self` is still in the signature; supply a stand-in for it.
    takes_self = next(iter(parameters.parameters), None) in {"self", "cls"}
    arguments: list[object] = [None] * (positional + (1 if takes_self else 0))
    try:
        parameters.bind(*arguments, **{name: None for name in keywords})
    except TypeError as error:
        return [f"{rendered}: {error}"]
    return []


# FUNCTION: _exact_table_set_assertions
# SUMMARY: Find tests that compare the ORM metadata's table names against a fixed collection.
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
            # side each operand is on — a named constant, a set() call, a sorted list all count.
            # `in metadata.tables` (membership) is deliberately not one: it stays green when a
            # table is added, which is the case these ledgers exist to catch.
            if "metadata.tables" in ast.unparse(statement):
                names.append(node.name)
                break
    return names


# CLASS: tests.application.test_skill_texts_match_reality.TestAddVerticalNamesEveryTableLedger
# SUMMARY: Verify the skill names every test a new table has to be registered in.
class TestAddVerticalNamesEveryTableLedger:
    # FUNCTION: test_the_skill_names_the_one_ledger_and_no_second_one_exists
    # SUMMARY: Verify no ledger exists that the skill fails to mention, and that the skill's one
    # named ledger is the only one left in the tree — the same sentence pinned in two files at once
    # cost two projects a red gate twice each.
    @pytest.mark.unit
    def test_the_skill_names_the_one_ledger_and_no_second_one_exists(self) -> None:
        skill_text = _ADD_VERTICAL.read_text(encoding="utf-8")
        # **LOGIC_STEP**: The whole tree, not two directories — a ledger anywhere would turn a
        # gate red, and "the gate fails in a file nobody told you about" is what this prevents.
        carriers = [
            path
            for directory in _LEDGER_SCAN_DIRECTORIES
            for path in sorted((_REPO_ROOT / directory).rglob("*.py"))
            if _exact_table_set_assertions(path)
        ]

        unnamed = [str(p.relative_to(_REPO_ROOT)) for p in carriers if p.name not in skill_text]
        assert unnamed == [], (
            f"these tests pin the exact table set but the skill never names: {unnamed}"
        )

        relative = [str(p.relative_to(_REPO_ROOT)) for p in carriers]
        assert relative == ["tests/application/test_validate_migrations.py"], (
            f"the exact table set must be pinned in exactly this one place; found {relative}"
        )

    # FUNCTION: test_the_detector_recognises_every_idiomatic_ledger_spelling
    # SUMMARY: Verify the scan is not defeated by a named constant, a set() call, or a sorted list —
    # and that a membership check (`in metadata.tables`), which stays green when a table is added,
    # is correctly not mistaken for a ledger.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("assertion", "detected"),
        [
            ('assert set(Base.metadata.tables.keys()) == {"a"}', True),
            ("assert set(Base.metadata.tables.keys()) == EXPECTED_TABLES", True),
            ('assert Base.metadata.tables.keys() == set(["a"])', True),
            ('assert sorted(Base.metadata.tables.keys()) == ["a"]', True),
            ('assert {"a"} == set(Base.metadata.tables)', True),
            ('assert "a" in Base.metadata.tables', False),
        ],
    )
    def test_the_detector_recognises_every_idiomatic_ledger_spelling(
        self, assertion: str, detected: bool, tmp_path: Path
    ) -> None:
        module = tmp_path / "test_probe.py"
        module.write_text(f"def test_ledger() -> None:\n    {assertion}\n", encoding="utf-8")

        assert _exact_table_set_assertions(module) == (["test_ledger"] if detected else [])


# CLASS: tests.application.test_skill_texts_match_reality._ProbeService
# SUMMARY: Stand-in for a service, so the checker's own tests survive deleting the reference
# vertical — calling ReferenceTaskService by name here would go red the day it is deleted.
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

    # FUNCTION: test_the_checker_reports_and_clears_correctly
    # SUMMARY: Verify a missing argument and a renamed method are reported, a correct example and a
    # name the checker does not know both clear, and an unparseable fragment is a reader's problem.
    @pytest.mark.unit
    def test_the_checker_reports_and_clears_correctly(self) -> None:
        probe: dict[str, type] = {"ProbeService": _ProbeService}

        # **LOGIC_STEP**: list_items(self, status, limit=50); the example omits `status`.
        missing = signature_mismatches_in_skill_text(
            "```python\nservice = ProbeService(repository)\nservice.list_items(limit=7)\n```\n",
            probe,
        )
        assert len(missing) == 1 and "list_items" in missing[0] and "status" in missing[0]

        no_attr = signature_mismatches_in_skill_text(
            "```python\nservice = ProbeService(repository)\nservice.create(title='x')\n```\n", probe
        )
        assert len(no_attr) == 1 and "no attribute 'create'" in no_attr[0]

        correct = (
            "```python\nservice = ProbeService(repository)\n"
            'service.list_items(status="pending", limit=7)\n```\n'
        )
        assert signature_mismatches_in_skill_text(correct, probe) == []

        # **LOGIC_STEP**: only names bound to a known class are checked, so a mock clears too.
        unknown = (
            "```python\nrepository = AsyncMock()\n"
            "repository.anything_at_all.assert_awaited_once_with(1, 2, 3)\n```\n"
        )
        assert signature_mismatches_in_skill_text(unknown, probe) == []

        assert signature_mismatches_in_skill_text("```python\nfor item in\n```\n") == []


# CLASS: tests.application.test_skill_texts_match_reality.TestDeletionSweepFindsEverything
# SUMMARY: Verify the completeness sweep the skill prints cannot miss a spelling of the vertical.
class TestDeletionSweepFindsEverything:
    # FUNCTION: test_the_skill_prints_a_sweep_the_narrow_pattern_cannot_match
    # SUMMARY: Verify the runnable command is case-insensitive and separator-agnostic, not the
    # narrow one that missed two files — and that the narrow pattern still really does miss files
    # in this tree, so the prose reason for widening it still holds.
    @pytest.mark.unit
    def test_the_skill_prints_a_sweep_the_narrow_pattern_cannot_match(self) -> None:
        # **LOGIC_STEP**: Only fenced blocks — the prose also quotes the narrow command on purpose
        # to say what not to run, and scanning prose would fail on that very sentence.
        fenced = "\n".join(
            re.findall(r"```[a-z]*\n(.*?)```", _ADD_VERTICAL.read_text("utf-8"), re.DOTALL)
        )
        commands = re.findall(r"git grep [^\n]*reference[^\n]*", fenced)

        assert commands, "the deletion section prints no runnable sweep command at all"
        for command in commands:
            flags = command.split()[2]
            assert flags.startswith("-") and "i" in flags, f"case-sensitive sweep: {command}"
            assert "reference[-_]?task" in command, f"separator-bound sweep: {command}"

        missed = _files_matching(_VERTICAL_ANY_SPELLING) - _files_matching(_VERTICAL_NAIVE_SPELLING)
        assert missed, "no file is spelled in a way the narrow pattern misses any more"


# ATTRIBUTE: _DELETION_ACCOUNTING_EXEMPTIONS
# SUMMARY: Path prefixes the deletion section's tables may cover as a category, not file-by-file,
# plus why each category is legitimate — narrower than "the table": a project's own migrations or
# its own ADRs must not make this test red for reasons the skill was never wrong about.
_DELETION_ACCOUNTING_EXEMPTIONS: tuple[tuple[str, str], ...] = (
    (".agents/skills/add-vertical/SKILL.md", "the skill need not name itself"),
    ("docs/adr/", "an ADR is a decision record; the skill points at ADR-007 once, not every ADR"),
    ("alembic/versions/", "migration history is append-only, governed by 'Two branches' above"),
)


# FUNCTION: _shared_basenames
# SUMMARY: Filenames that more than one tracked file in this repository carries.
# OUTPUT: (set[str]): Bare filenames that cannot stand for a single file on their own.
def _shared_basenames() -> set[str]:
    seen: dict[str, int] = {}
    for path in _tracked_text_files():
        seen[path.name] = seen.get(path.name, 0) + 1
    return {name for name, count in seen.items() if count > 1}


# FUNCTION: _skill_names
# SUMMARY: Whether the skill's text mentions a tracked file, by its repo-relative path or — only
# where that name belongs to one file in the whole repository — by its bare filename.
# OUTPUT: (bool): True when the skill names this file, not merely something called the same.
# **LOGIC_STEP**: A bare filename is how most survivors are spelled, so it counts — but where two
# directories share a basename, naming one file must not vouch for the other unread one, so the
# full path is required when the basename is ambiguous.
def _skill_names(path: str, skill_text: str, shared_basenames: set[str]) -> bool:
    if path in skill_text:
        return True
    name = Path(path).name
    if name in shared_basenames or name not in skill_text:
        return False
    # **LOGIC_STEP**: The mention has to be free of a longer path — `ai_query/common.py` in the
    # skill contains `common.py`, which must not vouch for `tests/application/common.py` too.
    return not re.search(rf"\S+/{re.escape(name)}", skill_text)


# FUNCTION: _contains_wrapped
# SUMMARY: Whether `phrase` (written with single spaces) appears in `text`, tolerant of the markdown
# soft-wrap that can insert a newline and indentation between two of its words.
def _contains_wrapped(phrase: str, text: str) -> bool:
    return phrase in re.sub(r"\s+", " ", text)


# CLASS: tests.application.test_skill_texts_match_reality.TestDeletionAccountsForEveryMatch
# SUMMARY: Verify every file currently carrying the vertical is named by the deletion section, or
# falls into one of the three exempt categories above — checking the actual sweep the skill tells a
# reader to run, not merely the exact-table-set ledgers `TestAddVerticalNamesEveryTableLedger` names.
class TestDeletionAccountsForEveryMatch:
    # FUNCTION: test_every_current_match_is_named_or_exempt
    @pytest.mark.unit
    def test_every_current_match_is_named_or_exempt(self) -> None:
        skill_text = _ADD_VERTICAL.read_text(encoding="utf-8")
        shared = _shared_basenames()
        unaccounted = [
            path
            for path in sorted(_files_matching(_VERTICAL_ANY_SPELLING))
            if not any(path.startswith(prefix) for prefix, _ in _DELETION_ACCOUNTING_EXEMPTIONS)
            if not _skill_names(path, skill_text, shared)
        ]

        assert unaccounted == [], (
            "these files carry `reference_task` but .agents/skills/add-vertical/SKILL.md never "
            "names them, individually or by one of its stated exemptions: " + ", ".join(unaccounted)
        )

    # FUNCTION: test_every_file_the_tables_name_still_carries_the_vertical
    # SUMMARY: Verify the other direction — a row naming a file that no longer carries
    # `reference_task` is a stale instruction, and the check above cannot see one.
    # NOTE: The forward check only asks whether every matching file is named. A row for a file that
    # stopped matching stays green there forever: the row for
    # `tests/application/test_validate_test_quality.py` survived the deletion of the span rules that
    # put `reference_task` in it, and pointed a reader at a file with no trace of the vertical left.
    @pytest.mark.unit
    def test_every_file_the_tables_name_still_carries_the_vertical(self) -> None:
        skill_text = _ADD_VERTICAL.read_text(encoding="utf-8")
        # **LOGIC_STEP**: Only rows whose first cell is a backticked, placeholder-free path that
        # exists on disk. `project/domain/<name>.py` is a shape, not a file, and a row about a
        # directory is not a claim about any one file's text.
        named = {
            match.group(1)
            for match in re.finditer(r"(?m)^\| `([^`<>]+\.[a-z]+)` \|", skill_text)
            if (_REPO_ROOT / match.group(1)).is_file()
        }
        stale = sorted(
            path
            for path in named
            if not _VERTICAL_ANY_SPELLING.search((_REPO_ROOT / path).read_text(encoding="utf-8"))
        )

        assert stale == [], (
            "these files are named by a table in .agents/skills/add-vertical/SKILL.md, but no "
            "longer carry `reference_task` at all, so the row is a stale instruction: "
            + ", ".join(stale)
        )

    # FUNCTION: test_a_shared_basename_never_vouches_for_the_wrong_file
    # SUMMARY: Verify the check above is not vacuous — a file the skill never mentions is reported —
    # and that a bare filename cannot ride on a full path the skill names for a different file:
    # `ai_query/common.py` must not vouch for `tests/application/common.py`, and a basename two
    # directories both carry has to be spelled in full to count.
    @pytest.mark.unit
    def test_a_shared_basename_never_vouches_for_the_wrong_file(self) -> None:
        assert not _skill_names(
            "tests/application/test_a_file_this_skill_will_never_mention.py",
            _ADD_VERTICAL.read_text(encoding="utf-8"),
            _shared_basenames(),
        )

        common = "the fixtures live in ai_query/common.py and nowhere else"
        assert not _skill_names("tests/application/common.py", common, set())
        assert _skill_names("ai_query/common.py", common, set())

        twin = "the deletion list names tests/application/probe_twin.py and nothing else"
        assert _skill_names("tests/application/probe_twin.py", twin, {"probe_twin.py"})
        assert not _skill_names("tests/infrastructure/probe_twin.py", twin, {"probe_twin.py"})

    # FUNCTION: test_the_exemption_prefixes_are_real_directories
    # SUMMARY: Verify each exempt category still matches at least one tracked file, so an exemption
    # for a directory that has been renamed or removed cannot silently widen the check for nothing.
    @pytest.mark.unit
    @pytest.mark.parametrize("prefix", [prefix for prefix, _ in _DELETION_ACCOUNTING_EXEMPTIONS])
    def test_the_exemption_prefixes_are_real_directories(self, prefix: str) -> None:
        tracked = {str(path.relative_to(_REPO_ROOT)) for path in _tracked_text_files()}

        assert any(path.startswith(prefix) for path in tracked), (
            f"no tracked file starts with {prefix!r} any more; this exemption covers nothing"
        )


# FUNCTION: _numbered_step
# SUMMARY: Slice out one numbered item's text from the "Order of work" list, by position rather
# than by the wording of its own heading — a rewritten step heading must not break this anchor.
# OUTPUT: (str): The text between "\n<n>. " and "\n<n+1>. ".
def _numbered_step(skill_text: str, n: int) -> str:
    after = re.split(rf"\n{n}\. ", skill_text, maxsplit=1)[1]
    return re.split(rf"\n{n + 1}\. ", after, maxsplit=1)[0]


# CLASS: tests.application.test_skill_texts_match_reality.TestConcurrencyAndForeignKeyGuidanceStays
# SUMMARY: Verify ADR-007's two prose additions and the skill's two step pointers still name each
# other — nothing here is exercised by a red/green pytest run in the template itself, since the
# reference vertical has no second row to conflict over and no ForeignKey to leave bare.
class TestConcurrencyAndForeignKeyGuidanceStays:
    # FUNCTION: test_adr_007_still_carries_both_claims
    # SUMMARY: Verify both headings are present, and the load-bearing sentence under the foreign-key
    # one — a heading alone can sit above prose that argues the opposite.
    @pytest.mark.unit
    def test_adr_007_still_carries_both_claims(self) -> None:
        text = _ADR_007.read_text(encoding="utf-8")
        assert _contains_wrapped("Where the single-row token does not reach", text)
        assert _contains_wrapped("A foreign key's deletion policy is a domain decision", text)
        assert _contains_wrapped(
            "writes exactly the `ForeignKey(...)` the ORM model declares", text
        )
        assert _contains_wrapped("RESTRICT", text) and _contains_wrapped("CASCADE", text)

    # FUNCTION: test_the_order_of_work_steps_still_point_at_adr_007
    # SUMMARY: Verify the step writing the service points at ADR-007's boundary section, and the
    # step writing the ORM model points at its deletion-policy section — rather than either
    # repeating the reasoning inline.
    @pytest.mark.unit
    def test_the_order_of_work_steps_still_point_at_adr_007(self) -> None:
        skill_text = _ADD_VERTICAL.read_text(encoding="utf-8")

        assert _contains_wrapped(
            "Where the single-row token does not reach", _numbered_step(skill_text, 6)
        )
        assert _contains_wrapped(
            "A foreign key's deletion policy is a domain decision", _numbered_step(skill_text, 2)
        )


# CLASS: tests.application.test_skill_texts_match_reality.TestTheMemoryTagStaysGone
# SUMMARY: Verify no `# MEM:` tag reappears in the kernel, and no text tells a project to write one
# — the external-memory integration these tags belonged to is gone, and a tag alone points at
# nothing, turning a checkout without that store's access red on its first ordinary edit.
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

        assert carriers == [], f"a `# MEM:` tag is back in the kernel: {carriers}"

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
# **LOGIC_STEP**: CLAUDE.md is not listed. It is an import stub with no prose of its own, so
# scanning it would name no target and quietly contribute nothing; the contract it delivers is
# AGENTS.md, which is scanned.
_AGENT_FACING_DOCUMENTS: tuple[Path, ...] = (
    _REPO_ROOT / "README.md",
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
# SUMMARY: Verify no document tells an agent to run a make target this repository does not define —
# a stale target reads as a broken checkout to an agent following the instruction, not as a typo.
class TestEveryCommandTheDocumentsNameExists:
    # FUNCTION: test_no_document_names_a_target_the_makefile_does_not_define
    # SUMMARY: Verify every `make X` in the agent-facing documents resolves to a real rule, and that
    # the collector is not vacuously returning an empty set — three commands every document repeats
    # is what separates "no broken target" from "no target read".
    @pytest.mark.unit
    def test_no_document_names_a_target_the_makefile_does_not_define(self) -> None:
        named = _targets_named_in_prose()
        declared = _targets_declared_in_the_makefile()

        missing = {target: docs for target, docs in named.items() if target not in declared}
        assert missing == {}, f"named in prose but absent from the Makefile: {missing}"

        for command in ("quality-gates", "init-project", "refresh-agent-docs"):
            assert command in named
