# FILE: tests/application/test_import_graph_is_acyclic.py
# SUMMARY: Guard the one defect the whole suite is structurally blind to — an import cycle inside
# project/ that only breaks when a module is imported first.
# NOTE: `python -c "import project.core.error_utils"` raised ImportError on a tree whose 1129
# tests were green. error_utils imported project.core.logging.redaction; importing any
# submodule runs that package's __init__, which imports logger, which imported error_utils back —
# a module still half-built. The suite never saw it because conftest.py imports the logging package
# long before anything reaches error_utils, so every test ran in the one import order that works.
# A test that imports the module cannot catch this either, for the same reason. Two things do: the
# graph below, read from the source rather than from a live interpreter, and one subprocess that
# starts from nothing.

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_ROOT = _REPO_ROOT / "project"


# FUNCTION: _module_files
# SUMMARY: Map every importable module name under a package root to its file.
# INPUT: package_root (Path): Directory of the package to walk; the mechanism test passes its own.
# OUTPUT: (dict[str, Path]): Dotted module name to source file, `__init__.py` named as its package.
def _module_files(package_root: Path) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.with_suffix("").relative_to(package_root.parent).parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules[".".join(parts)] = path
    return modules


# FUNCTION: _import_edges
# SUMMARY: Build the module graph Python actually walks at import time.
# INPUT: package_root (Path): Package to read.
# OUTPUT: (dict[str, set[str]]): Module name to the modules importing it runs.
# **LOGIC_STEP**: Only module-level imports count — an import inside a function runs when the
# function is called, long after every module is built, and is the ordinary way to break a cycle
# on purpose. Reading nested statements would report cycles that cannot happen.
# **LOGIC_STEP**: Importing `a.b.c` runs `a`'s and `a.b`'s `__init__` first, so an ancestor package
# is an edge too — but only for an importer outside that package. Inside it, `__init__` is already
# running and is not re-entered, which is why sibling modules referring to each other are not a
# cycle. Getting this wrong in either direction is the difference between a rule that catches the
# error_utils defect above and one that goes red on the shipped logging package.
def _import_edges(package_root: Path) -> dict[str, set[str]]:
    modules = _module_files(package_root)
    package_name = package_root.name
    edges: dict[str, set[str]] = {}

    for name, path in modules.items():
        targets: set[str] = set()
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Import):
                targets.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                targets.add(node.module)

        dependencies: set[str] = set()
        for target in targets:
            if target != package_name and not target.startswith(f"{package_name}."):
                continue
            # **LOGIC_STEP**: `from project.domain.exceptions import ConflictError` names a symbol,
            # not a module — walk up until the dotted prefix is one this package really has.
            candidate = target
            while candidate and candidate not in modules:
                candidate = candidate.rsplit(".", 1)[0] if "." in candidate else ""
            if not candidate:
                continue
            dependencies.add(candidate)
            parts = candidate.split(".")
            for depth in range(1, len(parts)):
                ancestor = ".".join(parts[:depth])
                if ancestor in modules and not name.startswith(f"{ancestor}."):
                    dependencies.add(ancestor)
        dependencies.discard(name)
        edges[name] = dependencies

    return edges


# FUNCTION: _first_cycle
# SUMMARY: Return one import cycle from the graph, as the path that closes it.
# INPUT: edges (dict[str, set[str]]): The graph from _import_edges.
# OUTPUT: (list[str]): Module names, first repeated at the end, or an empty list when acyclic.
def _first_cycle(edges: dict[str, set[str]]) -> list[str]:
    state: dict[str, int] = {}
    path: list[str] = []
    found: list[str] = []

    def visit(module: str) -> None:
        if found:
            return
        state[module] = 1
        path.append(module)
        for neighbour in sorted(edges.get(module, ())):
            if found:
                break
            if state.get(neighbour, 0) == 0:
                visit(neighbour)
            elif state[neighbour] == 1:
                found.extend(path[path.index(neighbour) :] + [neighbour])
        path.pop()
        state[module] = 2

    for module in sorted(edges):
        if state.get(module, 0) == 0:
            visit(module)
    return found


