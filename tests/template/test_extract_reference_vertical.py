# FILE: tests/template/test_extract_reference_vertical.py
# SUMMARY: Taking the reference vertical out of a project: nothing in the template, everything once in
# a project, and nothing at all when it would take a project's own work with it.
# Each case runs the script on a copy of this checkout. What only a whole project can show — its
# gates and e2e green after the extraction — is `make check-product`, run by template CI.

from __future__ import annotations

import ast
import hashlib
import json
import py_compile
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts import extract_reference_vertical as extraction
from scripts.check_product_from_template import copy_checkout, hermetic_env, replace_identity

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pristine(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("template") / "checkout"
    root.mkdir()
    copy_checkout(_REPO_ROOT, root)
    return root


@pytest.fixture
def checkout(pristine: Path, tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    shutil.copytree(pristine, root, symlinks=True)
    return root


def _files(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.parts
    }


def _imports_of_the_vertical(root: Path) -> list[str]:
    found: list[str] = []
    for path in [*root.glob("project/**/*.py"), *root.glob("tests/**/*.py")]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            if module and "reference_task" in module:
                found.append(f"{path.relative_to(root)}: {module}")
    return found


_PATH = re.compile(r"(?<![\w<>./-])((?:[\w.-]+/)+[\w.-]+\.(?:py|json|md|sh|toml))")


# What an agent reads for instructions: the contract it loads, the Makefile, the hooks, the README
# and PROJECT.md, the permission list, and every skill.
_TOLD_TO_AGENTS = (
    "AGENTS.md",
    "Makefile",
    "README.md",
    "PROJECT.md",
    ".githooks/pre-commit",
    ".agents/hooks/pre-edit-guard.sh",
    ".claude/settings.json",
)


# The paths those texts tell an agent to run, open or edit that the checkout does not have. A
# command naming a removed file fails the moment an agent follows it; the generated maps did this
# until they went, and a project's copy of the template's own tools can do it again. A path is
# found from the root or from the text's own directory, and one the text first tests with
# `[ -f <path> ]` is guarded — the Makefile's template-only steps are.
def _missing_paths_an_agent_is_told_to_use(root: Path) -> list[str]:
    skills = sorted(str(path.relative_to(root)) for path in root.glob(".agents/skills/*/SKILL.md"))
    missing: list[str] = []
    for source in (*_TOLD_TO_AGENTS, *skills):
        text = (root / source).read_text(encoding="utf-8")
        for path in _PATH.findall(text):
            if f"-f {path}" in text or (root / path).exists():
                continue
            if not (root / source).parent.joinpath(path).exists():
                missing.append(f"{source}: {path}")
    return missing


@pytest.mark.unit
def test_the_template_names_no_path_it_does_not_have() -> None:
    assert _missing_paths_an_agent_is_told_to_use(_REPO_ROOT) == []


# Inside the pre-commit hook git exports GIT_DIR, and the copy these tests make once ran its
# `git init`, `add` and `commit` against the outer repository: the commit re-ran the hook, the hook
# ran these tests, eighteen levels deep. The copy has to be a repository of its own.
@pytest.mark.unit
def test_copying_the_checkout_leaves_the_repository_that_runs_it_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=outer, env=hermetic_env(), check=True)
    monkeypatch.setenv("GIT_DIR", str(outer / ".git"))
    copy = tmp_path / "copy"
    copy.mkdir()

    copy_checkout(_REPO_ROOT, copy)

    outer_log = subprocess.run(
        ["git", "log", "--oneline"], cwd=outer, env=hermetic_env(), capture_output=True, text=True
    )
    assert outer_log.stdout == ""
    assert (copy / ".git").is_dir()


# Git 2.55 in CI packed a fresh copy's loose objects in a detached maintenance run right after its
# commit, while the next test was copying the directory: shutil.Error on .git/objects/<xx> in the
# diff-coverage job on 2026-09-24. Here a commit that would repack at once, in the foreground.
@pytest.mark.unit
def test_a_commit_in_the_copy_starts_no_maintenance_under_it(tmp_path: Path) -> None:
    copy_checkout(_REPO_ROOT, tmp_path)
    (tmp_path / "next.txt").write_text("next\n", encoding="utf-8")
    subprocess.run(["git", "add", "next.txt"], cwd=tmp_path, env=hermetic_env(), check=True)

    subprocess.run(
        [
            "git",
            *("-c", "gc.auto=1", "-c", "gc.autoDetach=false", "-c", "maintenance.autoDetach=false"),
            *("-c", "user.name=probe", "-c", "user.email=probe@example.invalid"),
            *("commit", "-qm", "next"),
        ],
        cwd=tmp_path,
        env=hermetic_env(),
        check=True,
    )

    assert list((tmp_path / ".git/objects/pack").glob("*.pack")) == []


class TestTheExtractionMatchesTheTemplate:
    # Stale digests make every project refuse — "a file of the vertical differs" — for an edit the
    # template made itself. Regenerate with `--print-manifest` in the same commit as the edit.
    @pytest.mark.unit
    def test_the_manifest_is_the_sample_as_it_ships(self) -> None:
        shipped = {
            path: hashlib.sha256((_REPO_ROOT / path).read_bytes()).hexdigest()
            for path in extraction.SAMPLE_FILES
        }

        assert shipped == extraction.SAMPLE_SHA256

    # The same for what each cut takes from a shared file: stale, every project refuses with "lines
    # the template did not ship" for an edit the template made itself.
    @pytest.mark.unit
    def test_the_cut_manifest_is_the_shared_files_as_they_ship(self) -> None:
        assert extraction.cut_digests(_REPO_ROOT) == extraction.CUT_SHA256

    # A kernel file the list does not know reads as a vertical of the project's own, and the
    # extraction refuses in every project made from the template.
    @pytest.mark.unit
    def test_the_kernel_lists_name_every_other_file_where_verticals_go(self) -> None:
        for directory, kernel in extraction.KERNEL_FILES.items():
            present = {
                path.name
                for path in (_REPO_ROOT / directory).glob("*.py")
                if f"{directory}/{path.name}" not in extraction.SAMPLE_SHA256
            }
            assert present == kernel, directory


@pytest.mark.unit
def test_the_template_itself_is_left_alone(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _files(checkout)

    assert extraction.main(["--root", str(checkout)]) == 0

    assert _files(checkout) == before
    assert "this is the template" in capsys.readouterr().out


@pytest.mark.unit
def test_a_project_has_the_vertical_taken_out_once(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replace_identity(checkout)
    shipped = {
        path: (checkout / path).read_text(encoding="utf-8") for path in extraction.SAMPLE_FILES
    }

    assert extraction.main(["--root", str(checkout)]) == 0

    # A refusal returns 0 as well; without its reason the failure below would be a bare
    # FileNotFoundError from the scaffold.
    assert "reference vertical taken out" in capsys.readouterr().out
    scaffold = checkout / extraction.SCAFFOLD
    assert {path: (scaffold / path).read_text(encoding="utf-8") for path in shipped} == shipped
    assert not any((checkout / path).exists() for path in shipped)
    assert not (checkout / "tests/template").exists()
    assert _imports_of_the_vertical(checkout) == []
    # A project has no template to make a project from; the job only cost every push a minute.
    workflow = (checkout / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert ("  product-from-template:" in workflow, "  secret-scan:" in workflow) == (False, True)
    context = json.loads((checkout / "docs/project_context.json").read_text(encoding="utf-8"))
    assert "verticals" not in context
    drop = (checkout / extraction.DROP_MIGRATION).read_text(encoding="utf-8")
    assert f'down_revision: Union[str, None] = "{extraction.SAMPLE_HEAD}"' in drop
    assert _missing_paths_an_agent_is_told_to_use(checkout) == []
    assert extraction.build_plan(checkout) == (
        "already done: the reference vertical is in " + extraction.SCAFFOLD
    )


# bench3 (2026-09-27): a test run had left bytecode in tests/template/__pycache__, the plan named it
# among the deletions, and `git add` refused the whole list — exit 1, nothing staged. Tool leftovers
# are planned apart from the staged deletions, and what goes is exactly what the plan names.
@pytest.mark.unit
def test_tool_leftovers_do_not_stop_the_staging_and_the_plan_is_what_goes(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replace_identity(checkout)
    source = checkout / "tests/template/test_run_mutations.py"
    py_compile.compile(
        str(source), cfile=str(source.parent / "__pycache__/test_run_mutations.cpython-313.pyc")
    )
    (checkout / "tests/template/.pytest_cache/v/cache").mkdir(parents=True)
    (checkout / "tests/template/.pytest_cache/v/cache/nodeids").write_text("[]\n", encoding="utf-8")
    plan = extraction.build_plan(checkout)
    assert isinstance(plan, extraction.Plan), plan
    assert extraction.main(["--root", str(checkout), "--dry-run"]) == 0
    assert "and 2 tool leftovers git ignores" in capsys.readouterr().out
    before = set(_files(checkout))

    assert extraction.main(["--root", str(checkout)]) == 0

    assert "follow-up failed" not in capsys.readouterr().out
    assert before - set(_files(checkout)) == {*plan.deletes, *plan.generated}
    assert len(plan.generated) == 2
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-status"],
        cwd=checkout,
        env=hermetic_env(),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "D\ttests/template/test_run_mutations.py" in staged.splitlines()
    assert not (checkout / "tests/template").exists()
    assert extraction.main(["--root", str(checkout)]) == 0
    assert "already done" in capsys.readouterr().out


# A file the project deleted from the template's paths is no work to keep, and a file of its own
# outside them is none of the extraction's business: neither stops it, and the second stays.
@pytest.mark.unit
def test_deleted_template_files_and_work_elsewhere_do_not_stop_it(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replace_identity(checkout)
    (checkout / "tests/template/test_run_mutations.py").unlink()
    (checkout / "scratch.md").write_text("mine\n", encoding="utf-8")

    assert extraction.main(["--root", str(checkout)]) == 0

    assert "reference vertical taken out" in capsys.readouterr().out
    assert (checkout / "scratch.md").read_text(encoding="utf-8") == "mine\n"
    assert not (checkout / "tests/template").exists()


# Blank lines an editor added at the end of a shared file are not a line of the project's own; a
# refusal over them would stop every project whose editor ends files differently.
@pytest.mark.unit
def test_blank_lines_at_the_end_of_a_shared_file_do_not_stop_it(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replace_identity(checkout)
    for path in ("project/core/service_registration.py", ".agents/skills/add-vertical/SKILL.md"):
        target = checkout / path
        target.write_text(target.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")

    assert extraction.main(["--root", str(checkout)]) == 0

    assert "reference vertical taken out" in capsys.readouterr().out


def _keep_the_template_name(root: Path) -> None:
    context_path = root / "docs/project_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    context["is_template"] = False
    context_path.write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")


def _edit_the_sample(root: Path) -> None:
    replace_identity(root)
    service = root / "project/application/reference_task_service.py"
    service.write_text(
        service.read_text(encoding="utf-8") + "\n# started on it\n", encoding="utf-8"
    )


def _add_a_vertical_of_its_own(root: Path) -> None:
    replace_identity(root)
    (root / "project/application/booking_service.py").write_text(
        "# FILE: project/application/booking_service.py\n# SUMMARY: Bookings.\n", encoding="utf-8"
    )


# In a package of its own: the independent check of 2026-09-25 found the search looked only at the
# top of each directory, so this one went unseen and the extraction went ahead.
def _add_a_nested_vertical_of_its_own(root: Path) -> None:
    replace_identity(root)
    package = root / "project/domain/booking"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "model.py").write_text(
        "# FILE: project/domain/booking/model.py\n# SUMMARY: A booking.\n", encoding="utf-8"
    )


# A line of the project's own below the vertical's in a shared file. The cut ran from the
# vertical's first line to the end of the file, and took this line with it — silently, the same
# check found: the diff was staged, nothing said so.
def _add_a_line_of_its_own_to_a_shared_file(root: Path) -> None:
    replace_identity(root)
    registration = root / "project/core/service_registration.py"
    registration.write_text(
        registration.read_text(encoding="utf-8") + "\n\nOWN_SETTING = 1\n", encoding="utf-8"
    )


def _git_in(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, env=hermetic_env(), capture_output=True, text=True, check=True
    ).stdout


# Local work in the paths the extraction deletes. Until 1.3.1 they went whole: the plan named what
# git tracked, the deletion took everything, and an edit no commit held was gone without a word.
def _note_in_the_template_tests(root: Path) -> None:
    replace_identity(root)
    (root / "tests/template/local-note.txt").write_text("not tracked\n", encoding="utf-8")


def _note_in_the_mutation_catalogue(root: Path) -> None:
    replace_identity(root)
    (root / "docs/mutations/ideas.md").write_text("not tracked\n", encoding="utf-8")


def _uncommitted_edit_of_a_template_test(root: Path) -> None:
    replace_identity(root)
    with (root / "tests/template/test_run_mutations.py").open("a", encoding="utf-8") as test:
        test.write("\n# started on it\n")


def _staged_new_template_test(root: Path) -> None:
    replace_identity(root)
    (root / "tests/template/test_mine.py").write_text("# staged\n", encoding="utf-8")
    _git_in(root, "add", "tests/template/test_mine.py")


def _uncommitted_edit_of_the_mutation_runner(root: Path) -> None:
    replace_identity(root)
    with (root / "scripts/run_mutations.py").open("a", encoding="utf-8") as runner:
        runner.write("\n# started on it\n")


# Ignored is not disposable: a log or an env file kept out of git is still the project's own.
def _ignored_file_of_its_own(root: Path) -> None:
    replace_identity(root)
    (root / "docs/mutations/run.log").write_text("kept out of git\n", encoding="utf-8")


def _commit(root: Path, *paths: str) -> None:
    _git_in(root, "add", "--", *paths)
    _git_in(
        root,
        "-c",
        "user.name=probe",
        "-c",
        "user.email=probe@example.invalid",
        "commit",
        "-qm",
        "own",
    )


# Committed is not the template's either. Until 1.3.2 the deletions were what `git ls-files` listed
# under the template's paths, so a commit made a project's note one of them: gone, exit 0.
def _committed_note_in_the_template_tests(root: Path) -> None:
    replace_identity(root)
    (root / "tests/template/product-notes.md").write_text("ours\n", encoding="utf-8")
    _commit(root, "tests/template/product-notes.md")


def _committed_edit_of_a_template_test(root: Path) -> None:
    replace_identity(root)
    with (root / "tests/template/test_run_mutations.py").open("a", encoding="utf-8") as test:
        test.write("\n# kept for the project\n")
    _commit(root, "tests/template/test_run_mutations.py")


def _committed_edit_of_the_product_check(root: Path) -> None:
    replace_identity(root)
    with (root / "scripts/check_product_from_template.py").open("a", encoding="utf-8") as script:
        script.write("\n# kept for the project\n")
    _commit(root, "scripts/check_product_from_template.py")


def _committed_edit_of_the_extractor(root: Path) -> None:
    replace_identity(root)
    with (root / extraction.SELF).open("a", encoding="utf-8") as stream:
        stream.write("\n# Product-specific extraction convention.\n")
    _commit(root, extraction.SELF)


# A draft where the extraction puts the scaffold's README: v1.3.1 wrote its own over it, exit 0.
def _draft_in_the_scaffold(root: Path) -> None:
    replace_identity(root)
    readme = root / extraction.SCAFFOLD / "README.md"
    readme.parent.mkdir(parents=True)
    readme.write_text("Keep this draft byte for byte.\n", encoding="utf-8")


def _different_copy_in_the_scaffold(root: Path) -> None:
    replace_identity(root)
    copy = root / extraction.SCAFFOLD / "project/domain/reference_task.py"
    copy.parent.mkdir(parents=True)
    copy.write_text("# my own version\n", encoding="utf-8")
    _commit(root, f"{extraction.SCAFFOLD}/project/domain/reference_task.py")


def _a_file_where_the_scaffold_directory_goes(root: Path) -> None:
    replace_identity(root)
    (root / extraction.SCAFFOLD).write_text("notes\n", encoding="utf-8")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("prepare", "reason"),
    [
        (_keep_the_template_name, "still names the template"),
        (_edit_the_sample, "differ from the template's"),
        (_add_a_vertical_of_its_own, "files of its own vertical"),
        (_add_a_nested_vertical_of_its_own, "project/domain/booking/model.py"),
        (_add_a_line_of_its_own_to_a_shared_file, "project/core/service_registration.py"),
        (_note_in_the_template_tests, "tests/template/local-note.txt"),
        (_note_in_the_mutation_catalogue, "docs/mutations/ideas.md"),
        (_uncommitted_edit_of_a_template_test, "tests/template/test_run_mutations.py"),
        (_staged_new_template_test, "tests/template/test_mine.py"),
        (_uncommitted_edit_of_the_mutation_runner, "scripts/run_mutations.py"),
        (_ignored_file_of_its_own, "docs/mutations/run.log"),
        (
            _committed_note_in_the_template_tests,
            "did not ship, committed or not: tests/template/product-notes.md",
        ),
        (
            _committed_edit_of_a_template_test,
            "differ from what it shipped, committed or not: tests/template/test_run_mutations.py",
        ),
        (_committed_edit_of_the_product_check, "scripts/check_product_from_template.py"),
        (_committed_edit_of_the_extractor, extraction.SELF),
        (
            _draft_in_the_scaffold,
            f"{extraction.SCAFFOLD}/README.md (create: a different file is there)",
        ),
        (
            _different_copy_in_the_scaffold,
            f"{extraction.SCAFFOLD}/project/domain/reference_task.py (create: a different file is there)",
        ),
        (
            _a_file_where_the_scaffold_directory_goes,
            f"(create: {extraction.SCAFFOLD} is not a directory)",
        ),
    ],
    ids=[
        "identity-not-replaced",
        "sample-edited",
        "own-vertical",
        "own-vertical-in-a-package",
        "own-line-in-a-shared-file",
        "untracked-note-in-tests-template",
        "untracked-note-in-docs-mutations",
        "uncommitted-edit-of-a-template-test",
        "staged-new-template-test",
        "uncommitted-edit-of-the-mutation-runner",
        "ignored-file-of-its-own",
        "committed-note-in-tests-template",
        "committed-edit-of-a-template-test",
        "committed-edit-of-a-template-script",
        "committed-edit-of-the-extractor",
        "draft-in-the-scaffold-readme",
        "different-copy-in-the-scaffold",
        "file-where-the-scaffold-directory-goes",
    ],
)
def test_it_refuses_and_changes_nothing_when_it_would_take_a_projects_work(
    checkout: Path,
    capsys: pytest.CaptureFixture[str],
    prepare: Callable[[Path], None],
    reason: str,
) -> None:
    prepare(checkout)
    before = _files(checkout), _git_in(checkout, "ls-files", "--stage")

    assert extraction.main(["--root", str(checkout), "--dry-run"]) == 0
    assert extraction.main(["--root", str(checkout)]) == 0

    assert (_files(checkout), _git_in(checkout, "ls-files", "--stage")) == before
    dry_run, applied = capsys.readouterr().out.strip().split("\n")
    assert dry_run == applied and reason in applied


# Bootstrap asks the same plan before it writes the identity, so the refusal names the file and
# nothing — identity, .env, the note — has changed.
def _untracked_note(root: Path) -> str:
    (root / "tests/template/local-note.txt").write_text("not tracked\n", encoding="utf-8")
    return "tests/template/local-note.txt"


def _committed_note(root: Path) -> str:
    (root / "tests/template/product-notes.md").write_text("ours\n", encoding="utf-8")
    _commit(root, "tests/template/product-notes.md")
    return "tests/template/product-notes.md"


def _scaffold_draft(root: Path) -> str:
    readme = root / extraction.SCAFFOLD / "README.md"
    readme.parent.mkdir(parents=True)
    readme.write_text("Keep this draft byte for byte.\n", encoding="utf-8")
    return f"{extraction.SCAFFOLD}/README.md"


@pytest.mark.unit
@pytest.mark.parametrize(
    "prepare",
    [_untracked_note, _committed_note, _scaffold_draft],
    ids=["untracked-note", "committed-note", "scaffold-draft"],
)
def test_bootstrap_stops_on_local_work_before_writing_anything(
    checkout: Path, prepare: Callable[[Path], str]
) -> None:
    from scripts.bootstrap_project import bootstrap

    path = prepare(checkout)
    before = _files(checkout), _git_in(checkout, "ls-files", "--stage")

    with pytest.raises(ValueError, match=re.escape(path)):
        bootstrap(checkout, name="Room Book", domain="Bookings", database="none", uv="uv")

    assert (_files(checkout), _git_in(checkout, "ls-files", "--stage")) == before


# A path that already holds exactly what the extraction would put there is a step done, not a
# conflict: the extraction goes ahead and the file is as it was.
@pytest.mark.unit
def test_a_destination_already_holding_the_same_bytes_does_not_stop_it(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replace_identity(checkout)
    copy = checkout / extraction.SCAFFOLD / "project/domain/reference_task.py"
    copy.parent.mkdir(parents=True)
    shipped = (checkout / "project/domain/reference_task.py").read_bytes()
    copy.write_bytes(shipped)
    _commit(checkout, f"{extraction.SCAFFOLD}/project/domain/reference_task.py")

    migration = checkout / extraction.DROP_MIGRATION
    migration.write_text(extraction._drop_migration(), encoding="utf-8")
    assert extraction.main(["--root", str(checkout)]) == 0

    assert "reference vertical taken out" in capsys.readouterr().out
    assert copy.read_bytes() == shipped
    assert not (checkout / "project/domain/reference_task.py").exists()


# Checked when planned and again before the first write: a file that turns up in between is kept,
# and nothing of the plan is applied.
@pytest.mark.unit
def test_a_file_that_appears_after_the_plan_is_kept_and_nothing_is_written(checkout: Path) -> None:
    replace_identity(checkout)
    plan = extraction.build_plan(checkout)
    assert isinstance(plan, extraction.Plan), plan
    readme = checkout / extraction.SCAFFOLD / "README.md"
    readme.parent.mkdir(parents=True)
    readme.write_text("arrived late\n", encoding="utf-8")
    before = _files(checkout), _git_in(checkout, "ls-files", "--stage")

    with pytest.raises(ValueError, match=re.escape(f"{extraction.SCAFFOLD}/README.md")):
        extraction.apply_plan(checkout, plan)

    assert (_files(checkout), _git_in(checkout, "ls-files", "--stage")) == before


# Stale, every project refuses — "files the template did not ship" or "differ from what it shipped"
# — over an edit the template made itself. Regenerate with `--print-manifest` in the same commit.
@pytest.mark.unit
def test_the_template_only_manifest_is_what_the_template_ships() -> None:
    assert extraction.template_only_digests(_REPO_ROOT) == extraction.TEMPLATE_ONLY_SHA256


# The proof that outlives tests/template: in a project after the extraction, the test runner it
# keeps collects a test in a directory the template never had, and leaves tests/functional to
# Docker. Collection only — the red-then-green run of such a test is checked outside, by a full
# `make verify` in a product, which this suite cannot run from inside its own verify.
@pytest.mark.unit
def test_a_projects_runner_collects_a_test_directory_of_its_own(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replace_identity(checkout)
    assert extraction.main(["--root", str(checkout)]) == 0
    assert "reference vertical taken out" in capsys.readouterr().out
    canary = checkout / "tests/domain/test_product_version_canary.py"
    canary.parent.mkdir()
    canary.write_text("def test_product_version_canary() -> None:\n    assert False\n")

    collected = subprocess.run(
        [sys.executable, "scripts/run_all_tests.py", "--skip-functional"],
        cwd=checkout,
        env={
            key: value
            for key, value in hermetic_env().items()
            if not key.startswith("COV_CORE_") and key != "PYTEST_ADDOPTS"
        }
        | {"PYTEST_ADDOPTS": "--collect-only"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert collected.returncode == 0, collected.stdout[-2000:] + collected.stderr[-2000:]
    nodes = [line for line in collected.stdout.splitlines() if "::" in line]
    assert "tests/domain/test_product_version_canary.py::test_product_version_canary" in nodes
    assert len(nodes) == len(set(nodes))
    assert not [node for node in nodes if node.startswith(("tests/functional", "tests/template"))]
    assert any(node.startswith("tests/application/") for node in nodes)
