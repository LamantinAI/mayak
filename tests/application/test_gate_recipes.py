# FILE: tests/application/test_gate_recipes.py
# SUMMARY: Guard the Makefile recipes whose defect is what they do NOT do — a gate that measures
# less than it claims, or that keeps a second copy of a definition that already exists elsewhere.

from __future__ import annotations

import json
import re
from pathlib import Path
from subprocess import run

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ATTRIBUTE: _SHARED_GATE_TARGETS (tuple[str, ...])
# SUMMARY: The tool steps that must exist as their own target, because more than one caller runs them.
_SHARED_GATE_TARGETS = ("gate-lockfile", "gate-lint", "gate-format", "gate-types", "gate-tests")


# FUNCTION: _recipe
# SUMMARY: Return the recipe lines of one Makefile target.
# INPUT: target (str): Target name, without the colon.
# OUTPUT: (list[str]): Recipe lines, tabs and leading `@` stripped.
def _recipe(target: str) -> list[str]:
    makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    body = makefile.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]
    return [line.strip().lstrip("@") for line in body.splitlines() if line.startswith("\t")]


# CLASS: tests.application.test_gate_recipes.TestDiffCoverageAnnouncesWhatItCannotSee
# SUMMARY: Verify the diff-coverage gate warns about the files it is structurally blind to.
# NOTE: diff-cover reads `git diff`, which never lists an untracked file — and a new vertical is
# mostly untracked files. Measured: 17 lines across 4 files before committing, 176 across 9 after
# the same commit. The gate whose whole purpose is uncovered NEW code was measuring a tenth of it
# and reporting a pass.
class TestDiffCoverageAnnouncesWhatItCannotSee:
    # FUNCTION: test_the_recipe_lists_untracked_sources_before_measuring
    # SUMMARY: Verify the recipe asks git for untracked sources and says they are unmeasured.
    @pytest.mark.unit
    def test_the_recipe_lists_untracked_sources_before_measuring(self) -> None:
        recipe = "\n".join(_recipe("diff-coverage"))

        assert "git ls-files --others --exclude-standard" in recipe
        assert "WARNING" in recipe
        # **LOGIC_STEP**: The warning has to name the way out. `git add` is enough — `git diff HEAD`
        # lists a staged addition — and telling the reader to commit instead would be wrong advice
        # in the middle of a change they are not finished with.
        assert "git add" in recipe

    # FUNCTION: test_the_warning_runs_before_the_measurement
    # SUMMARY: Verify the warning cannot be buried under the test output it precedes.
    @pytest.mark.unit
    def test_the_warning_runs_before_the_measurement(self) -> None:
        lines = _recipe("diff-coverage")
        warning_index = next(i for i, line in enumerate(lines) if "ls-files --others" in line)
        measure_index = next(i for i, line in enumerate(lines) if "diff-cover" in line)

        assert warning_index < measure_index


