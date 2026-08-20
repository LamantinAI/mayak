# FILE: tests/application/test_structure_builder.py
# SUMMARY: Unit tests for project-map generation targeting the dedicated reference document.

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.structure_builder import _build_tree_content, hermetic_git_env, resolve_project_name
from scripts.structure_builder import main as structure_builder_main
from scripts.structure_builder import update_project_map_file


# CLASS: tests.application.test_structure_builder.TestStructureBuilder
# SUMMARY: Verify structure_builder updates docs/project_map.md instead of the operational architecture doc.
class TestStructureBuilder:
    # FUNCTION: test_update_project_map_file_replaces_marker_block
    # SUMMARY: Verify the generated tree is injected between the project-map markers.
    @pytest.mark.unit
    def test_update_project_map_file_replaces_marker_block(self, tmp_path: Path) -> None:
        target_path = tmp_path / "docs" / "project_map.md"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            "\n".join(
                [
                    "# Project Map",
                    "",
                    "<!-- PROJECT_MAP_START -->",
                    "old content",
                    "<!-- PROJECT_MAP_END -->",
                ]
            ),
            encoding="utf-8",
        )

        update_project_map_file(target_path, "```\\nnew tree\\n```\\n")

        content = target_path.read_text(encoding="utf-8")
        assert "new tree" in content
        assert "old content" not in content

    # FUNCTION: test_main_check_mode_succeeds_when_project_map_is_current
    # SUMMARY: Verify --check exits 0 with an "up to date" message when the on-disk project_map matches the regenerated tree.
    @pytest.mark.unit
    def test_main_check_mode_succeeds_when_project_map_is_current(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # **LOGIC_STEP**: Build a minimal project tree the script can walk, including the
        # project_context.json that resolve_project_name now requires as its primary source.
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "project_context.json").write_text(
            '{"project_name": "FixtureProject"}', encoding="utf-8"
        )
        target_path = tmp_path / "docs" / "project_map.md"
        target_path.write_text(
            "<!-- PROJECT_MAP_START -->\n<!-- PROJECT_MAP_END -->\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        # **LOGIC_STEP**: First run regenerates project_map.md.
        monkeypatch.setattr(sys, "argv", ["structure_builder.py"])
        assert structure_builder_main() == 0

        # **LOGIC_STEP**: Second run with --check must report no drift.
        monkeypatch.setattr(sys, "argv", ["structure_builder.py", "--check"])
        capsys.readouterr()  # discard prior output
        assert structure_builder_main() == 0

        captured = capsys.readouterr()
        assert "up to date" in captured.out

    # FUNCTION: test_main_check_mode_fails_when_project_map_is_outdated
    # SUMMARY: Verify --check exits 1 with a unified diff on stderr when the on-disk project_map drifts from the regenerated tree.
    @pytest.mark.unit
    def test_main_check_mode_fails_when_project_map_is_outdated(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # **LOGIC_STEP**: Generate a fresh project_map then corrupt it. project_context.json is
        # required as resolve_project_name's primary source (no cwd-basename fallback).
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "project_context.json").write_text(
            '{"project_name": "FixtureProject"}', encoding="utf-8"
        )
        target_path = tmp_path / "docs" / "project_map.md"
        target_path.write_text(
            "<!-- PROJECT_MAP_START -->\n<!-- PROJECT_MAP_END -->\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        monkeypatch.setattr(sys, "argv", ["structure_builder.py"])
        assert structure_builder_main() == 0

        # **LOGIC_STEP**: Inject stale content between the markers.
        target_path.write_text(
            "<!-- PROJECT_MAP_START -->\nstale content that does not match\n<!-- PROJECT_MAP_END -->\n",
            encoding="utf-8",
        )

        # **LOGIC_STEP**: --check must detect drift, exit 1, emit unified diff to stderr.
        monkeypatch.setattr(sys, "argv", ["structure_builder.py", "--check"])
        capsys.readouterr()  # discard prior output
        assert structure_builder_main() == 1

        captured = capsys.readouterr()
        assert "Drift detected" in captured.err
        assert "make refresh-project-map" in captured.err
        # Unified-diff markers from difflib.unified_diff.
        assert "---" in captured.err
        assert "+++" in captured.err

    # FUNCTION: test_main_check_mode_fails_when_project_map_missing
    # SUMMARY: Verify --check exits 1 with a clear message when docs/project_map.md is absent.
    @pytest.mark.unit
    def test_main_check_mode_fails_when_project_map_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # **LOGIC_STEP**: tmp_path has no docs/project_map.md.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["structure_builder.py", "--check"])

        assert structure_builder_main() == 1

        captured = capsys.readouterr()
        assert "missing" in captured.err.lower()
        assert "make refresh-project-map" in captured.err


# FUNCTION: _init_git_repo
# SUMMARY: Initialize a minimal git repository under tmp_path with a committed docs/ fixture so
#          git-index-driven enumeration has a real index to query.
# INPUT: repo_root (Path): Directory to initialize as a git repository.
# INPUT: project_name (str): Value written to docs/project_context.json's project_name field.
# OUTPUT: (None): Repository is initialized and the fixture files are committed.
def _init_git_repo(repo_root: Path, project_name: str = "Mayak") -> None:
    # **LOGIC_STEP**: Minimal project_context.json shape accepted by resolve_project_name.
    docs_dir = repo_root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "project_context.json").write_text(
        json.dumps({"schema_version": 1, "project_name": project_name}),
        encoding="utf-8",
    )
    (docs_dir / "project_map.md").write_text(
        "# Project Map\n\n<!-- PROJECT_MAP_START -->\n<!-- PROJECT_MAP_END -->\n",
        encoding="utf-8",
    )

    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        env=hermetic_git_env(),
    )
    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test User")
    run("add", "-A")
    run("commit", "-q", "-m", "initial fixture commit")


