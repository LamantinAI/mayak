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
    IMPORT_LINE,
    ROOT_DIR,
    DroppedSectionError,
    build_documents,
    dropped_section_headings,
    main,
    malformed_section_headings,
    render_python_versions,
)

# ATTRIBUTE: _REPO_ROOT (Path)
# SUMMARY: Repository root, for the tests that compare two hand-written documents against each other.
_REPO_ROOT = Path(__file__).resolve().parents[2]

# ATTRIBUTE: _IMPORT_TEXT (str)
# SUMMARY: The import line as literal text. Written out rather than read from the generator, so a
# guard cannot agree with a renderer that points the import at the wrong file.
_IMPORT_TEXT = "@AGENTS.md"

# ATTRIBUTE: _HTML_COMMENT (re.Pattern)
# SUMMARY: A block HTML comment, which Claude Code strips before the file reaches the model — so
# what the stub actually contributes to a session is whatever survives this substitution.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


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
        agents_content = build_documents()[AGENTS_PATH]

        assert "## Common Commands" not in agents_content
        assert "make help" in agents_content


# CLASS: tests.application.test_sync_agent_docs.TestSyncAgentDocs
# SUMMARY: Verify shared rules are embedded into the one generated wrapper, AGENTS.md.
class TestSyncAgentDocs:
    # FUNCTION: test_build_documents_embeds_shared_rules
    # SUMMARY: Verify the generated contract includes the shared rules body and generation notice.
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
        agents_content = documents[AGENTS_PATH]
        assert "Generated from `docs/agent_rules.md`" in agents_content
        assert "## Quick Start" in agents_content
        assert "make help" in agents_content
        assert "## Tech Stack" in agents_content
        # **LOGIC_STEP**: An "Architecture Reference" section used to sit here, pointing at
        # ARCHITECTURE.md. That file was folded into docs/agent_rules.md, so the wrapper must now
        # carry the contract rather than a pointer to a second document.
        assert "## Architecture Reference" not in agents_content
        assert "ARCHITECTURE.md" not in agents_content
        assert "- keep tests updated" in agents_content

    # FUNCTION: test_additional_sections_reach_the_wrapper
    # SUMMARY: Regression guard: dropping an unrecognized section from the shared source silently
    # drops it from the wrapper too, so a rule added there never reaches the agent meant to follow it.
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

        agents_content = build_documents()[AGENTS_PATH]

        assert "## Code versus memory" in agents_content
        assert "- put the why in memory, the what in the code" in agents_content
        assert "## Validator authoring conventions" in agents_content
        assert "- define stable rule_id constants" in agents_content

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

        agents_content = build_documents()[AGENTS_PATH]

        assert "- Copy the `invoice` vertical, all four files of it." in agents_content
        assert "reference_task" not in agents_content.split("## Working Notes", 1)[0]
        # **LOGIC_STEP**: Once, not twice. "Start here" sits in _SECTIONS_RENDERED_SEPARATELY, and
        # the day it was excluded from the generic pass nothing else rendered it — the section
        # reached no wrapper at all while the tuple claimed someone else was handling it.
        assert agents_content.count("- Copy the `invoice` vertical") == 1

    # FUNCTION: test_the_quick_start_states_the_file_count_the_skill_states
    # SUMMARY: One number, two documents: the vertical's file count must not drift between them.
    @pytest.mark.unit
    def test_the_quick_start_states_the_file_count_the_skill_states(self) -> None:
        # **LOGIC_STEP**: The skill's heading once went from nine files to eleven while the
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

        agents_content = build_documents()[AGENTS_PATH]

        assert agents_content.count("- keep tests updated") == 1
        assert agents_content.count("- branch before you edit") == 1

    # FUNCTION: test_wrapper_states_one_final_command
    # SUMMARY: Regression guard: a hardcoded "Finish with" line appended below one already carried
    # over from the shared source would tell the agent two different final commands.
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

        agents_content = build_documents()[AGENTS_PATH]

        assert agents_content.count("Finish with") == 1

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
        # That happened once to the rule about where a fact belongs, which never reached
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
# SUMMARY: Verify each generated file still delivers what the agent reading it will load.
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

    # FUNCTION: test_claude_md_carries_nothing_but_the_import
    # SUMMARY: Verify the stub adds no rule of its own, so no rule can reach one agent and not the other.
    # **LOGIC_STEP**: This replaces the check that compared two rendered bodies for equality. There
    # is one body now, so the drift it guarded against can only re-enter one way: a rule written
    # into CLAUDE.md below the import. Claude Code would read it and Codex never would. Strip what
    # Claude Code strips — the block comment — take the import out, and nothing may remain.
    @pytest.mark.unit
    def test_claude_md_carries_nothing_but_the_import(self) -> None:
        stub = build_documents()[CLAUDE_PATH]

        remainder = _HTML_COMMENT.sub("", stub).replace(_IMPORT_TEXT, "", 1).strip()

        assert remainder == "", (
            f"CLAUDE.md carries {remainder!r} beyond its import; a rule written there reaches "
            "Claude Code and never reaches Codex. It belongs in docs/agent_rules.md."
        )

    # FUNCTION: test_the_import_is_a_bare_unindented_line
    # SUMMARY: Verify the import is an import — quoted or indented it is prose, and the session loads no rules at all.
    # **LOGIC_STEP**: Claude Code skips `@` references inside code spans and fenced blocks, by
    # design, and four spaces of indentation make a block of the line just as surely as a fence
    # does — measured against the CLI, an indented import expands to nothing. So a well-meant edit
    # that quotes the line for readability, or indents it under a bullet, silently empties the
    # contract for every Claude session with every gate still green. This is the single point of
    # failure the whole arrangement rests on. The comparison is against the literal text and the
    # unstripped line: an earlier version read the renderer's own constant and stripped each line,
    # and passed on both `@CLAUDE.md` and a four-space indent.
    @pytest.mark.unit
    def test_the_import_is_a_bare_unindented_line(self) -> None:
        body = _HTML_COMMENT.sub("", build_documents()[CLAUDE_PATH])

        assert _IMPORT_TEXT in body.splitlines()
        assert f"`{_IMPORT_TEXT}`" not in body
        assert "```" not in body

    # FUNCTION: test_the_import_names_the_file_that_carries_the_rules
    # SUMMARY: Pin the import target as literal text, since the two guards above read what the same constant produced.
    # **LOGIC_STEP**: Both guards above compare the rendered stub against `_IMPORT_TEXT`. That is
    # only worth anything if `_IMPORT_TEXT` is the name the rules actually live under: pointing
    # `IMPORT_LINE` at CLAUDE.md itself would make the stub import itself, and every check that
    # reads the renderer's own constant would still agree with it.
    @pytest.mark.unit
    def test_the_import_names_the_file_that_carries_the_rules(self) -> None:
        assert IMPORT_LINE == _IMPORT_TEXT
        assert AGENTS_PATH.name in _IMPORT_TEXT
        assert AGENTS_PATH.exists()

    # FUNCTION: test_the_rules_body_is_rendered_once
    # SUMMARY: Verify a rule from the shared source reaches AGENTS.md and only AGENTS.md.
    @pytest.mark.unit
    def test_the_rules_body_is_rendered_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        shared_rules = tmp_path / "agent_rules.md"
        shared_rules.write_text(
            "# Shared Agent Wrapper Source\n\nWorking notes:\n- the one rule\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("scripts.sync_agent_docs.SHARED_RULES_PATH", shared_rules)

        documents = build_documents()

        carrying = [path for path, text in documents.items() if "- the one rule" in text]
        assert carrying == [AGENTS_PATH]