# CLASS: tests.application.test_gate_recipes.TestToolStepsHaveOneDefinition
# SUMMARY: Verify no caller re-spells a gate step instead of calling the target that owns it.
# NOTE: scripts/doctor_layers.py runs these targets to diagnose a failed gate. A second spelling
# of `ruff check $(PYTHON_SOURCES)` anywhere means the doctor and the gate can disagree about what
# the step is — which is the class of defect this repository keeps paying for.
class TestToolStepsHaveOneDefinition:
    # FUNCTION: test_each_shared_step_exists_as_its_own_target
    # SUMMARY: Verify the targets the doctor invokes are actually declared.
    @pytest.mark.unit
    @pytest.mark.parametrize("target", _SHARED_GATE_TARGETS)
    def test_each_shared_step_exists_as_its_own_target(self, target: str) -> None:
        makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")

        assert f"\n{target}:" in makefile
        assert _recipe(target), f"{target} is declared with an empty recipe"

    # FUNCTION: test_no_aggregate_target_respells_a_shared_step
    # SUMMARY: Verify the aggregates delegate rather than repeat the tool invocation.
    @pytest.mark.unit
    @pytest.mark.parametrize("aggregate", ["quality-gates-steps"])
    def test_no_aggregate_target_respells_a_shared_step(self, aggregate: str) -> None:
        owned = {line for target in _SHARED_GATE_TARGETS for line in _recipe(target)}
        repeated = [
            line
            for line in _recipe(aggregate)
            # **LOGIC_STEP**: Compared against the targets' own recipe lines, so this keeps
            # holding if a flag changes — it is the duplication that is forbidden, not a literal.
            if line in owned
        ]

        assert repeated == [], (
            f"{aggregate} repeats a step that already has a target: {repeated}. "
            "Call `$(MAKE) --no-print-directory gate-<name>` instead."
        )

    # FUNCTION: test_the_doctor_names_targets_that_exist
    # SUMMARY: Verify every make target scripts/doctor_layers.py invokes is declared.
    @pytest.mark.unit
    def test_the_doctor_names_targets_that_exist(self) -> None:
        source = (_REPO_ROOT / "scripts" / "doctor_layers.py").read_text(encoding="utf-8")
        makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        named = set(re.findall(r'"(gate-[a-z]+)"', source))

        assert named, "the doctor names no gate target at all"
        missing = sorted(target for target in named if f"\n{target}:" not in makefile)
        assert missing == [], f"the doctor would run targets that do not exist: {missing}"

    # FUNCTION: test_no_file_outside_the_makefile_respells_a_tool_step
    # SUMMARY: Verify no CI job, generator or doc hands anyone a second spelling of a gate step.
    # NOTE: Restricting this check to the Makefile is how three stale copies survived unnoticed:
    # .github/workflows/ci.yml ran mypy over four roots where the gate covers five and installed
    # it with `--with mypy` instead of the pin; ai_context/build_context_map.py handed an agent a
    # lint command still naming top-level script paths that moved into scripts/ long ago, and a
    # type check covering `project` alone. Each was a command someone could run and believe.
    # **LOGIC_STEP**: Only the two steps that carry a source list. `uv lock --check` and
    # `run_all_tests.py --skip-functional` take no arguments that can go stale, and they appear
    # legitimately as recommended commands in the change map, the file policy and skill prose —
    # forbidding them there would be a rule with no defect behind it.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("tool", "pattern"),
        [
            ("ruff", re.compile(r"(?:uv run |uv run --with ruff )ruff (?:check|format)\s+\w")),
            ("mypy", re.compile(r"(?:uv run |uv run --with mypy )mypy\s+\w")),
        ],
    )
    def test_no_file_outside_the_makefile_respells_a_tool_step(
        self, tool: str, pattern: re.Pattern[str]
    ) -> None:
        # **LOGIC_STEP**: The Makefile is where these belong, and this test file quotes them in
        # its own patterns, so both are excluded. Everything else — CI, generators, skills, docs —
        # must go through the target.
        listed = run(
            ["git", "ls-files", "-z"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
        )
        exempt = {"Makefile", "tests/application/test_gate_recipes.py"}
        offenders: list[str] = []
        for name in listed.stdout.split("\0"):
            if not name or name in exempt:
                continue
            path = _REPO_ROOT / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line in text.splitlines():
                # **LOGIC_STEP**: A comment quoting the old command is documentation of what not
                # to run, and a commented-out command is not a second spelling — nothing executes
                # either. Without this the guard failed on the very comment explaining the fix.
                if line.lstrip().startswith("#"):
                    continue
                if pattern.search(line):
                    offenders.append(f"{name}: {line.strip()[:110]}")

        assert offenders == [], (
            f"a second spelling of the {tool} step lives outside the Makefile: {offenders}. "
            "Call the gate-* target instead."
        )


# ATTRIBUTE: _CI_JOB_TO_LOCAL_COVER (dict[str, str])
# SUMMARY: Every job in .github/workflows/ci.yml and the local command that covers it.
# NOTE: The value is either a `make` target `ci-local` runs as one of its lanes, or the sentinel
# below for a job whose check is a step inside `make quality-gates` rather than a lane of its own.
# This table is the coupling between the pipeline and the local command — the thing that was a
# comment in the Makefile until 2026-08-14 and therefore checked by nobody.
_INSIDE_THE_GATE_SUITE = "inside make quality-gates"
_CI_JOB_TO_LOCAL_COVER: dict[str, str] = {
    "quality-gates": "quality-gates",
    "quality-gates-without-postgres": "quality-gates",
    "functional-tests": "test-e2e",
    "security": "security-scan",
    "dependency-audit": "audit-deps",
    "diff-coverage": "diff-coverage",
    "secret-scan": _INSIDE_THE_GATE_SUITE,
    "type-check": _INSIDE_THE_GATE_SUITE,
    "unit-tests": _INSIDE_THE_GATE_SUITE,
    "infrastructure-tests": _INSIDE_THE_GATE_SUITE,
}


# FUNCTION: _ci_job_names
# SUMMARY: Read the job names out of the workflow, ignoring the trigger block above `jobs:`.
# OUTPUT: (set[str]): Job identifiers declared under `jobs:`.
def _ci_job_names() -> set[str]:
    workflow = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    # **LOGIC_STEP**: `on:` declares `push:` at the same indentation as a job, so a regex over the
    # whole file reports a job named "push". Cut at the `jobs:` key first.
    _, _, jobs_block = workflow.partition("\njobs:\n")
    return {match.group(1) for match in re.finditer(r"(?m)^  ([a-z0-9-]+):\s*$", jobs_block)}


# CLASS: tests.application.test_gate_recipes.TestPipelineAndLocalCommandCoverTheSameGround
# SUMMARY: Verify `make ci-local` still answers "would the pipeline pass?" — the claim it is named for.
# NOTE: The pipeline was believed dead from 2026-08-06 to 2026-08-14 and kept running the whole
# time, green, on every push to main. A workflow nobody watches drifts from the local command that
# claims to mirror it, and the drift is invisible precisely because both are green. Two of these
# tests exist so adding or removing a job cannot be a silent decision.
class TestPipelineAndLocalCommandCoverTheSameGround:
    # FUNCTION: test_every_ci_job_has_a_declared_local_cover
    # SUMMARY: Verify no job runs in the pipeline without a decision about how it runs locally.
    @pytest.mark.unit
    def test_every_ci_job_has_a_declared_local_cover(self) -> None:
        jobs = _ci_job_names()

        assert jobs, "no jobs parsed out of ci.yml — the parser, not the workflow, is broken"
        # **LOGIC_STEP**: What this can prove is coverage of the job LIST, both ways. What it
        # cannot prove is that a job and its lane run equivalent commands; that claim lives in the
        # table above and is a human's to keep true.
        assert sorted(jobs) == sorted(_CI_JOB_TO_LOCAL_COVER), (
            "ci.yml and the local-cover table disagree. Add the new job to "
            "_CI_JOB_TO_LOCAL_COVER with the lane that covers it, or add a lane to `ci-local`."
        )

    # FUNCTION: test_every_named_lane_exists_in_ci_local
    # SUMMARY: Verify each make target the table names is actually a lane of the ci-local recipe.
    @pytest.mark.unit
    def test_every_named_lane_exists_in_ci_local(self) -> None:
        # **LOGIC_STEP**: Only the lines that invoke something count. This test read the whole
        # recipe as one string until 2026-08-14, and every lane is preceded by an
        # `@echo "===> 1/6 quality-gates"` banner naming it — so deleting the invocation and
        # leaving the banner kept the test green while the lane stopped running. Measured: removing
        # the `$(MAKE) ... quality-gates` line passed. The echo lines are stripped first.
        invocations = "\n".join(
            line for line in _recipe("ci-local") if not line.startswith("echo ")
        )
        named_targets = {
            target for target in _CI_JOB_TO_LOCAL_COVER.values() if target != _INSIDE_THE_GATE_SUITE
        }

        missing = sorted(target for target in named_targets if target not in invocations)
        assert missing == [], (
            f"the table maps a CI job onto a lane `ci-local` does not run: {missing}"
        )

    # FUNCTION: test_every_announced_lane_actually_runs_something
    # SUMMARY: Verify the recipe runs one command per lane it announces, and as many as it counts.
    @pytest.mark.unit
    def test_every_announced_lane_actually_runs_something(self) -> None:
        # **LOGIC_STEP**: The test above cannot see a deleted lane whose target name still appears
        # elsewhere in the recipe — `quality-gates` runs twice, once per Postgres mode, so removing
        # the first invocation leaves the name behind and every assertion green. Measured on
        # 2026-08-14. What no substring check can fake is the arithmetic: the recipe announces
        # `===> i/N` for each lane, so N banners must be matched by N commands.
        # **LOGIC_STEP**: `_recipe` strips the leading `@`, so a banner arrives as
        # `echo "===> 1/6 quality-gates"`.
        lines = [line for line in _recipe("ci-local") if line]
        banners = re.findall(r'echo "===> (\d+)/(\d+) ', "\n".join(lines))
        commands = [line for line in lines if not line.startswith("echo ")]

        assert banners, "ci-local no longer announces its lanes; this test reads those banners"
        # **LOGIC_STEP**: Three claims the recipe makes about itself, each checkable: the lanes are
        # numbered from one without a gap, every banner agrees on the total, and the total is the
        # number of commands. The closing `===> ci-local: all lanes passed` line carries no
        # numbering and is not a lane, which is why the pattern requires `i/N`.
        assert [index for index, _ in banners] == [str(n) for n in range(1, len(banners) + 1)]
        assert {total for _, total in banners} == {str(len(banners))}, (
            f"the lane banners disagree about how many lanes there are: {sorted(banners)}"
        )
        assert len(commands) == len(banners), (
            f"ci-local announces {len(banners)} lanes and runs {len(commands)} commands"
        )

    # FUNCTION: test_both_gate_jobs_refuse_a_stale_generated_artifact
    # SUMMARY: Verify the pipeline runs the gates in strict mode, as `ci-local` does.
    @pytest.mark.unit
    def test_both_gate_jobs_refuse_a_stale_generated_artifact(self) -> None:
        # **LOGIC_STEP**: Locally the gate regenerates a stale artifact and prints a notice,
        # because the fix is always the same command. In the pipeline there is nobody to rerun the
        # generator, so the same situation has to fail instead — otherwise "CI is green" and "the
        # local gate is green" stop meaning the same thing. Measured on 2026-08-14: the flag was
        # documented in the Makefile as something the pipeline sets, and the pipeline did not.
        workflow = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        strict_lanes = workflow.count('STRICT_GENERATED: "1"')

        assert strict_lanes == 2, (
            f"expected both quality-gates jobs to set STRICT_GENERATED, found {strict_lanes}"
        )


# CLASS: tests.application.test_gate_recipes.TestDocumentedTargetsRunWithoutAPrompt
# SUMMARY: Verify every target the command list advertises is pre-approved in .claude/settings.json.
# NOTE: `make migrate` was documented in the generated command list for months and missing from the
# permission allowlist — the one target of nineteen that stopped an agent mid-task with a permission
# prompt. Two lists of the same commands, kept in step by nobody, is the same defect class the
# GENERATED_PATHS list already fixed for the hooks.
class TestDocumentedTargetsRunWithoutAPrompt:
    # FUNCTION: test_every_documented_target_is_pre_approved
    # SUMMARY: Verify the help annotations and the permission allowlist name the same targets.
    @pytest.mark.unit
    def test_every_documented_target_is_pre_approved(self) -> None:
        # **LOGIC_STEP**: The help annotations are the source of the command list in CLAUDE.md, so
        # this compares what an agent is told to run against what it may run without asking. A
        # target that should stay behind a prompt belongs in the exemption tuple below with the
        # reason written down — not silently absent from one of the two lists.
        exempt_from_pre_approval: tuple[str, ...] = ()
        makefile = (_REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        settings = json.loads(
            (_REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
        )

        documented = {
            match.group(1) for match in re.finditer(r"(?m)^([a-z0-9-]+):[^#\n]*##", makefile)
        }
        approved = {
            match.group(1)
            for rule in settings["permissions"]["allow"]
            if (match := re.match(r"Bash\(make ([a-z0-9-]+)", rule))
        }

        assert documented, "no target carries a help annotation"
        missing = sorted(documented - approved - set(exempt_from_pre_approval))
        assert missing == [], (
            f"documented but not pre-approved, so an agent hits a permission prompt: {missing}. "
            "Add each to permissions.allow in .claude/settings.json."
        )


# CLASS: tests.application.test_gate_recipes.TestGeneratedArtifactsAreRefreshedNotReported
# SUMMARY: Verify the local gate fixes stale generated artifacts and the strict one still refuses them.
# NOTE: Measured. An A/B measurement ran the same task twice, and the ONLY red gate either arm
# saw was `drift.generated.outdated` — a generated map left behind by an edit, fixed by the same
# command both times. Two out of two, plus three more in one session. A
# step whose remedy is always the identical command is a ritual, not a check.
class TestGeneratedArtifactsAreRefreshedNotReported:
    # FUNCTION: test_the_gate_refreshes_before_it_checks
    # SUMMARY: Verify `quality-gates` regenerates before running the steps that check generation.
    @pytest.mark.unit
    def test_the_gate_refreshes_before_it_checks(self) -> None:
        recipe = _recipe("quality-gates")

        assert any("refresh-generated-unless-strict" in line for line in recipe), (
            f"quality-gates must refresh generated artifacts before checking them; recipe: {recipe}"
        )
        refresh_at = next(
            index for index, line in enumerate(recipe) if "refresh-generated-unless-strict" in line
        )
        steps_at = next(index for index, line in enumerate(recipe) if "quality-gates-steps" in line)
        assert refresh_at < steps_at, "the refresh has to happen before the checks, not after"

    # FUNCTION: test_ci_local_asks_for_the_strict_behaviour
    # SUMMARY: Verify the pre-push command still fails on stale artifacts instead of fixing them.
    @pytest.mark.unit
    def test_ci_local_asks_for_the_strict_behaviour(self) -> None:
        # **LOGIC_STEP**: ci-local answers "would the pipeline pass?". There a stale artifact is a
        # failure — fixing it on the fly would hide exactly what the pipeline would reject.
        # **LOGIC_STEP**: Lines that INVOKE the gate, not the `echo` announcing the lane — the
        # first version of this matched the progress messages and failed on its own banner.
        gate_lanes = [
            line for line in _recipe("ci-local") if "quality-gates" in line and "$(MAKE)" in line
        ]

        assert gate_lanes, "ci-local no longer runs quality-gates at all"
        assert all("STRICT_GENERATED=1" in line for line in gate_lanes), (
            f"every ci-local gate lane must be strict about generated artifacts: {gate_lanes}"
        )

    # FUNCTION: test_the_hook_reads_the_generated_paths_from_the_makefile
    # SUMMARY: Verify the pre-commit hook does not keep a second copy of the list.
    @pytest.mark.unit
    def test_the_hook_reads_the_generated_paths_from_the_makefile(self) -> None:
        # **LOGIC_STEP**: The hook's own copy had drifted once — it named six of the eight outputs,
        # so a regenerated file went unstaged and the commit passed with the output left behind.
        hook = (_REPO_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")

        assert "make -s print-generated-paths" in hook
        assert "docs/ai_context_map.json docs/ai_change_map.json" not in hook

    # FUNCTION: test_every_declared_generated_path_exists
    # SUMMARY: Verify the list names real outputs, so the refresh and the hook act on something.
    @pytest.mark.unit
    def test_every_declared_generated_path_exists(self) -> None:
        listed = run(
            ["make", "-s", "print-generated-paths"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        paths = listed.stdout.split()

        assert len(paths) >= 5
        missing = [name for name in paths if not (_REPO_ROOT / name).exists()]
        assert missing == [], f"GENERATED_PATHS names things that do not exist: {missing}"


# CLASS: tests.application.test_gate_recipes.TestGeneratedFilesRefuseTheEdit
# SUMMARY: Verify the PreToolUse hook is installed and actually denies a write to every generated
# path, running the hook rather than reading it.
# NOTE: "CLAUDE.md is a generated file and must not be edited directly" is stated twice in CLAUDE.md
# and, until this hook, enforced nowhere. The drift check used to catch it after the fact; once the
# gate learned to refresh generated artifacts itself, a hand edit is simply overwritten on the next
# run and the work disappears silently. The hook is the only place the agent still learns anything.
class TestGeneratedFilesRefuseTheEdit:
    # FUNCTION: _decision
    # SUMMARY: Run the hook against one absolute path and return its decision, or None for allow.
    # INPUT: path (str): Absolute path the tool would write to.
    # INPUT: cwd (Path): Repository the hook resolves its generated list from.
    # OUTPUT: (str | None): The permissionDecision, or None when the hook stayed silent.
    @staticmethod
    def _decision(path: str, cwd: Path = _REPO_ROOT) -> str | None:
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": path}})
        result = run(
            [str(_REPO_ROOT / ".agents" / "hooks" / "pre-edit-guard.sh")],
            input=payload,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        if not result.stdout.strip():
            return None
        return str(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"])

    # FUNCTION: test_the_hook_is_registered_for_every_writing_tool
    # SUMMARY: Verify settings.json wires the guard to Edit, Write and MultiEdit.
    @pytest.mark.unit
    def test_the_hook_is_registered_for_every_writing_tool(self) -> None:
        settings = json.loads(
            (_REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        entries = settings["hooks"]["PreToolUse"]
        commands = [hook["command"] for entry in entries for hook in entry["hooks"]]

        assert ".agents/hooks/pre-edit-guard.sh" in commands
        matchers = [entry["matcher"] for entry in entries]
        assert any(
            {"Edit", "Write", "MultiEdit"} <= set(matcher.split("|")) for matcher in matchers
        ), f"the guard must cover every writing tool; matchers are {matchers}"

    # FUNCTION: test_every_generated_path_is_refused
    # SUMMARY: Verify the hook denies each path `make print-generated-paths` names.
    @pytest.mark.unit
    def test_every_generated_path_is_refused(self) -> None:
        listed = run(
            ["make", "-s", "print-generated-paths"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        allowed = [
            name
            for name in listed.stdout.split()
            if self._decision(str(_REPO_ROOT / name)) != "deny"
        ]

        assert allowed == [], f"the guard let a generated path through: {allowed}"

    # FUNCTION: test_a_file_inside_a_generated_directory_is_refused
    # SUMMARY: Verify the prefix case, not only exact equality.
    # **LOGIC_STEP**: No entry in GENERATED_PATHS is a directory today — `.claude/commands` was the
    # last one and it left with its generator. The branch stays because a project that generates a
    # directory needs it, and it is exercised here against a synthetic entry so that removing the
    # branch turns this red rather than passing on an empty case.
    @pytest.mark.unit
    def test_a_file_inside_a_generated_directory_is_refused(self, tmp_path: Path) -> None:
        run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / "Makefile").write_text(
            "print-generated-paths:\n\t@echo rendered\n", encoding="utf-8"
        )
        rendered = tmp_path / "rendered"
        rendered.mkdir()
        page = rendered / "page.md"
        page.write_text("generated", encoding="utf-8")

        assert self._decision(str(page), cwd=tmp_path) == "deny"

    # FUNCTION: test_hand_written_sources_are_left_alone
    # SUMMARY: Verify the guard stays silent for the files an agent is supposed to edit.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "name",
        [
            "docs/agent_rules.md",
            ".agents/skills/add-vertical/SKILL.md",
            "project/core/logging/logger.py",
            "README.md",
        ],
    )
    def test_hand_written_sources_are_left_alone(self, name: str) -> None:
        assert self._decision(str(_REPO_ROOT / name)) is None

    # FUNCTION: test_an_unreadable_payload_allows_rather_than_blocks
    # SUMMARY: Verify a hook that cannot parse its input fails open, not closed.
    @pytest.mark.unit
    def test_an_unreadable_payload_allows_rather_than_blocks(self) -> None:
        # **LOGIC_STEP**: Failing closed here would block every edit in the repository on a machine
        # without jq or python3 — a guard that bricks the workspace is worse than the drift it stops.
        result = run(
            [str(_REPO_ROOT / ".agents" / "hooks" / "pre-edit-guard.sh")],
            input="not json at all",
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == ""


# CLASS: tests.application.test_gate_recipes.TestBothAgentsRunTheSameGuard
# SUMMARY: Verify the pre-edit guard is registered for Codex as well, against the same script.
class TestBothAgentsRunTheSameGuard:
    # ATTRIBUTE: GUARD (str)
    # SUMMARY: Repository-relative path both agent configurations must name.
    GUARD = ".agents/hooks/pre-edit-guard.sh"

    # FUNCTION: test_codex_registers_the_guard_for_its_writing_tools
    # SUMMARY: Verify .codex/hooks.json fires the guard on Edit, Write and apply_patch.
    # **LOGIC_STEP**: apply_patch is the one Claude Code has no equivalent of, and it is how Codex
    # edits a file by default — a matcher that lists only Edit and Write would leave the ordinary
    # path unguarded while looking configured.
    @pytest.mark.unit
    def test_codex_registers_the_guard_for_its_writing_tools(self) -> None:
        config = json.loads((_REPO_ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        entries = config["hooks"]["PreToolUse"]
        matchers = " ".join(entry.get("matcher", "") for entry in entries)
        commands = [hook["command"] for entry in entries for hook in entry["hooks"]]

        assert self.GUARD in " ".join(commands)
        for tool in ("Edit", "Write", "apply_patch"):
            assert tool in matchers

    # FUNCTION: test_a_patch_naming_a_generated_file_is_refused
    # SUMMARY: Verify the guard reads a path out of an apply_patch body, not only file_path.
    # **LOGIC_STEP**: Codex sends the whole patch as `tool_input.command`. A guard that only knew
    # `file_path` would return silence here — allowing every Codex edit while every Claude Code
    # edit was refused, which is worse than no guard because it looks like one.
    @pytest.mark.unit
    def test_a_patch_naming_a_generated_file_is_refused(self) -> None:
        patch_body = "*** Begin Patch\n*** Update File: CLAUDE.md\n@@\n-was\n+now\n*** End Patch"
        payload = json.dumps({"tool_name": "apply_patch", "tool_input": {"command": patch_body}})

        result = run(
            [str(_REPO_ROOT / ".agents" / "hooks" / "pre-edit-guard.sh")],
            input=payload,
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

        decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "deny"

    # FUNCTION: test_both_agent_configurations_name_the_one_script
    # SUMMARY: Verify neither agent grew its own copy of the guard.
    @pytest.mark.unit
    def test_both_agent_configurations_name_the_one_script(self) -> None:
        claude = (_REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
        codex = (_REPO_ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8")

        assert self.GUARD in claude
        assert self.GUARD in codex
        assert len(list(_REPO_ROOT.glob("**/pre-edit-guard.sh"))) == 1