# CLASS: tests.application.test_structure_builder.TestHermeticProjectMap
# SUMMARY: Red-to-green coverage for the non-determinism defects fixed by T1
#          (hermetic-project-map): a cwd-derived title and raw-iterdir junk leakage.
class TestHermeticProjectMap:
    # FUNCTION: test_title_uses_project_context_name_not_cwd_basename
    # SUMMARY: AC1 — the map header comes from project_context.json:project_name, not Path.cwd().name.
    @pytest.mark.unit
    def test_title_uses_project_context_name_not_cwd_basename(self, tmp_path: Path) -> None:
        # **LOGIC_STEP**: Use a distinctive cwd basename that must NOT leak into the rendered header.
        distinctive_root = tmp_path / "hopeful-shirley-87c989"
        distinctive_root.mkdir()
        _init_git_repo(distinctive_root, project_name="Mayak")

        content = _build_tree_content(distinctive_root)

        assert "Project Map: Mayak" in content
        assert "hopeful-shirley-87c989" not in content

    # FUNCTION: test_resolve_project_name_reads_project_context_json
    # SUMMARY: Direct unit check on the name-resolution helper in isolation.
    @pytest.mark.unit
    def test_resolve_project_name_reads_project_context_json(self, tmp_path: Path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "project_context.json").write_text(
            json.dumps({"project_name": "SomeOtherProject"}), encoding="utf-8"
        )

        assert resolve_project_name(tmp_path) == "SomeOtherProject"

    # FUNCTION: test_resolve_project_name_falls_back_to_pyproject_toml
    # SUMMARY: When project_context.json is absent, fall back to [project].name in pyproject.toml.
    @pytest.mark.unit
    def test_resolve_project_name_falls_back_to_pyproject_toml(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "fallback-project"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )

        assert resolve_project_name(tmp_path) == "fallback-project"

    # FUNCTION: test_resolve_project_name_never_uses_cwd_basename
    # SUMMARY: With neither source available, resolve_project_name must raise rather than
    #          silently degrade to Path.cwd().name (forbidden source per spec).
    @pytest.mark.unit
    def test_resolve_project_name_never_uses_cwd_basename(self, tmp_path: Path) -> None:
        distinctive_root = tmp_path / "totally-unrelated-basename"
        distinctive_root.mkdir()

        with pytest.raises(RuntimeError):
            resolve_project_name(distinctive_root)

    # FUNCTION: test_gitignored_file_excluded_from_tree
    # SUMMARY: AC3 — a file that exists on disk but is git-ignored must not appear in the
    #          generated map, even though raw iterdir() would see it.
    @pytest.mark.unit
    def test_gitignored_file_excluded_from_tree(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / ".gitignore").write_text(".tiktoken_cache/\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", ".gitignore"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            env=hermetic_git_env(),
        )
        subprocess.run(
            ["git", "commit", "-q", "-m", "add gitignore"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            env=hermetic_git_env(),
        )

        # **LOGIC_STEP**: Author-local junk that exists on disk but is untracked + ignored.
        junk_dir = tmp_path / ".tiktoken_cache"
        junk_dir.mkdir()
        (junk_dir / "cache.bin").write_text("junk", encoding="utf-8")

        content = _build_tree_content(tmp_path)

        assert ".tiktoken_cache" not in content
        assert "cache.bin" not in content

    # FUNCTION: test_check_mode_green_in_fresh_git_worktree
    # SUMMARY: AC2 — a fresh `git worktree add` off a repo whose HEAD has a correctly generated
    #          project_map.md must pass --check with zero manual actions.
    @pytest.mark.unit
    def test_check_mode_green_in_fresh_git_worktree(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_repo = tmp_path / "source_repo"
        source_repo.mkdir()
        _init_git_repo(source_repo, project_name="WorktreeFixtureProject")

        # **LOGIC_STEP**: Regenerate + commit a correct project_map.md on the source repo's HEAD,
        # exactly like a real repo's committed generated artifact.
        monkeypatch.setattr(sys, "argv", ["structure_builder.py"])
        monkeypatch.chdir(source_repo)
        assert structure_builder_main() == 0
        subprocess.run(
            ["git", "add", "-A"],
            cwd=source_repo,
            check=True,
            capture_output=True,
            env=hermetic_git_env(),
        )
        subprocess.run(
            ["git", "commit", "-q", "-m", "regenerate project_map.md"],
            cwd=source_repo,
            check=True,
            capture_output=True,
            env=hermetic_git_env(),
        )

        # **LOGIC_STEP**: Create a fresh worktree off HEAD — this is the exact scenario the audit
        # found broken (worktree basename differs from the project name).
        worktree_path = tmp_path / "a-completely-different-worktree-name"
        subprocess.run(
            ["git", "worktree", "add", str(worktree_path), "HEAD"],
            cwd=source_repo,
            check=True,
            capture_output=True,
            env=hermetic_git_env(),
        )

        # **LOGIC_STEP**: --check must be green in the fresh worktree with zero manual actions.
        monkeypatch.chdir(worktree_path)
        monkeypatch.setattr(sys, "argv", ["structure_builder.py", "--check"])
        assert structure_builder_main() == 0


# CONSTANT: _REPO_ROOT
# SUMMARY: The actual project repository root, resolved from this test file's location, used by the
#          live regression probes below that must run against the real .gitignore (not a synthetic
#          fixture) to prove the audit's cited real-world junk paths are genuinely excluded.
_REPO_ROOT = Path(__file__).resolve().parents[2]


# CLASS: tests.application.test_structure_builder.TestLiveRepoRegressionProbes
# SUMMARY: Fix-cycle iteration 1 (Phase 7): the independent verifier's live probe found that AC3's
#          synthetic-fixture test (TestHermeticProjectMap.test_gitignored_file_excluded_from_tree)
#          supplies its OWN throwaway .gitignore rule, so it proves the git-index-driven enumeration
#          mechanism respects whatever .gitignore says, but never proves THIS repository's actual
#          root .gitignore covers the paths the original audit cited (.tiktoken_cache/, docs/research/).
#          These tests run against the real repo root (not tmp_path) via subprocess, so they exercise
#          the exact CLI entrypoint an agent/CI invokes and would have caught the FAIL the verifier
#          found (real .tiktoken_cache/probe.bin -> "Drift detected" / exit 1) before it shipped.
class TestLiveRepoRegressionProbes:
    # FUNCTION: test_tiktoken_cache_probe_excluded_from_real_repo_map
    # SUMMARY: AC3 (live) — creating .tiktoken_cache/__probe.bin under the real repo root must not
    #          break `structure_builder.py --check`, now that .gitignore covers .tiktoken_cache/.
    @pytest.mark.unit
    def test_tiktoken_cache_probe_excluded_from_real_repo_map(self) -> None:
        probe_dir = _REPO_ROOT / ".tiktoken_cache"
        probe_file = probe_dir / "__probe.bin"
        # **LOGIC_STEP**: Guard against a pre-existing directory from unrelated local state — only
        # remove what this test itself creates, tracked via created_dir.
        created_dir = not probe_dir.exists()
        try:
            probe_dir.mkdir(parents=True, exist_ok=True)
            probe_file.write_text("probe", encoding="utf-8")

            # **LOGIC_STEP**: Confirm the probe is genuinely gitignored — if this assertion ever
            # fails, .gitignore regressed and the --check assertion below would be a false negative
            # proof (it could pass for the wrong reason, e.g. the file not existing at all).
            check_ignore = subprocess.run(
                ["git", "check-ignore", "-v", str(probe_file)],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
                env=hermetic_git_env(),
            )
            assert check_ignore.returncode == 0, (
                f".tiktoken_cache/ is not covered by .gitignore — git check-ignore said: "
                f"{check_ignore.stdout}{check_ignore.stderr}"
            )

            # **LOGIC_STEP**: Regenerate the map in process and look at what came out, rather than
            # asking `--check` whether the COMMITTED map still matches. The exit code of --check
            # answers a different question — is docs/project_map.md up to date — and it is 1 for
            # any uncommitted new file in the tree. So this test, whose subject is the probe, used
            # to fail whenever the map was merely stale, with a message about .tiktoken_cache that
            # sent the reader nowhere. Measured: adding one file to project/domain turned this and
            # two neighbours red.
            regenerated = _build_tree_content(_REPO_ROOT)

            # **LOGIC_STEP**: A map that failed to build is empty, and "not in" holds for
            # everything in an empty string. Anchor on a kernel file so absence means absence.
            assert "composition_root.py" in regenerated
            assert "__probe.bin" not in regenerated
            assert ".tiktoken_cache" not in regenerated
        finally:
            # **LOGIC_STEP**: Unconditional cleanup — never leave probe artifacts in the real working
            # tree, even if an assertion above raised.
            if probe_file.exists():
                probe_file.unlink()
            if created_dir and probe_dir.exists():
                probe_dir.rmdir()

    # FUNCTION: test_docs_research_probe_excluded_from_real_repo_map
    # SUMMARY: AC3 (live) — same probe for docs/research/, the second real-world junk path that
    #          drifted the committed map before .gitignore covered it.
    @pytest.mark.unit
    def test_docs_research_probe_excluded_from_real_repo_map(self) -> None:
        probe_dir = _REPO_ROOT / "docs" / "research"
        probe_file = probe_dir / "__probe.md"
        created_dir = not probe_dir.exists()
        try:
            probe_dir.mkdir(parents=True, exist_ok=True)
            probe_file.write_text("probe", encoding="utf-8")

            check_ignore = subprocess.run(
                ["git", "check-ignore", "-v", str(probe_file)],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
                env=hermetic_git_env(),
            )
            assert check_ignore.returncode == 0, (
                f"docs/research/ is not covered by .gitignore — git check-ignore said: "
                f"{check_ignore.stdout}{check_ignore.stderr}"
            )

            # **LOGIC_STEP**: Same reasoning as the .tiktoken_cache probe above — the subject is
            # what the generator emits, not whether the committed map is current.
            regenerated = _build_tree_content(_REPO_ROOT)

            assert "composition_root.py" in regenerated
            assert "__probe.md" not in regenerated
            assert "docs/research" not in regenerated
        finally:
            if probe_file.exists():
                probe_file.unlink()
            if created_dir and probe_dir.exists():
                probe_dir.rmdir()


# CLASS: tests.application.test_structure_builder.TestHookEnvironmentIsolation
# SUMMARY: Fix-cycle iteration 2 (Phase 7): a pre-commit hook invocation of this test suite sets
#          GIT_DIR/GIT_INDEX_FILE/GIT_PREFIX in its own process environment pointing at the REAL
#          repository. Every git subprocess call this test file and scripts/structure_builder.py make
#          used to inherit those variables silently, so fixture git operations that intended to target
#          a throwaway tmp_path repo (git init/add/commit/worktree add) were actually applied to the
#          real repository instead — confirmed by finding core.bare=true and a stray test identity
#          (user.email=test@example.com, user.name=Test User) in the real repo's local config after a
#          hook-context pytest run. hermetic_git_env() (imported from scripts.structure_builder) strips
#          every GIT_*-prefixed variable before each git subprocess call; these tests prove that
#          protection holds both for the test fixtures themselves and for structure_builder.py's own
#          git ls-files call, under a deliberately hostile, hook-like environment.
class TestHookEnvironmentIsolation:
    # FUNCTION: test_fixture_repo_init_lands_in_tmp_path_under_inherited_hook_env
    # SUMMARY: Safety invariant requested by the coordinator: even with GIT_DIR/GIT_INDEX_FILE set in
    #          this test process's own environment (simulating a pre-commit hook), _init_git_repo's
    #          hermetic_git_env()-protected git calls must still create their repository in tmp_path —
    #          not silently mutate whatever repository GIT_DIR/GIT_INDEX_FILE happen to point at.
    @pytest.mark.unit
    def test_fixture_repo_init_lands_in_tmp_path_under_inherited_hook_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # **LOGIC_STEP**: Simulate the pre-commit hook's environment: GIT_DIR/GIT_INDEX_FILE pointing
        # at THIS actual repository's real .git, exactly as the coordinator's minimal repro showed
        # (`GIT_DIR=.git GIT_INDEX_FILE=.git/index ...`). This is the hostile precondition that caused
        # the real incident — fixture git calls inheriting these and silently mutating the real repo.
        monkeypatch.setenv("GIT_DIR", str(_REPO_ROOT / ".git"))
        monkeypatch.setenv("GIT_INDEX_FILE", str(_REPO_ROOT / ".git" / "index"))
        monkeypatch.setenv("GIT_PREFIX", "some/bogus/prefix/")

        repo_dir = tmp_path / "isolated_repo"
        repo_dir.mkdir()
        _init_git_repo(repo_dir, project_name="HookIsolationFixtureProject")

        # **LOGIC_STEP**: The repository must have been created in tmp_path, not wherever the
        # inherited GIT_DIR/GIT_INDEX_FILE would have pointed — this is exactly the invariant that
        # was silently violated in the real incident.
        assert (repo_dir / ".git").exists(), (
            "Expected a real .git directory inside tmp_path's isolated_repo — if this is missing, "
            "the fixture's git calls followed the inherited (hostile) GIT_DIR instead of operating "
            "on repo_dir, which is the exact failure mode that corrupted the real repository."
        )

        toplevel = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            env=hermetic_git_env(),
        )
        assert toplevel.stdout.strip() == str(repo_dir.resolve()), (
            f"Expected git to report {repo_dir.resolve()} as the toplevel of the fixture repo, got "
            f"{toplevel.stdout.strip()!r} — the fixture repo's identity is not what it should be."
        )

        # **LOGIC_STEP**: Also confirm the REAL repository's local config was not touched by the
        # fixture's git calls under this hostile environment (the concrete symptom the coordinator
        # found: a stray test identity / core.bare flip leaking into the real repo's local config).
        real_repo_config = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "config", "--local", "--get", "user.email"],
            capture_output=True,
            text=True,
            env=hermetic_git_env(),
        )
        assert "test@example.com" not in real_repo_config.stdout, (
            "The fixture's throwaway git identity leaked into the real repository's local config — "
            "hermetic_git_env() failed to prevent the incident it exists to prevent."
        )

    # FUNCTION: test_structure_builder_check_survives_hook_style_git_env_in_subprocess
    # SUMMARY: Regression test for the exact bug the coordinator's minimal repro demonstrated:
    #          `GIT_DIR=.git GIT_INDEX_FILE=.git/index <python> -m pytest ...` made
    #          TestLiveRepoRegressionProbes fail with "fatal: ... index file open failed" / "this
    #          operation must be run in a work tree" (git exit 128) because git subprocess calls
    #          inherited those variables. This test invokes structure_builder.py --check as a
    #          subprocess with GIT_DIR/GIT_INDEX_FILE explicitly injected into ITS environment (the
    #          same shape a pre-commit hook produces), proving the fix (hermetic_git_env() inside
    #          list_repo_files) holds even when the hostile variables are set on the immediate child
    #          process, not just inherited transitively.
    #          IMPORTANT METHODOLOGICAL NOTE (found while writing this test): asserting only
    #          `returncode == 0` is NOT sufficient here. If list_repo_files' git call fails silently
    #          (returns None), _build_tree_content falls back to the iterdir()-based walk, which
    #          also produces exit 0 when the resulting tree happens to match the committed map — so
    #          a weak version of this test could pass for the WRONG reason (silently exercising the
    #          fallback path instead of proving the git-index path survives the hostile env). The
    #          real assertion is the ABSENCE of the fallback warning on stderr — that warning only
    #          fires when list_repo_files returned None, i.e. exactly when the hostile env broke it.
    @pytest.mark.unit
    def test_structure_builder_check_survives_hook_style_git_env_in_subprocess(self) -> None:
        hook_like_env = dict(os.environ)
        hook_like_env["GIT_DIR"] = ".git"
        hook_like_env["GIT_INDEX_FILE"] = ".git/index"

        result = subprocess.run(
            [sys.executable, "scripts/structure_builder.py", "--check"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            env=hook_like_env,
        )
        # **LOGIC_STEP**: The invariant is "the hostile environment changes nothing", not "--check
        # exits 0". The old assertion conflated the two, so an out-of-date docs/project_map.md —
        # any new file not yet regenerated — failed this test with a paragraph about git worktrees.
        # Comparing the two runs states the real subject and cannot be confused by drift.
        clean = subprocess.run(
            [sys.executable, "scripts/structure_builder.py", "--check"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        )

        assert (result.returncode, result.stdout, result.stderr) == (
            clean.returncode,
            clean.stdout,
            clean.stderr,
        ), (
            "structure_builder.py --check behaved differently under a hook-style "
            "GIT_DIR/GIT_INDEX_FILE environment — this is the failure the minimal repro "
            "demonstrated ('fatal: this operation must be run in a work tree', git exit 128) "
            "prior to the hermetic_git_env() fix in scripts.structure_builder.list_repo_files. "
            f"hostile: {result.returncode} {result.stderr!r}; clean: {clean.returncode} "
            f"{clean.stderr!r}"
        )
        assert "fatal" not in result.stderr.lower()
        # **LOGIC_STEP**: The real proof — the git-index enumeration path must have been used, not
        # the iterdir() fallback (which would also exit 0 here, masking the bug). This warning only
        # ever appears when list_repo_files() returned None (its git subprocess call failed).
        assert "falling back to a raw directory walk" not in result.stderr, (
            "structure_builder.py silently fell back to the iterdir()-based walk instead of using "
            "git-index enumeration — this means list_repo_files()'s git subprocess call failed under "
            "the hostile GIT_DIR/GIT_INDEX_FILE environment, and the test only passed because the "
            "fallback produced a matching (but non-hermetic) tree by coincidence."
        )

    # FUNCTION: test_list_repo_files_does_not_silently_fall_back_under_hook_style_env
    # SUMMARY: Narrower unit-level companion to the subprocess test above — calls list_repo_files()
    #          directly (in-process) under a monkeypatched hook-style environment and asserts it
    #          still returns a real file list (not None), i.e. the git call actually succeeded rather
    #          than triggering the fallback branch by returning None.
    @pytest.mark.unit
    def test_list_repo_files_does_not_silently_fall_back_under_hook_style_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from scripts.structure_builder import list_repo_files

        monkeypatch.setenv("GIT_DIR", ".git")
        monkeypatch.setenv("GIT_INDEX_FILE", ".git/index")

        files = list_repo_files(_REPO_ROOT)

        assert files is not None, (
            "list_repo_files() returned None under a hook-style GIT_DIR/GIT_INDEX_FILE environment "
            "— its git subprocess call failed, which is the exact regression hermetic_git_env() "
            "exists to prevent."
        )
        assert "scripts/structure_builder.py" in files
