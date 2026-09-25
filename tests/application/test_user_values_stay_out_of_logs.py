# FILE: tests/application/test_user_values_stay_out_of_logs.py
# SUMMARY: What a client sent goes back in the response that rejects it and nowhere into the log — ADR-013.
# Each case names a synthetic value in the rejection's own text, the way a 409 names the title or a
# check quotes the value, and reads the NDJSON the project's loggers write at INFO — the level
# production keeps. Measured on a live server before the fix: a berth's name in 100 log lines after
# 50 conflicting requests.

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, field_validator

from project.core.logging import get_logger
from project.core.logging.formatters import NDJSONFormatter
from project.domain.exceptions import ConflictError, ValidationError

_VALUE = "Synthetic-Private-Value-7731"

# Under project.infrastructure, like a repository's own span, so its records reach the handler below.
_repository_logger = get_logger("project.infrastructure.persistence.probe")


@contextmanager
def _project_log() -> Iterator[StringIO]:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(NDJSONFormatter())
    target = logging.getLogger("project")
    level = target.level
    target.addHandler(handler)
    target.setLevel(logging.INFO)
    try:
        yield stream
    finally:
        target.removeHandler(handler)
        target.setLevel(level)


class _NamedCustomer(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _known(cls, value: str) -> str:
        raise ValueError(f"no customer called {value}")


async def _request(app: FastAPI, method: str, path: str, **kwargs: object) -> tuple[int, str, str]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    with _project_log() as log:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, path, **kwargs)  # type: ignore[arg-type]
    return response.status_code, response.text, log.getvalue()


class TestARejectionKeepsTheValueOutOfTheLog:
    @pytest.mark.unit
    async def test_a_conflict_naming_the_value(self, fastapi_app: FastAPI) -> None:
        async def _insert() -> None:
            with _repository_logger.span("db.probe.insert", level=logging.INFO):
                raise ConflictError(f"a task titled {_VALUE!r} is already open")

        fastapi_app.add_api_route("/__conflict", _insert, methods=["POST"])

        status, body, log = await _request(fastapi_app, "POST", "/__conflict")

        assert status == 409
        assert _VALUE in body
        assert _VALUE not in log
        assert "ConflictError" in log

    @pytest.mark.unit
    async def test_a_domain_check_quoting_the_value(self, fastapi_app: FastAPI) -> None:
        async def _update() -> None:
            with _repository_logger.span("db.probe.update", level=logging.INFO):
                raise ValidationError(f"status must be pending or done, got {_VALUE}", "status")

        fastapi_app.add_api_route("/__domain_check", _update, methods=["PATCH"])

        status, body, log = await _request(fastapi_app, "PATCH", "/__domain_check")

        assert status == 422
        assert _VALUE in body
        assert _VALUE not in log
        assert "ValidationError on status" in log

    @pytest.mark.unit
    async def test_a_field_check_quoting_the_value(self, fastapi_app: FastAPI) -> None:
        async def _create(customer: _NamedCustomer) -> None:
            return None

        fastapi_app.add_api_route("/__field_check", _create, methods=["POST"])

        status, body, log = await _request(
            fastapi_app, "POST", "/__field_check", json={"name": _VALUE}
        )

        assert status == 422
        assert _VALUE in body
        assert _VALUE not in log
        assert '"value_error"' in log

    @pytest.mark.unit
    async def test_an_http_exception_naming_the_value(self, fastapi_app: FastAPI) -> None:
        async def _lookup() -> None:
            raise HTTPException(404, f"no customer called {_VALUE}")

        fastapi_app.add_api_route("/__lookup", _lookup, methods=["GET"])

        status, body, log = await _request(fastapi_app, "GET", "/__lookup")

        assert status == 404
        assert _VALUE in body
        assert _VALUE not in log
