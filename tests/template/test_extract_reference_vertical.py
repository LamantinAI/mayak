# FILE: tests/template/test_extract_reference_vertical.py
# SUMMARY: Taking the reference vertical out of a project: nothing in the template, everything once in
# a project, and nothing at all when it would take a project's own work with it.
# Each case runs the script on a copy of this checkout. What only a whole project can show — its
# gates and e2e green after the extraction — is `make check-product`, run by template CI.

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
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


# The paths an agent in the project is told to act on — every one the generated maps name (the
# checks and tests to run for an edit, what `before-edit` answers from) and every one in the
# skills' code blocks — that the project does not have. A check naming a removed file fails the
# moment an agent follows it.
def _missing_paths_an_agent_is_told_to_use(root: Path) -> list[str]:
    def strings(node: object) -> list[str]:
        if isinstance(node, dict):
            return [s for key, value in node.items() for s in [key, *strings(value)]]
        if isinstance(node, list):
            return [s for value in node for s in strings(value)]
        return [node] if isinstance(node, str) else []

    sources: list[tuple[str, str]] = []
    for generated in ("docs/ai_context_map.json", "docs/ai_change_map.json"):
        tree = json.loads((root / generated).read_text(encoding="utf-8"))
        sources.append((generated, "\n".join(strings(tree))))
    for skill in sorted(root.glob(".agents/skills/*/SKILL.md")):
        blocks = re.findall(r"```[a-z]*\n(.*?)```", skill.read_text(encoding="utf-8"), re.S)
        sources.append((str(skill.relative_to(root)), "\n".join(blocks)))
    return [
        f"{source}: {path}"
        for source, text in sources
        for path in _PATH.findall(text)
        if not (root / path).exists()
    ]


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
    context = json.loads((checkout / "docs/project_context.json").read_text(encoding="utf-8"))
    assert "reference_task" not in context["verticals"]
    drop = (checkout / extraction.DROP_MIGRATION).read_text(encoding="utf-8")
    assert f'down_revision: Union[str, None] = "{extraction.SAMPLE_HEAD}"' in drop
    assert _missing_paths_an_agent_is_told_to_use(checkout) == []
    assert extraction.build_plan(checkout) == (
        "already done: the reference vertical is in " + extraction.SCAFFOLD
    )


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


@pytest.mark.unit
@pytest.mark.parametrize(
    ("prepare", "reason"),
    [
        (_keep_the_template_name, "still names the template"),
        (_edit_the_sample, "differ from the template's"),
        (_add_a_vertical_of_its_own, "files of its own vertical"),
        (_add_a_nested_vertical_of_its_own, "project/domain/booking/model.py"),
        (_add_a_line_of_its_own_to_a_shared_file, "project/core/service_registration.py"),
    ],
    ids=[
        "identity-not-replaced",
        "sample-edited",
        "own-vertical",
        "own-vertical-in-a-package",
        "own-line-in-a-shared-file",
    ],
)
def test_it_refuses_and_changes_nothing_when_it_would_take_a_projects_work(
    checkout: Path,
    capsys: pytest.CaptureFixture[str],
    prepare: Callable[[Path], None],
    reason: str,
) -> None:
    prepare(checkout)
    before = _files(checkout)

    assert extraction.main(["--root", str(checkout)]) == 0

    assert _files(checkout) == before
    assert reason in capsys.readouterr().out
