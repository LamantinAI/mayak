# FILE: tests/application/test_openapi_declares_the_error_envelope.py
# SUMMARY: A 422 on the wire is the shape the OpenAPI schema declares for it.

from fastapi import FastAPI
from httpx import AsyncClient

from project.infrastructure.api.exception_handlers import ErrorEnvelope


# FastAPI declared every 422 as its own `{"detail": [...]}` while the handler answers
# `{"error": {...}}`: a client generated from the schema parsed a body it never received
# (GPT-6 Astra, 2026-09-27). The body is validated against the declared model with extra keys
# refused, so a field either side adds alone fails here.
async def test_a_real_422_is_the_body_the_schema_declares(
    fastapi_app: FastAPI, async_client: AsyncClient
) -> None:
    @fastapi_app.get("/__probe_422")
    async def _probe(count: int) -> dict[str, int]:
        return {"count": count}

    response = await async_client.get("/__probe_422", params={"count": "many"})

    declared = fastapi_app.openapi()["paths"]["/__probe_422"]["get"]["responses"]["422"]
    assert declared["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorEnvelope"
    }
    assert response.status_code == 422
    assert ErrorEnvelope.model_validate(response.json()).error.details
