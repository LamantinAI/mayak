# FILE: structure_builder.py
# SUMMARY: A utility script to generate a project tree map with file summaries extracted from CBM tags, using the git index (tracked + untracked-not-ignored files) so the map is hermetic across worktrees and checkouts.

import argparse
import difflib
import io
import os
import subprocess
import sys
import fnmatch
import json
import tomllib
from pathlib import Path

# --- CONFIGURATION ---

# Base directories/files to always ignore (even without .gitignore).
DEFAULT_IGNORE_PATTERNS = {".git", "__pycache__", ".idea", ".vscode", ".DS_Store"}

# Export profile containing extra AI-facing ignore globs.
EXPORT_PROFILE_PATH = Path("docs/llm_export_profile.json")

# Project context file — canonical source for the project's display name (see resolve_project_name).
PROJECT_CONTEXT_PATH = Path("docs/project_context.json")

# pyproject.toml — fallback source for the project's display name when project_context.json is absent.
PYPROJECT_TOML_PATH = Path("pyproject.toml")

# File extensions to include (empty = all)
INCLUDE_EXTENSIONS = set()
# Example: INCLUDE_EXTENSIONS = {'.py', '.yaml', '.yml', '.env', '.json', '.md'}

# Tags to search for at the beginning of a file (case-sensitive)
TAG_PREFIX = "# SUMMARY:"


