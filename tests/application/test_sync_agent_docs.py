# FILE: tests/application/test_sync_agent_docs.py
# SUMMARY: Unit tests for the generated agent wrapper sync script.

from pathlib import Path
import re
import sys

import pytest

from scripts.sync_agent_docs import (
    AGENTS_PATH,
    CLAUDE_PATH,
    DROPPED_SECTION_RULE_ID,
    ROOT_DIR,
    DroppedSectionError,
    WRAPPER_READERS,
    build_documents,
    dropped_section_headings,
    main,
    malformed_section_headings,
    render_python_versions,
)

# ATTRIBUTE: _REPO_ROOT (Path)
# SUMMARY: Repository root, for the tests that compare two hand-written documents against each other.
_REPO_ROOT = Path(__file__).resolve().parents[2]


# CLASS: tests.application.test_sync_agent_docs.TestCommandReference
# SUMMARY: The command list is not rendered into the wrapper any more (regression guard below); the
# two version numbers still are, and still must come from their own files rather than be typed.
class TestCommandReference:
    # FUNCTION: test_python_versions_come_from_their_own_files
    # SUMMARY: The two version numbers are read from pyproject.toml and .python-version, not typed.
    @pytest.mark.unit
    def test_python_versions_come_from_their_own_files(self) -> None:
        minimum, toolchain = render_python_versions()

        assert f'requires-python = ">={minimum}"' in (ROOT_DIR / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        assert (ROOT_DIR / ".python-version").read_text(encoding="utf-8").strip() == toolchain

    # FUNCTION: test_no_static_command_catalogue_is_rendered
    # SUMMARY: Regression guard: a hand-rendered "Common Commands" catalogue used to duplicate the
    # Makefile's own annotations and could fall out of step with a renamed or deleted target
    # (test_gate_recipes.py::TestDocumentedTargetsRunWithoutAPrompt reads the Makefile directly, not
    # this wrapper, so it does not depend on this catalogue existing). `make help` reads the same
    # annotations live and cannot drift, so the wrapper points at it instead of duplicating it.
    @pytest.mark.unit
    def test_no_static_command_catalogue_is_rendered(self) -> None:
        claude_content = build_documents()[CLAUDE_PATH]

        assert "## Common Commands" not in claude_content
        assert "make help" in claude_content


# CLASS: tests.application.test_sync_agent_docs.TestSyncAgentDocs
# SUMMARY: Verify shared rules are embedded into the generated CLAUDE.md wrapper.
class TestSyncAgentDocs:
    # FUNCTION: test_build_documents_embeds_shared_rules
    # SUMMARY: Verify the generated root wrappers include the shared rules body and generation notice.
    @pytest.mark.unit
    def test_build_documents_embeds_shared_rules(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        shared_rules = tmp_path / "agent_rules.md"
        shared_rules.write_text(
            "# Shared Agent Wrapper Source\n\nWorking notes:\n- keep tests updated\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("scripts.sync_agent_docs.SHARED_RULES_PATH", shared_rules)

        documents = build_documents()

        assert len(documents) == 2
        claude_content = documents[CLAUDE_PATH]
        assert "Generated from `docs/agent_rules.md`" in claude_content
        assert "## Quick Start" in claude_content
        assert "make help" in claude_content
        assert "## Tech Stack" in claude_content
        # **LOGIC_STEP**: An "Architecture Reference" section used to sit here, pointing at
        # ARCHITECTURE.md. That file was folded into docs/agent_rules.md on 2026-08-11, so the
        # wrapper must now carry the contract rather than a pointer to a second document.
        assert "## Architecture Reference" not in claude_content
        assert "ARCHITECTURE.md" not in claude_content
        assert "- keep tests updated" in claude_content

    # FUNCTION: test_additional_sections_reach_the_wrapper
    # SUMMARY: Regression guard: the renderer used to copy only `Task process:` and `Working notes:` and silently drop every other section, so a rule added to the shared source never reached the agent meant to follow it.
    @pytest.mark.unit
    def test_additional_sections_reach_the_wrapper(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        shared_rules = tmp_path / "agent_rules.md"
        shared_rules.write_text(
            "# Shared Agent Wrapper Source\n\n"
            "Working notes:\n- keep tests updated\n\n"
            "Code versus memory:\n- put the why in memory, the what in the code\n\n"
            "Validator authoring conventions:\n- define stable rule_id constants\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("scripts.sync_agent_docs.SHARED_RULES_PATH", shared_rules)

        claude_content = build_documents()[CLAUDE_PATH]

        assert "## Code versus memory" in claude_content
        assert "- put the why in memory, the what in the code" in claude_content
        assert "## Validator authoring conventions" in claude_content
        assert "- define stable rule_id constants" in claude_content

    # FUNCTION: test_the_quick_start_comes_from_the_shared_source
    # SUMMARY: Regression guard: the Quick Start was a literal in the generator, so the one
    # instruction a project most needs to change was the one place it could not.
    @pytest.mark.unit
    def test_the_quick_start_comes_from_the_shared_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # **LOGIC_STEP**: The first Quick Start step named the shipped vertical, and
        # .agents/skills/add-vertical tells every project to delete that vertical. CLAUDE.md is generated
        # and the pre-edit hook refuses it, so the stale instruction could only be reached by
        # editing this generator — which the removal checklist never mentions, leaving a
        # hand-patched copy as the only way out.
        shared_rules = tmp_path / "agent_rules.md"
        shared_rules.write_text(
            "# Shared Agent Wrapper Source\n\n"
            "Start here:\n- Copy the `invoice` vertical, all four files of it.\n\n"
            "Working notes:\n- keep tests updated\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("scripts.sync_agent_docs.SHARED_RULES_PATH", shared_rules)

        claude_content = build_documents()[CLAUDE_PATH]

        assert "- Copy the `invoice` vertical, all four files of it." in claude_content
        assert "reference_task" not in claude_content.split("## Working Notes", 1)[0]
        # **LOGIC_STEP**: Once, not twice. "Start here" sits in _SECTIONS_RENDERED_SEPARATELY, and
        # the day it was excluded from the generic pass nothing else rendered it — the section
        # reached no wrapper at all while the tuple claimed someone else was handling it.
        assert claude_content.count("- Copy the `invoice` vertical") == 1

    # FUNCTION: test_the_quick_start_states_the_file_count_the_skill_states
    # SUMMARY: One number, two documents: the vertical's file count must not drift between them.
    @pytest.mark.unit
    def test_the_quick_start_states_the_file_count_the_skill_states(self) -> None:
        # **LOGIC_STEP**: The skill's heading went from nine files to eleven on 2026-08-13 and the
        # wrapper kept saying nine for a day short of a fortnight, because the two live in
        # different files and no gate compared them. Whichever number the skill's heading carries
        # is the one the shared source must repeat.
        skill = (_REPO_ROOT / ".agents" / "skills" / "add-vertical" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        shared_rules = (_REPO_ROOT / "docs" / "agent_rules.md").read_text(encoding="utf-8")

        heading = re.search(r"^## The (\w+) files of a vertical", skill, re.MULTILINE)
        quick_start = re.search(r"^Start here:\n((?:- .+\n?)+)", shared_rules, re.MULTILINE)

        assert heading is not None, ".agents/skills/add-vertical no longer states a file count"
        assert quick_start is not None, "docs/agent_rules.md no longer carries a Start here list"
        assert f"{heading.group(1)} files" in quick_start.group(1)

    # FUNCTION: test_working_notes_are_not_duplicated_as_a_section
    # SUMMARY: Sections the wrapper places by hand must not be emitted twice by the generic carry-over pass.
    @pytest.mark.unit
    def test_working_notes_are_not_duplicated_as_a_section(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        shared_rules = tmp_path / "agent_rules.md"
        shared_rules.write_text(
            "# Shared Agent Wrapper Source\n\n"
            "Task process:\n- branch before you edit\n\n"
            "Working notes:\n- keep tests updated\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("scripts.sync_agent_docs.SHARED_RULES_PATH", shared_rules)

        claude_content = build_documents()[CLAUDE_PATH]

        assert claude_content.count("- keep tests updated") == 1
        assert claude_content.count("- branch before you edit") == 1

    # FUNCTION: test_wrapper_states_one_final_command
    # SUMMARY: Regression guard: the renderer used to append a hardcoded `Finish with make quality-gates` below the one carried over from the shared source, telling the agent two different final commands three lines apart.
    @pytest.mark.unit
    def test_wrapper_states_one_final_command(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        shared_rules = tmp_path / "agent_rules.md"
        shared_rules.write_text(
            "# Shared Agent Wrapper Source\n\n"
            "Working notes:\n- Finish with `make quality-gates`.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("scripts.sync_agent_docs.SHARED_RULES_PATH", shared_rules)

        claude_content = build_documents()[CLAUDE_PATH]

        assert claude_content.count("Finish with") == 1

    # FUNCTION: test_main_check_json_reports_structured_drift_issue
    # SUMMARY: Verify JSON check mode emits stable remediation metadata when a generated wrapper is missing.
    @pytest.mark.unit
    def test_main_check_json_reports_structured_drift_issue(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        missing_path = tmp_path / "CLAUDE.md"
        monkeypatch.setattr(
            "scripts.sync_agent_docs.build_documents",
            lambda: {missing_path: "wrapper"},
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["scripts/sync_agent_docs.py", "--check", "--json"],
        )

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 1
        assert '"rule_id": "drift.agent_docs.missing"' in captured.out
        assert '"suggested_fix"' in captured.out

    # FUNCTION: test_section_lost_to_heading_punctuation_is_reported
    # SUMMARY: Verify a bullet section whose heading the renderer cannot match is reported instead of vanishing, and that a plain heading passes.
    @pytest.mark.unit
    def test_section_lost_to_heading_punctuation_is_reported(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # **LOGIC_STEP**: The renderer matches a plain `Title:` line only. A heading carrying bold,
        # a trailing period, or a colon inside emphasis silently drops its whole section, and the
        # drift check stays green because it compares the wrapper against the same lossy render.
        # That happened on 2026-08-04 to the rule about where a fact belongs, which never reached
        # CLAUDE.md, so the loss is now an error rather than a surprise.
        shared_rules = tmp_path / "agent_rules.md"
        shared_rules.write_text(
            "Working notes:\n- keep tests updated\n\nSome rule. **Bold heading:**\n- the bullet that must not vanish\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("scripts.sync_agent_docs.SHARED_RULES_PATH", shared_rules)
        monkeypatch.setattr("scripts.sync_agent_docs.CLAUDE_PATH", tmp_path / "CLAUDE.md")
        monkeypatch.setattr(sys, "argv", ["scripts/sync_agent_docs.py", "--json"])

        with pytest.raises(DroppedSectionError) as raised:
            build_documents()
        assert raised.value.headings == ["Some rule. **Bold heading:**"]

        exit_code = main()

        captured = capsys.readouterr()
        assert exit_code == 1
        assert f'"rule_id": "{DROPPED_SECTION_RULE_ID}"' in captured.out
        assert not (tmp_path / "CLAUDE.md").exists()

        shared_rules.write_text(
            "Working notes:\n- keep tests updated\n\nSome rule:\n- the bullet that must not vanish\n",
            encoding="utf-8",
        )
        documents = build_documents()
        assert "the bullet that must not vanish" in "\n".join(documents.values())
        assert dropped_section_headings(shared_rules.read_text(encoding="utf-8"), documents) == []


# CLASS: tests.application.test_sync_agent_docs.TestUnparsedHeadingIsRefused
# SUMMARY: A heading the renderer cannot read must stop generation, never produce a plausible wrapper built on a guess.
class TestUnparsedHeadingIsRefused:
    # FUNCTION: test_prose_between_working_notes_and_its_bullets_is_refused
    # SUMMARY: Regression guard: `Working notes:` fell back to every bullet in the file, so one prose line under the heading turned 20 rules into 49 with every gate green.
    @pytest.mark.unit
    def test_prose_between_working_notes_and_its_bullets_is_refused(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        shared_rules = tmp_path / "agent_rules.md"
        shared_rules.write_text(
            "Working notes:\nThese apply to every session.\n- keep tests updated\n\n"
            "How the kernel is shaped:\n- the domain imports nothing\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("scripts.sync_agent_docs.SHARED_RULES_PATH", shared_rules)
        monkeypatch.setattr("scripts.sync_agent_docs.CLAUDE_PATH", tmp_path / "CLAUDE.md")

        # **LOGIC_STEP**: Pinned against the check itself, not only against the raise. The dropped
        # net would report this one too, so asserting the exception alone would keep passing with
        # the heading check removed.
        assert malformed_section_headings(shared_rules.read_text(encoding="utf-8")) == [
            "Working notes:"
        ]

        with pytest.raises(DroppedSectionError) as raised:
            build_documents()

        assert raised.value.headings == ["Working notes:"]
        # **LOGIC_STEP**: The wrapper must not exist. The old fallback wrote one that read as a
        # complete rule set and carried the wrong rules, which is worse than no file at all.
        assert not (tmp_path / "CLAUDE.md").exists()

    # FUNCTION: test_prose_between_task_process_and_its_bullets_is_refused
    # SUMMARY: Regression guard: `Task process:` fell back to an empty string, and the whole `## Task Process` section left CLAUDE.md unannounced.
    @pytest.mark.unit
    def test_prose_between_task_process_and_its_bullets_is_refused(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        shared_rules = tmp_path / "agent_rules.md"
        shared_rules.write_text(
            "Task process:\nRun these in order.\n- branch before you edit\n\n"
            "Working notes:\n- keep tests updated\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("scripts.sync_agent_docs.SHARED_RULES_PATH", shared_rules)

        assert malformed_section_headings(shared_rules.read_text(encoding="utf-8")) == [
            "Task process:"
        ]

        with pytest.raises(DroppedSectionError) as raised:
            build_documents()

        assert raised.value.headings == ["Task process:"]

    # FUNCTION: test_prose_between_any_other_heading_and_its_bullets_is_reported
    # SUMMARY: Regression guard: the section-dropped net required heading and bullets on adjacent lines, so one prose line between them hid a whole section from CLAUDE.md and from the net.
    @pytest.mark.unit
    def test_prose_between_any_other_heading_and_its_bullets_is_reported(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        shared_rules = tmp_path / "agent_rules.md"
        shared_rules.write_text(
            "Working notes:\n- keep tests updated\n\n"
            "Where a fact goes:\nOne fact, one place.\n- the bullet that must not vanish\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("scripts.sync_agent_docs.SHARED_RULES_PATH", shared_rules)

        with pytest.raises(DroppedSectionError) as raised:
            build_documents()

        assert raised.value.headings == ["Where a fact goes:"]

    # FUNCTION: test_an_absent_heading_is_not_an_error
    # SUMMARY: A project that carries no task process at all is a legitimate source shape; only a heading that is present and unparsed is a failure.
    @pytest.mark.unit
    def test_an_absent_heading_is_not_an_error(self) -> None:
        source = "Working notes:\n- keep tests updated\n"

        assert malformed_section_headings(source) == []


# CLASS: tests.application.test_sync_agent_docs.TestBothWrappersStayReadable
# SUMMARY: Verify each generated wrapper still fits what the agent reading it will load.
class TestBothWrappersStayReadable:
    # FUNCTION: test_agents_md_fits_the_codex_budget
    # SUMMARY: Verify AGENTS.md stays under the size Codex reads by default.
    # **LOGIC_STEP**: Codex truncates the project doc at `project_doc_max_bytes`, 32 KiB by
    # default, and says nothing when it does — the rules past the cut simply are not there. The
    # file is generated, so it grows whenever docs/agent_rules.md does, and nothing else would
    # notice. Half the budget is the headroom; crossing it means splitting the source, not raising
    # the number here.
    @pytest.mark.unit
    def test_agents_md_fits_the_codex_budget(self) -> None:
        codex_default_budget = 32 * 1024
        size = len(build_documents()[AGENTS_PATH].encode("utf-8"))

        assert size < codex_default_budget, (
            f"AGENTS.md is {size} bytes; Codex reads only the first {codex_default_budget}"
        )

    # FUNCTION: test_the_two_wrappers_differ_only_where_they_must
    # SUMMARY: Verify one source really produces one contract, not two that drift.
    # **LOGIC_STEP**: The bodies are rendered from the same string, so any difference beyond the
    # file's own name is a bug in the renderer rather than a deliberate per-agent rule. Comparing
    # them with the names folded away is what makes that provable instead of assumed. The reader is
    # folded before the file name because one reader string contains the other file's name.
    @pytest.mark.unit
    def test_the_two_wrappers_differ_only_where_they_must(self) -> None:
        documents = build_documents()

        def fold(text: str) -> str:
            # **LOGIC_STEP**: Every wrapper name, not just this file's own — a future section could
            # name the sibling wrapper, and folding only one side would then report a difference
            # that is really the same sentence in both files.
            for path, reader in WRAPPER_READERS.items():
                text = text.replace(reader, "READER").replace(path.name, "WRAPPER")
            return text

        folded = [fold(content) for content in documents.values()]

        assert folded[0] == folded[1]
