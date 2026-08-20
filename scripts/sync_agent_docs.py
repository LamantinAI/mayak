#!/usr/bin/env python3
# FILE: sync_agent_docs.py
# SUMMARY: Generate or verify the agent instruction wrappers from the shared agent rules source.

from __future__ import annotations

import argparse
import re
from pathlib import Path
from ai_context.rendering import render_json


# ATTRIBUTE: ROOT_DIR (Path)
# SUMMARY: Absolute repository root containing the agent instruction files.
ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: SHARED_RULES_PATH (Path)
# SUMMARY: Canonical shared rules source consumed by the generated wrappers.
SHARED_RULES_PATH = ROOT_DIR / "docs" / "agent_rules.md"

# ATTRIBUTE: CLAUDE_PATH (Path)
# SUMMARY: Generated Claude Code instruction wrapper target.
CLAUDE_PATH = ROOT_DIR / "CLAUDE.md"

# ATTRIBUTE: AGENTS_PATH (Path)
# SUMMARY: The same rules under the name Codex and the wider agents.md convention read.
# **LOGIC_STEP**: Two files rather than one with a symlink. Codex reads AGENTS.md and never looks
# at CLAUDE.md; Claude Code reads CLAUDE.md and never looks at AGENTS.md. Neither is the source —
# docs/agent_rules.md is — so rendering both from it costs one dictionary entry and keeps the
# checkout free of a symlink, which git on Windows silently turns into a text file.
AGENTS_PATH = ROOT_DIR / "AGENTS.md"

# ATTRIBUTE: WRAPPER_READERS (dict[Path, str])
# SUMMARY: Which agent reads which generated wrapper, named in the wrapper's own opening line.
WRAPPER_READERS = {
    CLAUDE_PATH: "Claude Code",
    AGENTS_PATH: "Codex",
}

# ATTRIBUTE: MAKEFILE_PATH (Path)
# SUMMARY: The Makefile, single source of the command list rendered into the wrapper.
MAKEFILE_PATH = ROOT_DIR / "Makefile"

# ATTRIBUTE: _DOCUMENTED_TARGET_PATTERN (re.Pattern[str])
# SUMMARY: Matches `target: deps ## Group | description` lines in the Makefile.
_DOCUMENTED_TARGET_PATTERN = re.compile(
    r"^(?P<name>[a-z0-9-]+):[^#\n]*##\s*(?P<group>[^|]+?)\s*\|\s*(?P<description>.+?)\s*$",
    re.MULTILINE,
)