# FUNCTION: load_gitignore_patterns
# SUMMARY: Reads .gitignore file from the root directory and combines it with defaults.
def load_gitignore_patterns(root_path: Path) -> list:
    # **LOGIC_STEP**: Load patterns from .gitignore and merge with default exclusions.
    patterns = list(DEFAULT_IGNORE_PATTERNS)
    gitignore_path = root_path / ".gitignore"

    if gitignore_path.exists():
        try:
            with open(gitignore_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if not line or line.startswith("#"):
                        continue
                    patterns.append(line)
        except Exception as e:
            sys.stderr.write(f"Warning: Could not read .gitignore: {e}\n")

    patterns.extend(load_export_profile_patterns(root_path))
    return patterns


# FUNCTION: load_export_profile_patterns
# SUMMARY: Load extra ignore globs from the checked-in LLM export profile when present.
# OUTPUT: (list[str]): Additional ignore patterns declared for AI-facing exports.
def load_export_profile_patterns(root_path: Path) -> list[str]:
    profile_path = root_path / EXPORT_PROFILE_PATH
    if not profile_path.exists():
        return []

    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        sys.stderr.write(f"Warning: Could not parse {profile_path}: {error}\n")
        return []

    patterns = payload.get("ignore_globs", [])
    if not isinstance(patterns, list):
        return []
    return [pattern for pattern in patterns if isinstance(pattern, str)]


# FUNCTION: resolve_project_name
# SUMMARY: Resolve the project's display name for the map header. Path.cwd().name is forbidden as a
#          source (it makes the map's title depend on the checkout/worktree directory name); the
#          canonical source is docs/project_context.json:project_name, with pyproject.toml's
#          [project].name as a fallback for environments that lack the former.
# RAISES: RuntimeError: If neither source yields a usable name.
def resolve_project_name(root_path: Path) -> str:
    # **LOGIC_STEP**: Primary source — docs/project_context.json:project_name (schema-validated by
    # scripts/validate_project_context.py, so this is expected to exist and be well-formed in every
    # real invocation).
    context_path = root_path / PROJECT_CONTEXT_PATH
    if context_path.exists():
        try:
            payload = json.loads(context_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            sys.stderr.write(f"Warning: Could not parse {context_path}: {error}\n")
        else:
            name = payload.get("project_name")
            if isinstance(name, str) and name.strip():
                return name.strip()

    # **LOGIC_STEP**: Fallback source — pyproject.toml's [project].name. The import used to sit
    # behind try/except ImportError because tomllib is stdlib only from 3.11 and the floor was
    # 3.10; the floor is now 3.13, so the guard could only ever take its live branch.
    pyproject_path = root_path / PYPROJECT_TOML_PATH
    if pyproject_path.exists():
        try:
            payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            sys.stderr.write(f"Warning: Could not parse {pyproject_path}: {error}\n")
        else:
            name = payload.get("project", {}).get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()

    raise RuntimeError(
        f"Could not resolve project name from {PROJECT_CONTEXT_PATH} or {PYPROJECT_TOML_PATH} "
        f"under {root_path}. Path.cwd().name is intentionally not used as a fallback because it "
        "makes the generated map depend on the checkout/worktree directory name."
    )


# FUNCTION: hermetic_git_env
# SUMMARY: Build a subprocess environment with every inherited GIT_*-prefixed variable stripped, so
#          git subprocess calls do not accidentally inherit a caller's GIT_DIR/GIT_INDEX_FILE/GIT_PREFIX
#          (e.g. from a pre-commit hook's own environment) and point at the wrong repository state.
#          Filtering the whole GIT_ prefix (not just two known names) is deliberate: git's own hook
#          environment varies by git version and hook type, so an allowlist of "known offenders" would
#          silently miss the next one.
# OUTPUT: (dict[str, str]): A copy of os.environ with all GIT_*-prefixed keys removed; everything else
#         (PATH, HOME, etc.) is preserved so the subprocess can still find and run git normally.
def hermetic_git_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


# FUNCTION: list_repo_files
# SUMMARY: Enumerate tracked + untracked-not-ignored files via the git index, so the generated map
#          never includes gitignored local junk (e.g. cache directories) regardless of what a raw
#          directory walk would see.
# OUTPUT: (list[str] | None): Sorted repo-relative POSIX paths, or None if root_path is not inside a
#         usable git working tree (caller falls back to a raw directory walk).
def list_repo_files(root_path: Path) -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root_path,
            capture_output=True,
            check=True,
            env=hermetic_git_env(),
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    raw_paths = result.stdout.decode("utf-8", errors="replace").split("\0")
    return sorted(path for path in raw_paths if path)


# FUNCTION: build_tree_from_paths
# SUMMARY: Build a nested dict representing directories/files from a flat list of repo-relative
#          POSIX paths, so the renderer can walk a virtual tree derived from the git index instead
#          of a real directory listing.
# INPUT: paths (list[str]): Repo-relative POSIX file paths (no directory entries — git does not
#        track empty directories, so none are synthesized here).
# OUTPUT: (dict): Nested structure; each dict maps a path segment to either another dict (directory)
#         or None (file leaf).
def build_tree_from_paths(paths: list[str]) -> dict:
    root: dict = {}
    for path in paths:
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = None
    return root


# FUNCTION: should_ignore
# SUMMARY: Checks if a file or directory matches any of the ignore patterns.
# INPUT: root_path (Path): Repository root used for relative-path matching.
# OUTPUT: (bool): True if the item should be ignored.
def should_ignore(item: Path, root_path: Path, patterns: list) -> bool:
    # **LOGIC_STEP**: Check both basename and repository-relative path so export-specific globs can hide noisy artifacts.
    name = item.name
    relative_path = item.relative_to(root_path).as_posix()

    for pattern in patterns:
        # Strip trailing slash from pattern (e.g., node_modules/ -> node_modules)
        clean_pattern = pattern.rstrip("/")

        # Check 1: Exact name match or wildcard (e.g., *.pyc)
        if fnmatch.fnmatch(name, clean_pattern):
            return True

        # Check 2: Match repository-relative paths for scoped exclusions.
        if fnmatch.fnmatch(relative_path, clean_pattern):
            return True

        # Check 3: If pattern implied a path (e.g., dist/), check directory name or relative path.
        if (
            item.is_dir()
            and pattern.endswith("/")
            and (name == clean_pattern or relative_path == clean_pattern)
        ):
            return True

    return False


# FUNCTION: get_file_summary
# SUMMARY: Reads the first 20 lines of a file and searches for a line starting with '# SUMMARY:'.
# OUTPUT: (str): The extracted summary text or an empty string if not found.
def get_file_summary(file_path: Path) -> str:
    # **LOGIC_STEP**: Read first 20 lines and extract SUMMARY tag content.
    try:
        # Open file with encoding error ignoring (for binary files)
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break

                stripped = line.strip()
                if stripped.startswith(TAG_PREFIX):
                    return stripped[len(TAG_PREFIX) :].strip()
    except (PermissionError, OSError):
        return ""

    return ""


# FUNCTION: generate_tree
# SUMMARY: Recursive function to traverse real directories and build a tree with descriptions.
#          Fallback path used only when root_path is not inside a usable git working tree (see
#          list_repo_files) — kept so the tool remains usable against a bare directory (e.g. a
#          tarball export) without a git index to query.
def generate_tree(
    dir_path: Path,
    root_path: Path,
    ignore_patterns: list,
    prefix: str = "",
):
    # **LOGIC_STEP**: Recursively traverse directories and build tree with descriptions.
    if not dir_path.is_dir():
        return

    try:
        contents = list(dir_path.iterdir())
        contents.sort(key=lambda x: (not x.is_dir(), x.name.lower()))
    except PermissionError:
        print(f"{prefix}[ACCESS DENIED]")
        return

    filtered_contents = []
    for item in contents:
        # Filter using .gitignore and default patterns
        if should_ignore(item, root_path, ignore_patterns):
            continue

        # Additional extension filter (if configured)
        if item.is_file() and INCLUDE_EXTENSIONS:
            if item.suffix not in INCLUDE_EXTENSIONS and item.name != ".env":
                continue

        filtered_contents.append(item)

    pointers = [
        ("├── " if i < len(filtered_contents) - 1 else "└── ")
        for i in range(len(filtered_contents))
    ]

    for pointer, item in zip(pointers, filtered_contents):
        if item.is_dir():
            print(f"{prefix}{pointer}{item.name}/")
            extension = "│   " if pointer == "├── " else "    "
            # Pass patterns to recursion
            generate_tree(item, root_path, ignore_patterns, prefix=prefix + extension)
        else:
            summary = ""
            # Search for description only in .py files (can be extended)
            if item.suffix == ".py":
                found_text = get_file_summary(item)
                if found_text:
                    summary = f"  ---  {found_text}"

            print(f"{prefix}{pointer}{item.name}{summary}")


# FUNCTION: generate_tree_from_git_paths
# SUMMARY: Render a tree from the git-index-derived virtual structure (build_tree_from_paths),
#          instead of a raw directory walk. This is the primary rendering path: since the input
#          paths already come from `git ls-files --cached --others --exclude-standard`, gitignored
#          local junk can never appear here regardless of what exists on disk.
# INPUT: node (dict): Current subtree from build_tree_from_paths (segment -> dict | None).
# INPUT: node_path (str): Repo-relative POSIX path of this node ("" for the root).
# INPUT: root_path (Path): Repository root — used to resolve real files for CBM summary extraction
#        and for should_ignore's relative-path matching (defense-in-depth on top of the git filter,
#        e.g. for DEFAULT_IGNORE_PATTERNS and any llm_export_profile.json globs).
# INPUT: ignore_patterns (list): Extra ignore patterns (gitignore-derived + export profile).
def generate_tree_from_git_paths(
    node: dict,
    node_path: str,
    root_path: Path,
    ignore_patterns: list,
    prefix: str = "",
) -> None:
    entries = []
    for name, child in node.items():
        rel_path = f"{node_path}/{name}" if node_path else name
        real_path = root_path / rel_path
        if should_ignore(real_path, root_path, ignore_patterns):
            continue
        if child is None and INCLUDE_EXTENSIONS:
            if real_path.suffix not in INCLUDE_EXTENSIONS and real_path.name != ".env":
                continue
        entries.append((name, child, rel_path))

    entries.sort(key=lambda entry: (entry[1] is None, entry[0].lower()))

    pointers = ["├── " if i < len(entries) - 1 else "└── " for i in range(len(entries))]

    for pointer, (name, child, rel_path) in zip(pointers, entries):
        if child is not None:
            print(f"{prefix}{pointer}{name}/")
            extension = "│   " if pointer == "├── " else "    "
            generate_tree_from_git_paths(
                child, rel_path, root_path, ignore_patterns, prefix=prefix + extension
            )
        else:
            summary = ""
            real_path = root_path / rel_path
            if real_path.suffix == ".py":
                found_text = get_file_summary(real_path)
                if found_text:
                    summary = f"  ---  {found_text}"

            print(f"{prefix}{pointer}{name}{summary}")


# FUNCTION: update_project_map_file
# SUMMARY: Replaces the project map section inside a supported markdown reference file.
# INPUT: file_path (Path): Target markdown file containing project map markers.
# INPUT: tree_content (str): Rendered project tree block to inject between markers.
# RAISES: SystemExit: If the target file does not exist or is missing required markers.
# FUNCTION: _compute_updated_content
# SUMMARY: Compute the updated project_map.md content (with the freshly generated tree) without writing to disk.
# INPUT: file_path (Path): Path to the existing project_map.md file (must exist with required markers).
# INPUT: tree_content (str): Newly generated project tree content to inject between markers.
# OUTPUT: (str): Updated full file content with the project map block replaced.
# RAISES: FileNotFoundError: If the target file does not exist.
# RAISES: ValueError: If the required markers are missing from the target file.
def _compute_updated_content(file_path: Path, tree_content: str) -> str:
    START_MARKER = "<!-- PROJECT_MAP_START -->"
    END_MARKER = "<!-- PROJECT_MAP_END -->"

    # **LOGIC_STEP**: Verify the target file exists before attempting marker replacement.
    if not file_path.exists():
        raise FileNotFoundError(str(file_path))

    # **LOGIC_STEP**: Read target content and locate the project map markers.
    file_content = file_path.read_text(encoding="utf-8")
    start_idx = file_content.find(START_MARKER)
    end_idx = file_content.find(END_MARKER)

    if start_idx == -1 or end_idx == -1:
        raise ValueError(f"markers not found in {file_path}")

    # **LOGIC_STEP**: Replace the existing project map block with the newly generated tree.
    return (
        file_content[: start_idx + len(START_MARKER)] + "\n" + tree_content + file_content[end_idx:]
    )


# FUNCTION: update_project_map_file
# SUMMARY: Replace the project map block in the target file with the newly generated tree.
# INPUT: file_path (Path): Path to the existing project_map.md file.
# INPUT: tree_content (str): Newly generated project tree content to inject between markers.
# OUTPUT: (None): Writes the updated content to disk and prints a confirmation line.
def update_project_map_file(file_path: Path, tree_content: str) -> None:
    try:
        new_content = _compute_updated_content(file_path, tree_content)
    except FileNotFoundError:
        sys.stderr.write(f"Error: {file_path} not found.\n")
        sys.exit(1)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}.\n")
        sys.exit(1)

    file_path.write_text(new_content, encoding="utf-8")
    print(f"{file_path.name} updated with project map.")


# FUNCTION: _build_tree_content
# SUMMARY: Walk the project tree starting at root_dir and return the markdown-fenced project map block as a string. Prefers git-index-driven enumeration (tracked + untracked-not-ignored) for hermetic output across worktrees/checkouts; falls back to a raw directory walk only outside a git working tree.
# OUTPUT: (str): Complete fenced project map block ready for marker replacement.
def _build_tree_content(root_dir: Path) -> str:
    ignore_patterns = load_gitignore_patterns(root_dir)
    project_name = resolve_project_name(root_dir)
    repo_files = list_repo_files(root_dir)

    buffer = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = buffer
    try:
        # **LOGIC_STEP**: The two-line preamble is generated rather than typed above the marker.
        # It used to sit outside the markers, where `--check` cannot see it — the comparison copies
        # everything outside them straight from the file being checked, so `expected == actual`
        # there by construction. It spent two days pointing at ARCHITECTURE.md, deleted on
        # 2026-08-11, and the gate reported the map up to date every time.
        print("This file is the generated reference tree for repository navigation.")
        print("Read `docs/agent_rules.md` for the operational contract and change workflow.")
        print()
        print("```")
        print(f"Project Map: {project_name}")
        print("=" * 60)
        if repo_files is not None:
            tree = build_tree_from_paths(repo_files)
            generate_tree_from_git_paths(tree, "", root_dir, ignore_patterns)
        else:
            sys.stderr.write(
                "Warning: not inside a usable git working tree — falling back to a raw "
                "directory walk (gitignored files will NOT be filtered by the git index).\n"
            )
            generate_tree(root_dir, root_dir, ignore_patterns)
        print("```")
    finally:
        sys.stdout = original_stdout

    return buffer.getvalue()


# FUNCTION: main
# SUMMARY: The script's entry point. Generates the project tree and either writes it to docs/project_map.md or verifies the committed copy is up to date.
# OUTPUT: (int): Process exit code (0 on success, 1 on drift/missing target in --check mode).
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate or verify the project map block in docs/project_map.md. "
            "Without --check, writes the regenerated map to disk. With --check, "
            "compares the regenerated map against the committed copy and exits 1 "
            "on drift, missing target, or missing markers."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verify mode: compare regenerated content against the committed file "
            "without writing. Exits 1 with a unified diff on drift."
        ),
    )
    args = parser.parse_args()

    root_dir = Path.cwd()
    target_path = root_dir / "docs" / "project_map.md"

    if args.check:
        # **LOGIC_STEP**: Drift-check mode — bail out on a missing target before paying the cost of
        # tree generation (and before resolve_project_name can raise on a fixture that never
        # intended to exercise the name-resolution path at all).
        if not target_path.exists():
            sys.stderr.write("docs/project_map.md is missing — run `make refresh-project-map`\n")
            return 1

    tree_content = _build_tree_content(root_dir)

    if args.check:
        try:
            expected = _compute_updated_content(target_path, tree_content)
        except ValueError as exc:
            sys.stderr.write(
                f"docs/project_map.md is missing required markers ({exc}) — "
                "run `make refresh-project-map`\n"
            )
            return 1

        actual = target_path.read_text(encoding="utf-8")
        if expected == actual:
            print("docs/project_map.md is up to date.")
            return 0

        # **LOGIC_STEP**: Drift detected — print actionable header + unified diff to stderr.
        sys.stderr.write(
            "Drift detected in docs/project_map.md — "
            "run `make refresh-project-map` to regenerate:\n"
        )
        diff_lines = difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile="docs/project_map.md (committed)",
            tofile="docs/project_map.md (regenerated)",
        )
        sys.stderr.writelines(diff_lines)
        return 1

    # **LOGIC_STEP**: Default mode — write the regenerated tree into docs/project_map.md if present.
    if target_path.exists():
        update_project_map_file(target_path, tree_content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
