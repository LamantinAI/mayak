# FILE: tests/application/test_functional_request_helpers.py
# SUMMARY: Pin that the five request helpers in tests/functional/conftest.py read an empty 204
# body as text instead of handing zero bytes to a JSON parser.
# NOTE: This test lives here, not in tests/functional/, on purpose. The functional suite needs
# Docker and is excluded from the gate (`--ignore=tests/functional`), so a regression in its
# helpers ships past `make quality-gates` and surfaces twenty minutes later as a JSONDecodeError
# that looks like the caller's bug. FastAPI answers a 204 route with an empty body and a
# `Content-Type: application/json` header; the reference vertical has no 204 route, so the first
# one a project adds is the first caller to reach this. The real conftest is imported by module
# path — not copied — so this cannot drift away from the file it guards.

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FUNCTIONAL_DIR = _REPO_ROOT / "tests" / "functional"


# FUNCTION: _load_functional_conftest
# SUMMARY: Import tests/functional/conftest.py, the real one, with its own imports resolvable.
# OUTPUT: (ModuleType): The loaded module; its fixtures are still pytest-wrapped.
# NOTE: That conftest does `from settings import ...`, which only resolves with tests/functional
# on sys.path — that is how tests/functional/pytest.ini runs it and not how this suite does. Both
# entries are removed again so they cannot leak into a module collected later. The dotted name
# matters too: a bare `import conftest` would return the root conftest pytest has already cached.
def _load_functional_conftest() -> ModuleType:
    added = [path for path in (str(_REPO_ROOT), str(_FUNCTIONAL_DIR)) if path not in sys.path]
    sys.path[0:0] = added
    try:
        return importlib.import_module("tests.functional.conftest")
    finally:
        for path in added:
            sys.path.remove(path)


# CLASS: tests.application.test_functional_request_helpers._EmptyJsonResponseClient
# SUMMARY: Answer every verb the way FastAPI answers a 204 route: no body, JSON content type.
class _EmptyJsonResponseClient:
    # FUNCTION: _respond
    # SUMMARY: Return the 204 response shape, ignoring whatever the helper passed.
    async def _respond(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(204, headers={"content-type": "application/json"}, content=b"")

    post = get = put = patch = delete = _respond


# FUNCTION: test_a_request_helper_reads_an_empty_204_body_as_text
# SUMMARY: Verify each helper survives the empty body FastAPI sends with a JSON content type.
@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "helper_name",
    [
        "make_post_request",
        "make_get_request",
        "make_put_request",
        "make_patch_request",
        "make_delete_request",
    ],
)
async def test_a_request_helper_reads_an_empty_204_body_as_text(helper_name: str) -> None:
    conftest = _load_functional_conftest()
    # **LOGIC_STEP**: `__wrapped__` is the undecorated coroutine underneath the fixture, so the
    # helper is exercised without pytest's fixture machinery and without the Docker-bound
    # fixtures it would otherwise pull in.
    fixture_function = getattr(conftest, helper_name).__wrapped__

    send_request = await fixture_function(_EmptyJsonResponseClient())
    result = await send_request("/reference-tasks/00000000-0000-0000-0000-000000000000")

    assert result == {"status": 204, "body": ""}