# FUNCTION: render_python_versions
# SUMMARY: Read the supported-runtime minimum and the dev toolchain version from their own files.
# OUTPUT: (tuple[str, str]): Minimum runtime version, and the version local tooling runs on.
# NOTE: These two numbers were written out by hand in three places — here, ARCHITECTURE.md, and
# ADR-002 — none of which was checked against pyproject.toml. Bumping requires-python would have
# left every document quietly wrong.
def render_python_versions() -> tuple[str, str]:
    pyproject = (ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^requires-python\s*=\s*">=([0-9.]+)"', pyproject, re.MULTILINE)
    if match is None:
        raise RuntimeError('pyproject.toml has no `requires-python = ">=X.Y"` entry to read.')
    minimum = match.group(1)
    toolchain = (ROOT_DIR / ".python-version").read_text(encoding="utf-8").strip()
    return minimum, toolchain


# ATTRIBUTE: _GROUP_ORDER (dict[str, int])
# SUMMARY: Presentation order for command groups; groups absent from here are appended after.
_GROUP_ORDER = {
    "Validation": 0,
    "Testing": 1,
    "Refresh generated artifacts": 2,
    "Reading a running service": 3,
    "Run": 4,
    "Scaffolding": 5,
}


# FUNCTION: render_command_reference
# SUMMARY: Build the Common Commands block from the Makefile's own documented targets.
# OUTPUT: (str): A fenced bash block grouped by the Makefile's `## Group |` annotations.
# NOTE: This list used to be a hardcoded string literal right here, and a second hand-written copy
# sat in ARCHITECTURE.md. Neither was checked against the Makefile, so a renamed or deleted target
# would have kept being advertised to the agent until it ran the command and got "No rule to make
# target". Reading the Makefile makes the Makefile the only place a command name is written down.
def render_command_reference() -> str:
    grouped: dict[str, list[tuple[str, str]]] = {}
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    for match in _DOCUMENTED_TARGET_PATTERN.finditer(makefile):
        grouped.setdefault(match["group"], []).append((match["name"], match["description"]))

    if not grouped:
        raise RuntimeError(
            f"No documented targets found in {_display_path(MAKEFILE_PATH)}. "
            "Annotate targets as `name: ## Group | description`."
        )

    # **LOGIC_STEP**: Present validation first — it is what an agent reaches for most — and let any
    # group the Makefile grows later fall in after the known ones, in the order it declares them.
    ordered = sorted(grouped, key=lambda group: (_GROUP_ORDER.get(group, len(_GROUP_ORDER)),))

    width = max(len(name) for entries in grouped.values() for name, _ in entries) + 2
    blocks = []
    for group in ordered:
        entries = grouped[group]
        lines = [f"# {group}"]
        lines += [f"make {name:<{width}}# {description}" for name, description in entries]
        blocks.append("\n".join(lines))
    return "```bash\n" + "\n\n".join(blocks) + "\n```"


# FUNCTION: _display_path
# SUMMARY: Render a path relative to the repository root when possible, otherwise return the absolute path.
# OUTPUT: (str): Repository-relative or absolute path string.
def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


# ATTRIBUTE: _HAND_PLACED_SECTION_TITLES (tuple[str, ...])
# SUMMARY: Section titles the wrapper extracts by name rather than through the generic carry-over pass.
# NOTE: "Start here" joined this tuple on 2026-08-14, and until then it was the worst shape in the
# file: it sat in _SECTIONS_RENDERED_SEPARATELY, which told the generic pass "someone else renders
# this", while nothing rendered it at all — the Quick Start below was a hand-typed literal saying
# something similar in different words. So the shared source carried a section that reached no
# wrapper, the dropped-section net could not see it (its items were numbered, not bulleted), and a
# project that deleted the shipped vertical could not fix the instruction anywhere: CLAUDE.md is
# generated and the pre-edit hook refuses it, which leaves hand-patching this generator as the only
# way out — and a patched generator is a fork of the thing that was supposed to be shared.
_HAND_PLACED_SECTION_TITLES = ("Start here", "Working notes", "Task process")


# FUNCTION: _extract_named_bullets
# SUMMARY: Return the bullet list under a plain `Title:` heading, or an empty string when the heading is absent from the source.
# OUTPUT: (str): The section's bullets, empty when the source carries no such heading.
# NOTE: Measured on 2026-08-13. This function used to answer a heading it could not parse with a
# guess instead of a refusal, and both guesses reached CLAUDE.md behind a green gate. `Working
# notes:` fell back to every bullet in the file, so one prose line under the heading swept the
# architecture, logging and fact-placement rules into the working notes; `Task process:` fell back
# to nothing, and that whole section left the wrapper. The section-dropped net did not fire in
# either case. Callers now ask malformed_section_headings first and never render a wrapper built on
# a guess; see build_documents. The three shapes are pinned in
# tests/application/test_sync_agent_docs.py::TestUnparsedHeadingIsRefused.
def _extract_named_bullets(shared_rules: str, title: str) -> str:
    match = re.search(rf"^{re.escape(title)}:\n((?:- .+\n?)+)", shared_rules, re.MULTILINE)
    if match:
        return match.group(1).rstrip()
    return ""


# FUNCTION: malformed_section_headings
# SUMMARY: Return every hand-placed heading that exists in the shared source but carries no bullet list the renderer can read.
# OUTPUT: (list[str]): Headings present but unparsed, in source order; empty when both parse or are absent.
def malformed_section_headings(shared_rules: str) -> list[str]:
    # **LOGIC_STEP**: An absent heading is a legitimate source shape — a project may carry no task
    # process at all — so only a heading that IS there and still fails to parse is an error. The
    # substring test rather than a line match is deliberate: `**Working notes:**` and `Working
    # notes:.` both contain the title and both defeat the strict pattern.
    malformed: list[str] = []
    for title in _HAND_PLACED_SECTION_TITLES:
        if f"{title}:" not in shared_rules:
            continue
        if not _extract_named_bullets(shared_rules, title):
            malformed.append(f"{title}:")
    return malformed


# FUNCTION: _extract_working_notes
# SUMMARY: Extract only the bullet-point working notes from the shared rules source.
def _extract_working_notes(shared_rules: str) -> str:
    return _extract_named_bullets(shared_rules, "Working notes")


# FUNCTION: _extract_task_process
# SUMMARY: Extract the task process section from the shared rules source.
# OUTPUT: (str): Extracted task process bullet points as a single string, or empty string.
def _extract_task_process(shared_rules: str) -> str:
    return _extract_named_bullets(shared_rules, "Task process")


# FUNCTION: _extract_quick_start
# SUMMARY: Extract the shared source's `Start here:` bullets, which become the wrapper's Quick Start.
# OUTPUT: (str): The bullets as written, or an empty string when the source carries no such section.
# NOTE: The bullets are carried over verbatim rather than renumbered into `1.` `2.` `3.`, which
# would read better in a section called Quick Start. Measured: `dropped_section_headings` audits
# rendering by asking whether each source bullet appears in the wrapper *as written*, leading `- `
# included, so renumbering makes a rendered section look dropped and the generator refuses to write
# at all. That net exists because two sections silently vanished from CLAUDE.md behind a green
# gate; keeping one shape end to end is cheaper than teaching it about a second one.
def _extract_quick_start(shared_rules: str) -> str:
    return _extract_named_bullets(shared_rules, "Start here")


# ATTRIBUTE: _NAMED_SECTION_PATTERN (re.Pattern[str])
# SUMMARY: Matches a `Title:` heading followed by a contiguous bullet list in the shared rules source.
_NAMED_SECTION_PATTERN = re.compile(r"^([A-Z][^:\n]*):\n((?:- .+\n?)+)", re.MULTILINE)


# FUNCTION: _candidate_sections
# SUMMARY: Pair every bullet block in the shared source with the nearest preceding line that looks like a section heading.
# OUTPUT: (list[tuple[str, str]]): (heading line, first bullet of the block that follows it).
# NOTE: This replaced a regex requiring the heading and its bullets to be adjacent lines. Measured
# on 2026-08-13: one prose line between them hid the whole section from the net, so a section could
# vanish from CLAUDE.md with every gate green. Blank lines and markdown
# `#` headings are skipped rather than treated as headings, and a prose line without a colon leaves
# the pending heading in place, which is exactly the shape that used to slip through.
def _candidate_sections(shared_rules: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading: str | None = None
    inside_block = False
    for line in shared_rules.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            if not inside_block and heading is not None:
                sections.append((heading, stripped))
            inside_block = True
            continue
        inside_block = False
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped:
            heading = stripped
    return sections


# ATTRIBUTE: DROPPED_SECTION_RULE_ID (str)
# SUMMARY: Reported when a bullet section exists in the shared source but reaches no generated wrapper.
DROPPED_SECTION_RULE_ID = "drift.agent_docs.section_dropped"


# CLASS: DroppedSectionError
# SUMMARY: Raised by build_documents when a bullet section in the shared source reaches no wrapper, so the loss can never be written to disk or reported as up to date.
class DroppedSectionError(RuntimeError):
    # FUNCTION: __init__
    # SUMMARY: Store the headings that were dropped and build a message naming every one of them.
    def __init__(self, headings: list[str]) -> None:
        # ATTRIBUTE: headings (list[str])
        # SUMMARY: Source headings whose bullet lists reached no generated wrapper.
        self.headings = headings
        super().__init__(
            "Section(s) in docs/agent_rules.md reach no generated wrapper: "
            + "; ".join(headings)
            + ". The renderer reads a plain `Title:` line with its bullet list on the very next "
            "line. Remove the emphasis, the trailing period or any other punctuation from the "
            "heading, and delete any prose line sitting between the heading and its first bullet."
        )


# ATTRIBUTE: _SECTIONS_RENDERED_SEPARATELY (frozenset[str])
# SUMMARY: Section titles the wrapper already places by hand, so the generic pass must not repeat them.
_SECTIONS_RENDERED_SEPARATELY = frozenset({"Task process", "Working notes", "Start here"})


# FUNCTION: _extract_additional_sections
# SUMMARY: Carry every other `Title:` bullet section from the shared rules into the wrapper, so adding a section to docs/agent_rules.md is enough to make an agent see it.
# OUTPUT: (str): Rendered markdown for the remaining sections, empty when there are none.
def _extract_additional_sections(shared_rules: str) -> str:
    # **LOGIC_STEP**: Before this pass the renderer copied exactly two known headings and silently
    # dropped everything else, so a rule added to the "shared source" never reached the agent that
    # was supposed to follow it — and the drift check stayed green because it compared the wrapper
    # against the same lossy render.
    rendered: list[str] = []
    for title, bullets in _NAMED_SECTION_PATTERN.findall(shared_rules):
        if title in _SECTIONS_RENDERED_SEPARATELY:
            continue
        rendered.append(f"## {title}\n\n{bullets.rstrip()}\n")
    if not rendered:
        return ""
    return "\n".join(rendered) + "\n"


# FUNCTION: _render_wrapper
# SUMMARY: Render one agent's wrapper. The body is identical for every reader — only the file's own
#          name and the reader it addresses differ, and both are read off the target path.
# INPUT: shared_rules (str): Contents of docs/agent_rules.md.
# INPUT: target (Path): Which wrapper is being written; its name appears in the rendered text.
# OUTPUT: (str): Rendered markdown for that wrapper.
def _render_wrapper(shared_rules: str, target: Path) -> str:
    name = target.name
    reader = WRAPPER_READERS[target]
    working_notes = _extract_working_notes(shared_rules)
    task_process = _extract_task_process(shared_rules)
    quick_start = _extract_quick_start(shared_rules)
    additional_sections = _extract_additional_sections(shared_rules)
    task_process_section = ""
    if task_process:
        task_process_section = f"## Task Process\n\n{task_process}\n\n"
    python_minimum, python_toolchain = render_python_versions()
    return (
        f"# {name}\n\n"
        f"The shared Mayak agent rules, rendered for {reader}.\n\n"
        "Generated from `docs/agent_rules.md` via `scripts/sync_agent_docs.py`. Do not edit this file manually.\n\n"
        # **LOGIC_STEP**: These four steps were a literal in this file until 2026-08-14, and the
        # first of them named the shipped vertical. A project that deleted that vertical — which
        # .agents/skills/add-vertical tells it to do — could not fix the instruction: CLAUDE.md is
        # generated, the pre-edit hook refuses it, and the removal checklist never mentions this
        # generator, which leaves hand-patching the only way out. It now comes from the
        # `Start here:` bullets of docs/agent_rules.md, the file every other rule already lives in
        # and the one the wrapper tells the reader to edit.
        "## Quick Start\n\n"
        f"{quick_start}\n\n"
        "Running the service needs one more decision: the shipped `.env` points at PostgreSQL with\n"
        "placeholder credentials, so `make run-local` fails until either a database is up\n"
        "(`docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d db`) or the\n"
        "project declares it needs none (`POSTGRES_ENABLED=false` in `.env`).\n\n"
        # **LOGIC_STEP**: There is no second document to point at. An "Architecture Reference"
        # section used to send the reader to ARCHITECTURE.md; that file was folded into
        # docs/agent_rules.md on 2026-08-11, after a rule already present in this generated wrapper
        # was proposed for reversal anyway — a pointer to a second file was never going to reach an
        # agent better than the file that is already loaded in every session.
        "## Common Commands\n\n"
        f"{render_command_reference()}\n\n"
        f"{task_process_section}"
        "## Working Notes\n\n"
        f"{working_notes}\n"
        # **LOGIC_STEP**: Only the generated-file warning is appended here. A second hardcoded
        # "Finish with ..." line used to sit below it and contradicted the one carried over from
        # docs/agent_rules.md, leaving the wrapper telling the agent two different final commands.
        f"- `{name}` is a generated file and must not be edited directly. "
        "Edit `docs/agent_rules.md` instead, then run `make refresh-agent-docs`.\n\n"
        f"{additional_sections}"
        "## Tech Stack\n\n"
        f"- **Python**: {python_minimum}+ (runtime min), {python_toolchain} (dev toolchain)\n"
        "- **Package manager**: uv\n"
        "- **Framework**: FastAPI + Uvicorn\n"
        # Runtime data access is psycopg against the shared AsyncConnectionPool built in
        # composition_root.py. SQLAlchemy is present only so Alembic has metadata to diff
        # against; validate_runtime_ownership.py keeps create_async_engine out of the runtime.
        # The old "SQLAlchemy 2.0 async" line sent agents looking for an ORM session that
        # does not exist and has no worked example.
        "- **Database**: PostgreSQL — psycopg at runtime; SQLAlchemy only as Alembic metadata\n"
        "- **Migrations**: Alembic\n"
        "- **LLM**: OpenAI-compatible API via langchain-openai (no graph library ships by default)\n"
        "- **Observability**: semantic NDJSON logging with trace trees\n"
        "- **Linting**: ruff (format + check)\n"
        f"- **Type checking**: mypy (target Python {python_minimum})\n"
        "- **Testing**: pytest + pytest-asyncio + httpx\n"
    )


# FUNCTION: build_documents
# SUMMARY: Build the generated wrapper contents for every agent-facing file.
# OUTPUT: (dict[Path, str]): Mapping from output path to rendered markdown content.
def build_documents() -> dict[Path, str]:
    shared_rules = SHARED_RULES_PATH.read_text(encoding="utf-8")
    # **LOGIC_STEP**: A heading the extractors cannot read is refused before anything is rendered,
    # because the wrapper built on top of it looks plausible and is wrong — the working notes swell
    # with every bullet in the file, or the task process disappears, and the section-dropped net
    # below cannot see either one.
    malformed = malformed_section_headings(shared_rules)
    if malformed:
        raise DroppedSectionError(malformed)
    documents: dict[Path, str] = {
        target: _render_wrapper(shared_rules, target) for target in WRAPPER_READERS
    }
    dropped = dropped_section_headings(shared_rules, documents)
    if dropped:
        raise DroppedSectionError(dropped)
    return documents


# FUNCTION: dropped_section_headings
# SUMMARY: Return the heading of every bullet section present in the shared source whose bullets reach no generated wrapper.
# OUTPUT: (list[str]): Headings that were silently dropped, in source order; empty when every section landed.
def dropped_section_headings(shared_rules: str, documents: dict[Path, str]) -> list[str]:
    # **LOGIC_STEP**: The strict pattern that drives rendering matches a plain `Title:` line. Any
    # other punctuation on that line — bold, a trailing period, a colon inside emphasis — makes it
    # miss, and the section vanishes with the drift check still green, because the check compares
    # the wrapper against the same lossy render. This is that failure recurring: the rule about
    # where a fact belongs was added on 2026-08-04 under a bold heading and never reached CLAUDE.md.
    # So rendering is now audited by a separate, deliberately loose net that only asks whether the
    # bullets arrived anywhere at all.
    rendered_text = "\n".join(documents.values())
    dropped: list[str] = []
    for heading, first_bullet in _candidate_sections(shared_rules):
        if first_bullet not in rendered_text and heading not in dropped:
            dropped.append(heading)
    return dropped


# FUNCTION: main
# SUMMARY: Generate the agent wrappers or verify that the checked-in copies are current.
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify the agent wrappers from docs/agent_rules.md."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that the generated wrappers are up to date without rewriting them.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable check results.",
    )
    args = parser.parse_args()

    try:
        rendered_documents = build_documents()
    except DroppedSectionError as error:
        issue = {
            "rule_id": DROPPED_SECTION_RULE_ID,
            "category": "drift",
            "file": _display_path(SHARED_RULES_PATH),
            "line": 1,
            "message": str(error),
            "suggested_fix": "Rewrite the heading as a plain `Title:` line, then regenerate.",
            "read_first": [
                "docs/agent_rules.md",
                "scripts/sync_agent_docs.py",
            ],
            "next_commands": [
                "uv run python scripts/sync_agent_docs.py",
                "uv run python scripts/sync_agent_docs.py --check",
            ],
        }
        if args.json:
            print(render_json({"status": "error", "issues": [issue]}), end="")
        else:
            print(issue["message"])
        return 1
    if args.check:
        for path, rendered in rendered_documents.items():
            if not path.exists():
                issue = {
                    "rule_id": "drift.agent_docs.missing",
                    "category": "drift",
                    "file": _display_path(path),
                    "line": 1,
                    "message": f"Missing generated file: {_display_path(path)}",
                    "suggested_fix": "Regenerate the agent wrappers from docs/agent_rules.md.",
                    "read_first": [
                        "docs/agent_rules.md",
                        "CLAUDE.md",
                    ],
                    "next_commands": [
                        "uv run python scripts/sync_agent_docs.py",
                        "uv run python scripts/sync_agent_docs.py --check",
                    ],
                }
                if args.json:
                    print(render_json({"status": "error", "issues": [issue]}), end="")
                else:
                    print(issue["message"])
                return 1
            if path.read_text(encoding="utf-8") != rendered:
                issue = {
                    "rule_id": "drift.agent_docs.outdated",
                    "category": "drift",
                    "file": _display_path(path),
                    "line": 1,
                    "message": f"Outdated generated file: {_display_path(path)}",
                    "suggested_fix": "Regenerate CLAUDE.md after changing docs/agent_rules.md.",
                    "read_first": [
                        "docs/agent_rules.md",
                        "CLAUDE.md",
                    ],
                    "next_commands": [
                        "uv run python scripts/sync_agent_docs.py",
                        "uv run python scripts/sync_agent_docs.py --check",
                    ],
                }
                if args.json:
                    print(render_json({"status": "error", "issues": [issue]}), end="")
                else:
                    print(issue["message"])
                return 1
        if args.json:
            print(render_json({"status": "ok", "issues": []}), end="")
        else:
            print(f"{len(rendered_documents)} agent wrappers are up to date.")
        return 0

    for path, rendered in rendered_documents.items():
        path.write_text(rendered, encoding="utf-8")
        print(f"Wrote {_display_path(path)}")
    return 0


# FUNCTION: __main__
# SUMMARY: Script entrypoint for agent wrapper generation and verification.
if __name__ == "__main__":
    raise SystemExit(main())
