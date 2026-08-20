# FILE: tests/application/test_template_neutrality.py
# SUMMARY: Guard every way a stale project name creeps back into a template that must stay neutral.
# NOTE: Every guard here exists because the defect it covers shipped. The template was renamed once and a sweep
# over the code missed `docs/project_map.md:1`, because that line sits OUTSIDE the block
# `scripts/structure_builder.py` regenerates — so `make refresh-project-map` rewrote the map every
# time and left the wrong name in the heading, and the drift check compared only the generated
# block. A grep-based sweep cannot be repeated by hand on every rename; a test can.

import json
import re
from pathlib import Path
from subprocess import run
from typing import Any

import pytest
from fastapi import FastAPI

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ATTRIBUTE: SUPERSEDED_TEMPLATE_NAMES (tuple[str, ...])
# SUMMARY: Names this template used to carry. A rename adds the retired name here.
SUPERSEDED_TEMPLATE_NAMES = (
    "backendbase",
    "backend-base",
    "backend_base",
    "backendscaffold",
)

# ATTRIBUTE: FORBIDDEN_TERMS_FILE (Path)
# SUMMARY: Optional, git-ignored list of words this checkout must not contain — one per line.
# **LOGIC_STEP**: The list is deliberately not committed. Whatever a repository must not mention —
# a client, an internal service, a stack it does not use — naming those words in a tracked file
# publishes exactly what the check exists to keep out. So the mechanism ships and the words stay
# local: write them into this file and the guard below enforces them. With no file the guard is
# inert, and TestForbiddenTermsMechanism proves it still works.
FORBIDDEN_TERMS_FILE = _REPO_ROOT / ".neutrality-terms"


# FUNCTION: _forbidden_terms
# SUMMARY: Read the local word list, ignoring blanks and comments.
# INPUT: source (Path): File to read; absent is normal and yields no terms.
# OUTPUT: (tuple[str, ...]): Lower-cased terms to search for.
def _forbidden_terms(source: Path) -> tuple[str, ...]:
    if not source.is_file():
        return ()
    return tuple(
        line.strip().lower()
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )


# FUNCTION: _files_containing
# SUMMARY: Report which of the given files contain any of the given terms.
# INPUT: paths (list[Path]): Files to read.
# INPUT: terms (tuple[str, ...]): Lower-cased terms to look for.
# OUTPUT: (list[str]): "<relative path>: <term>" for every hit, sorted by discovery order.
def _files_containing(paths: list[Path], terms: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except (UnicodeDecodeError, OSError):
            continue
        # **LOGIC_STEP**: Relative when the file is inside the repository, absolute when it is not.
        # The mechanism test feeds this temporary files, and `relative_to` raises on those.
        try:
            label = str(path.relative_to(_REPO_ROOT))
        except ValueError:
            label = str(path)
        hits.extend(f"{label}: {term}" for term in terms if term in text)
    return hits


# ATTRIBUTE: PROJECT_MAP_HEADING (str)
# SUMMARY: The only heading docs/project_map.md may carry — generic, so it cannot go stale.
PROJECT_MAP_HEADING = "# Project Map"

# ATTRIBUTE: CONTRACT_HEADING (str)
# SUMMARY: The only heading the always-loaded wrapper may carry. It names the kernel's contract, not a domain.
CONTRACT_HEADING = "# CLAUDE.md"

# ATTRIBUTE: MAY_NAME_THE_PROJECT (frozenset[str])
# SUMMARY: The one file under project/ allowed to contain the project's own name.
# `initialize-project` row 4 documents it: ProjectSettings.name's default is what a deployment
# without APP_NAME serves, so it has to be this project's name and nothing else.
MAY_NAME_THE_PROJECT = frozenset({"project/core/config_settings_core.py"})


# FUNCTION: _declared_project_name
# SUMMARY: Read this project's own name from the file that owns it.
# OUTPUT: (str): The project name, lower-cased for substring comparison.
def _declared_project_name() -> str:
    context = json.loads((_REPO_ROOT / "docs" / "project_context.json").read_text(encoding="utf-8"))
    return str(context["project_name"]).lower()


# FUNCTION: _tracked_files
# SUMMARY: List the repository's tracked files, which is exactly what a clone of the template gets.
# OUTPUT: (list[Path]): Absolute paths of every file in the git index.
def _tracked_files() -> list[Path]:
    result = run(
        ["git", "ls-files", "-z"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [_REPO_ROOT / name for name in result.stdout.split("\0") if name]


# CLASS: tests.application.test_template_neutrality.TestNoSupersededTemplateName
# SUMMARY: Verify no tracked file still carries a retired name of this template.
class TestNoSupersededTemplateName:
    # FUNCTION: test_retired_name_appears_in_no_tracked_file
    # SUMMARY: Verify a rename left nothing behind, including in generated and hand-written docs.
    @pytest.mark.unit
    def test_retired_name_appears_in_no_tracked_file(self) -> None:
        # **LOGIC_STEP**: This file names the retired strings, so scanning it would always fail.
        this_file = Path(__file__).resolve()
        offenders: list[str] = []

        for path in _tracked_files():
            if path.resolve() == this_file or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8").lower()
            except (UnicodeDecodeError, OSError):
                # **LOGIC_STEP**: Binary blobs (the vendored tokenizer cache) carry no prose.
                continue
            for name in SUPERSEDED_TEMPLATE_NAMES:
                if name in text:
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}: {name}")

        assert not offenders, "retired template name still present in: " + ", ".join(offenders)


# CLASS: tests.application.test_template_neutrality.TestForbiddenTerms
# SUMMARY: Verify nothing this checkout is not allowed to mention appears in a tracked file.
class TestForbiddenTerms:
    # FUNCTION: test_no_tracked_file_contains_a_forbidden_term
    # SUMMARY: Verify the local word list, if there is one, finds nothing.
    @pytest.mark.unit
    def test_no_tracked_file_contains_a_forbidden_term(self) -> None:
        terms = _forbidden_terms(FORBIDDEN_TERMS_FILE)
        if not terms:
            pytest.skip(f"no {FORBIDDEN_TERMS_FILE.name} in this checkout — nothing to enforce")

        offenders = _files_containing(_tracked_files(), terms)

        assert not offenders, "a forbidden term appears in: " + ", ".join(offenders)


# CLASS: tests.application.test_template_neutrality.TestForbiddenTermsMechanism
# SUMMARY: Verify the guard above would actually catch something, independent of any local list.
# **LOGIC_STEP**: The enforcing test skips wherever the word list is absent, which is every fresh
# clone. Without this, a mistake in the reader or the matcher would hide behind that skip forever
# and the guard would be discovered broken on the day it was needed.
class TestForbiddenTermsMechanism:
    # FUNCTION: test_a_listed_term_is_found_and_a_comment_is_not
    # SUMMARY: Verify terms are read, comments and blanks ignored, and matching is case-insensitive.
    @pytest.mark.unit
    def test_a_listed_term_is_found_and_a_comment_is_not(self, tmp_path: Path) -> None:
        listing = tmp_path / ".neutrality-terms"
        listing.write_text("# a comment\n\nForbiddenCo\n", encoding="utf-8")
        clean = tmp_path / "clean.md"
        clean.write_text("nothing to see", encoding="utf-8")
        dirty = tmp_path / "dirty.md"
        dirty.write_text("built by FORBIDDENCO in 2026", encoding="utf-8")

        terms = _forbidden_terms(listing)

        assert terms == ("forbiddenco",)
        assert _files_containing([clean], terms) == []
        assert len(_files_containing([dirty], terms)) == 1

    # FUNCTION: test_an_absent_list_yields_no_terms
    # SUMMARY: Verify a checkout without the file gets an empty list rather than an error.
    @pytest.mark.unit
    def test_an_absent_list_yields_no_terms(self, tmp_path: Path) -> None:
        assert _forbidden_terms(tmp_path / "nothing-here") == ()


# CLASS: tests.application.test_template_neutrality.TestProjectMapHeading
# SUMMARY: Verify the hand-written heading of the generated map cannot carry a project name.
class TestProjectMapHeading:
    # FUNCTION: test_heading_is_generic
    # SUMMARY: Verify docs/project_map.md opens with a heading no rename can invalidate.
    @pytest.mark.unit
    def test_heading_is_generic(self) -> None:
        # **LOGIC_STEP**: The generated block below the marker already prints the live project
        # name, so the heading carrying one too is duplication that only ever goes stale.
        first_line = (
            (_REPO_ROOT / "docs" / "project_map.md")
            .read_text(encoding="utf-8")
            .splitlines()[0]
            .strip()
        )

        assert first_line == PROJECT_MAP_HEADING


# CLASS: tests.application.test_template_neutrality.TestKernelSurfacesCarryNoProjectName
# SUMMARY: Verify the identity checklist cannot silently grow a seventh place.
# NOTE: Three surfaces named the template outside the six places initialize-project renames: the
# operational contract's own heading, project/__init__.py's SUMMARY, which is copied verbatim
# into docs/project_map.md, and the /tmp file make audit-deps wrote. None of them is
# project-specific, so each was made generic rather than added to the checklist. These guards
# keep it that way; the name is read from the project's own context file, so they hold in a
# renamed project too.
class TestKernelSurfacesCarryNoProjectName:
    # FUNCTION: test_the_operational_contract_heading_is_generic
    # SUMMARY: Verify the always-loaded wrapper opens with a heading no rename can invalidate.
    # **LOGIC_STEP**: This used to read ARCHITECTURE.md, which was folded into the wrapper on
    # 2026-08-11. The claim is unchanged — the first thing an agent reads must not carry a
    # project name — only the file that carries it moved.
    @pytest.mark.unit
    def test_the_operational_contract_heading_is_generic(self) -> None:
        first_line = (_REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8").splitlines()[0].strip()

        assert first_line == CONTRACT_HEADING

    # FUNCTION: test_application_package_names_the_project_in_one_declared_place
    # SUMMARY: Verify no tracked file under project/ carries the project name except the documented default.
    @pytest.mark.unit
    def test_application_package_names_the_project_in_one_declared_place(self) -> None:
        # **LOGIC_STEP**: Every tracked file, not just `*.py`. The first version of this guard
        # walked `rglob("*.py")` while claiming to cover "anywhere under project/", and
        # project/prompts/ ships a .txt the scan never opened — a prompt, a fixture, a future
        # .sql or .jinja asset could carry a stale name straight past it.
        name = _declared_project_name()
        offenders: list[str] = []
        for path in _tracked_files():
            repo_path = str(path.relative_to(_REPO_ROOT))
            if not repo_path.startswith("project/") or repo_path in MAY_NAME_THE_PROJECT:
                continue
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                # **LOGIC_STEP**: A binary asset carries no prose to go stale.
                continue
            if name in text.lower():
                offenders.append(repo_path)

        assert sorted(offenders) == [], (
            f"the project name appears under project/ outside the documented default: {offenders}"
        )

    # FUNCTION: test_dependency_audit_writes_to_a_temporary_path_it_did_not_invent
    # SUMMARY: Verify make audit-deps names no fixed /tmp file, which would carry a stale name.
    @pytest.mark.unit
    def test_dependency_audit_writes_to_a_temporary_path_it_did_not_invent(self) -> None:
        recipe = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8").split("\naudit-deps:", 1)[1]
        recipe = recipe.split("\n\n", 1)[0]

        assert "mktemp" in recipe
        assert "/tmp/" not in recipe


# ATTRIBUTE: _MANDATE_MARKERS (tuple[str, ...])
# SUMMARY: Phrases in docs/agent_rules.md that turn a command from a suggestion into a requirement.
_MANDATE_MARKERS = (
    "Finish with",
    "Never claim task completion without",
)


# CLASS: tests.application.test_template_neutrality.TestAgentPermissionsCoverMandatedCommands
# SUMMARY: Verify the agent is allowed to run the command the rules force it to run.
# NOTE: docs/agent_rules.md named a `make quality-gates-with-doctor` variant twice as the only
# acceptable finish for a non-trivial task, and .claude/settings.json allowed `make quality-gates`
# but not it. That variant no longer exists — the doctor runs inside quality-gates — but the
# class of defect does not depend on the target name.
# The rules and the permissions were maintained by hand, separately, and disagreed in the one place
# where disagreement stops work: the last step. Only mandated commands are checked — targets that
# change state (`init-project`, `migrate`) stay off the allow-list on purpose.
class TestAgentPermissionsCoverMandatedCommands:
    # FUNCTION: test_every_mandated_make_command_is_permitted
    # SUMMARY: Verify each command the rules mandate appears in the permissions allow-list.
    @pytest.mark.unit
    def test_every_mandated_make_command_is_permitted(self) -> None:
        rules = (_REPO_ROOT / "docs" / "agent_rules.md").read_text(encoding="utf-8")
        settings = json.loads(
            (_REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        allowed = set(settings["permissions"]["allow"])

        mandated = {
            match
            for line in rules.splitlines()
            if any(marker in line for marker in _MANDATE_MARKERS)
            for match in re.findall(r"`(make [a-z0-9][a-z0-9-]*)`", line)
        }

        assert mandated, "no mandated command found — check the markers, not the permissions"
        missing = sorted(command for command in mandated if f"Bash({command})" not in allowed)

        assert missing == []


# CLASS: tests.application.test_template_neutrality.TestStartupEventReportsTheLiveTitle
# SUMMARY: Verify the startup log event names the application's real title, not a baked-in literal.
# NOTE: composition_root builds the FastAPI title from settings.project.name and then logged
# "Mayak API" three lines later as a string literal, so every project built from this template
# announced the template's name in its own startup event. Nothing caught it: the title in the
# OpenAPI document was correct, and no assertion ever read the event.
class TestStartupEventReportsTheLiveTitle:
    # FUNCTION: test_logged_title_follows_the_configured_project_name
    # SUMMARY: Verify the fastapi_application_built event carries the same title the app serves.
    @pytest.mark.unit
    def test_logged_title_follows_the_configured_project_name(
        self,
        fastapi_app: FastAPI,
        test_settings: Any,
        log_capture: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # **LOGIC_STEP**: A sentinel name no template ever carried, so the assertion fails on any
        # literal — including a literal that happens to be the template's current name. The
        # fastapi_app fixture is requested for its settings override, then the application is
        # rebuilt inside the capture so the startup event is visible.
        from project.core.composition_root import CompositionRoot

        monkeypatch.setattr(test_settings.project, "name", "Sentinel")
        app = CompositionRoot().build_application()

        startup_events = [
            entry["kwargs"]["data"]
            for entry in log_capture
            if entry["kwargs"].get("event_id") == "system.fastapi_application_built"
        ]
        assert startup_events, "the application was built without announcing it"
        logged_title = startup_events[-1]["new_value"]["title"]

        assert app.title == "Sentinel API"
        assert logged_title == app.title