# CLASS: tests.application.test_import_graph_is_acyclic.TestNoImportCycleUnderProject
# SUMMARY: Verify no module under project/ can be the first one imported and fail.
class TestNoImportCycleUnderProject:
    # FUNCTION: test_the_shipped_package_has_no_import_cycle
    # SUMMARY: Verify the real graph is acyclic, naming the cycle when it is not.
    # OUTPUT: (None): None.
    @pytest.mark.unit
    def test_the_shipped_package_has_no_import_cycle(self) -> None:
        cycle = _first_cycle(_import_edges(_PACKAGE_ROOT))

        assert cycle == [], "import cycle: " + " -> ".join(cycle)

    # FUNCTION: test_error_utils_imports_alone_in_a_fresh_interpreter
    # SUMMARY: Verify the module that broke can still be the first thing a process imports.
    # OUTPUT: (None): None.
    # NOTE: One subprocess, not sixty. The graph above covers every module; this proves the graph
    # is asking about something real — a rule nobody can run is a rule nobody believes. error_utils
    # is the module that actually failed, so it is the one worth the third of a second.
    @pytest.mark.unit
    def test_error_utils_imports_alone_in_a_fresh_interpreter(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", "import project.core.error_utils"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr


# CLASS: tests.application.test_import_graph_is_acyclic.TestCycleDetectionMechanism
# SUMMARY: Verify the detector above finds a cycle and does not invent one, on a package built here.
# **LOGIC_STEP**: Without this the guard passes vacuously on a clean tree forever, and the day it
# matters is the day nobody knows whether it works. The fixture reproduces the exact shape of the
# error_utils defect above: a module importing a submodule of a package whose `__init__` imports
# it back.
class TestCycleDetectionMechanism:
    # FUNCTION: test_a_package_reimporting_its_importer_is_reported
    # SUMMARY: Verify the ancestor-package edge is what closes the cycle, as it did in the defect.
    # INPUT: tmp_path (Path): Throwaway package root.
    # OUTPUT: (None): None.
    @pytest.mark.unit
    def test_a_package_reimporting_its_importer_is_reported(self, tmp_path: Path) -> None:
        root = tmp_path / "sample"
        (root / "logging").mkdir(parents=True)
        (root / "__init__.py").write_text("", encoding="utf-8")
        (root / "helper.py").write_text(
            "from sample.logging.redaction import summarize\n", encoding="utf-8"
        )
        (root / "logging" / "__init__.py").write_text(
            "from sample.logging.writer import write\n", encoding="utf-8"
        )
        (root / "logging" / "writer.py").write_text(
            "from sample.helper import clean\n", encoding="utf-8"
        )
        (root / "logging" / "redaction.py").write_text("def summarize(): ...\n", encoding="utf-8")

        cycle = _first_cycle(_import_edges(root))

        assert cycle, "the detector missed a cycle it was built to find"
        assert "sample.logging" in cycle
        assert "sample.helper" in cycle

    # FUNCTION: test_siblings_inside_one_package_are_not_a_cycle
    # SUMMARY: Verify the shape the shipped logging package has is left alone.
    # INPUT: tmp_path (Path): Throwaway package root.
    # OUTPUT: (None): None.
    # NOTE: `project/core/logging/__init__.py` imports `logger`, and `logger` imports its sibling
    # `logger_events`. Counting the package as an edge for a module inside it would report that as
    # a cycle and turn a correct, shipped package red — the failure mode this repository treats as
    # worse than a miss.
    @pytest.mark.unit
    def test_siblings_inside_one_package_are_not_a_cycle(self, tmp_path: Path) -> None:
        root = tmp_path / "sample"
        (root / "logging").mkdir(parents=True)
        (root / "__init__.py").write_text("", encoding="utf-8")
        (root / "logging" / "__init__.py").write_text(
            "from sample.logging.writer import write\n", encoding="utf-8"
        )
        (root / "logging" / "writer.py").write_text(
            "from sample.logging.events import EVENT\n", encoding="utf-8"
        )
        (root / "logging" / "events.py").write_text("EVENT = 1\n", encoding="utf-8")

        assert _first_cycle(_import_edges(root)) == []
