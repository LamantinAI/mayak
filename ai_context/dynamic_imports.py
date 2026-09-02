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

from __future__ import annotations

import ast

# ATTRIBUTE: _DYNAMIC_IMPORT_FUNCTIONS (frozenset[str])
# SUMMARY: Callable names whose first string argument names a module the interpreter will import.
_DYNAMIC_IMPORT_FUNCTIONS = frozenset({"import_module", "__import__"})


# FUNCTION: dynamic_import_target
# SUMMARY: Return the module a dynamic import call names, or None when the node is not one.
# INPUT: node (ast.AST): Any AST node; only Call nodes can yield a target.
# OUTPUT: (str | None): Dotted module name taken from a literal first argument, else None.
# NOTE: Only a literal argument is resolved. `importlib.import_module(name)` with a variable is
# deliberately ignored rather than guessed: the module is decided at runtime, and a validator that
# invented a name for it would report a violation nobody can act on. That residue is the honest
# limit of static analysis here, not an oversight — a project that must forbid the dynamic form
# outright bans the call itself rather than trying to resolve its argument.
#
# Both call shapes are recognised: an attribute call (`importlib.import_module(...)`, and the same
# through any alias of the module) and a bare name call (`import_module(...)` after
# `from importlib import import_module`, plus the builtin `__import__`). What escapes is the
# function itself renamed at import — `from importlib import import_module as pull` — which needs
# per-file alias tracking; the same escape hatch exists in every AST-based import linter.
def dynamic_import_target(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call) or not node.args:
        return None

    func = node.func
    if isinstance(func, ast.Attribute):
        name = func.attr
    elif isinstance(func, ast.Name):
        name = func.id
    else:
        return None

    if name not in _DYNAMIC_IMPORT_FUNCTIONS:
        return None

    first_argument = node.args[0]
    if isinstance(first_argument, ast.Constant) and isinstance(first_argument.value, str):
        return first_argument.value or None
    return None
