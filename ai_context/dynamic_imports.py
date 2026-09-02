# FILE: ai_context/dynamic_imports.py
# SUMMARY: Recognise dynamic import calls so the import-boundary validators are not blind to them.
# NOTE: Both validators that police imports — scripts/validate_architecture.py (the domain
# allowlist) and scripts/validate_dependencies.py (the undeclared-dependency check) — walked the AST
# looking only at ast.Import and ast.ImportFrom. `importlib.import_module("psycopg")` is neither: it
# is an ordinary ast.Call, so one line lifted a forbidden driver into project/domain/ and pulled in
# an undeclared distribution with `make quality-gates` fully green. Measured on 2026-09-02: three
# lines added to project/domain/reference_task.py, gate exit 0, both validators reporting "passed".
# One trick disabled two gates at once, which is why the detection lives here once rather than in
# each of them.
#
# The names are resolved per FILE rather than per call, which is what the first version got wrong.
# Matching on the attribute name alone flagged `registry.import_module("psycopg")` on an unrelated
# object — a false positive an independent review reproduced the same day — and missed
# `from importlib import import_module as pull`, because the call no longer spells the name this
# module knows. Reading the file's own import statements first fixes both: a call counts only when
# the name it uses is bound to importlib in that file.

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# ATTRIBUTE: _IMPORTLIB_MODULE (str)
# SUMMARY: The module whose import functions this detector follows.
_IMPORTLIB_MODULE = "importlib"

# ATTRIBUTE: _IMPORT_FUNCTIONS (frozenset[str])
# SUMMARY: Functions in importlib whose first argument names a module to import.
_IMPORT_FUNCTIONS = frozenset({"import_module", "__import__"})

# ATTRIBUTE: _MODULE_ARGUMENT (str)
# SUMMARY: The keyword both functions accept for the module name, e.g. `__import__(name="psycopg")`.
_MODULE_ARGUMENT = "name"


# CLASS: ai_context.dynamic_imports._Bindings
# SUMMARY: The names one file has bound to importlib and to its import functions.
@dataclass
class _Bindings:
    # ATTRIBUTE: modules (set[str])
    # SUMMARY: Names that refer to the importlib module here — `importlib`, plus any `as` alias.
    modules: set[str] = field(default_factory=set)

    # ATTRIBUTE: functions (set[str])
    # SUMMARY: Bare names that call an import function — `__import__` always, plus whatever
    # `from importlib import import_module [as x]` bound in this file.
    functions: set[str] = field(default_factory=lambda: {"__import__"})


# FUNCTION: _collect_bindings
# SUMMARY: Read the file's import statements to learn which names lead to importlib.
# INPUT: tree (ast.AST): Parsed module.
# OUTPUT: (_Bindings): Names bound to the module and to its import functions in this file.
def _collect_bindings(tree: ast.AST) -> _Bindings:
    bindings = _Bindings()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # **LOGIC_STEP**: `import importlib.util` binds the name `importlib` too, so the
                # first segment is what matters unless an explicit alias renames the whole path.
                if alias.name == _IMPORTLIB_MODULE or alias.name.startswith(
                    f"{_IMPORTLIB_MODULE}."
                ):
                    bindings.modules.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module == _IMPORTLIB_MODULE:
            for alias in node.names:
                if alias.name in _IMPORT_FUNCTIONS:
                    bindings.functions.add(alias.asname or alias.name)
    return bindings


# FUNCTION: _module_name_argument
# SUMMARY: Return the literal module name a call passes, positionally or by keyword.
# INPUT: node (ast.Call): The call being inspected.
# OUTPUT: (str | None): The literal name, or None when it is absent or computed.
# NOTE: Only a literal is resolved. `importlib.import_module(name)` with a variable is deliberately
# ignored rather than guessed: the module is decided at runtime, and a validator that invented a
# name for it would report a violation nobody can act on. Same for a concatenation. That residue is
# the honest limit of static analysis here — a project that must forbid the dynamic form outright
# bans the call itself rather than trying to resolve its argument.
def _module_name_argument(node: ast.Call) -> str | None:
    candidates: list[ast.expr] = list(node.args[:1])
    candidates.extend(keyword.value for keyword in node.keywords if keyword.arg == _MODULE_ARGUMENT)
    for candidate in candidates:
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            return candidate.value or None
    return None


# FUNCTION: dynamic_import_targets
# SUMMARY: Return every module this file imports through a call rather than an import statement.
# INPUT: tree (ast.AST): Parsed module, walked twice — once for bindings, once for calls.
# OUTPUT: (list[tuple[str, int]]): Dotted module name and the line of the call that imports it.
# NOTE: What still escapes, listed so nobody has to rediscover it: a name reached indirectly
# (`getattr(importlib, "import_module")(...)`, a function passed as an argument and called
# elsewhere), a module name that is not a literal, and `exec("import x")`. Each needs data-flow
# analysis rather than a name lookup, and each is a deliberate act rather than the ordinary
# spelling an agent reaches for — which is the line this detector draws.
def dynamic_import_targets(tree: ast.AST) -> list[tuple[str, int]]:
    bindings = _collect_bindings(tree)
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        if isinstance(func, ast.Attribute):
            # **LOGIC_STEP**: The receiver has to be a name this file bound to importlib. Without
            # that check any object with a method called `import_module` was reported — measured,
            # and exactly the kind of false positive that teaches people to distrust a gate.
            receiver = func.value
            if not isinstance(receiver, ast.Name) or receiver.id not in bindings.modules:
                continue
            if func.attr not in _IMPORT_FUNCTIONS:
                continue
        elif isinstance(func, ast.Name):
            if func.id not in bindings.functions:
                continue
        else:
            continue

        target = _module_name_argument(node)
        if target:
            found.append((target, node.lineno))
    return found
