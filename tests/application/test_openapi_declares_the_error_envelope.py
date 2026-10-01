# FILE: tests/application/test_openapi_declares_the_error_envelope.py
# SUMMARY: A 422 on the wire is the body the OpenAPI schema declares for it.

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import BaseModel


class _OwnReason(BaseModel):
    reason: str


# A value against an OpenAPI schema, as far as these schemas go: objects with no key beyond their
# properties, arrays, strings, integers, anything, $ref and anyOf.
def _conforms(value: Any, schema: dict[str, Any], components: dict[str, Any]) -> bool:
    if "$ref" in schema:
        return _conforms(value, components[schema["$ref"].rsplit("/", 1)[-1]], components)
    if "anyOf" in schema:
        return any(_conforms(value, option, components) for option in schema["anyOf"])
    kind = schema.get("type")
    if kind == "object":
        fields = schema.get("properties", {})
        return (
            isinstance(value, dict)
            and set(schema.get("required", [])) <= value.keys() <= fields.keys()
            and all(_conforms(item, fields[key], components) for key, item in value.items())
        )
    if kind == "array":
        items = schema.get("items", {})
        return isinstance(value, list) and all(_conforms(item, items, components) for item in value)
    return kind is None or isinstance(
        value, {"string": str, "integer": int, "null": type(None)}[kind]
    )


# FastAPI declared every 422 as its own `{"detail": [...]}` while the handler answers
# `{"error": {...}}`: a client generated from the schema parsed a body it never received
# (GPT-6 Astra, 2026-09-27). The body is checked against what the schema declares, and a 422 a
# route declared itself keeps its own schema.
async def test_a_real_422_is_the_body_the_schema_declares(
    fastapi_app: FastAPI, async_client: AsyncClient
) -> None:
    @fastapi_app.get("/__probe_422")
    async def _probe(count: int) -> dict[str, int]:
        return {"count": count}

    @fastapi_app.get("/__probe_own_422", responses={422: {"model": _OwnReason}})
    async def _own(count: int) -> dict[str, int]:
        return {"count": count}

    response = await async_client.get("/__probe_422", params={"count": "many"})

    schema = fastapi_app.openapi()
    paths, components = schema["paths"], schema["components"]["schemas"]
    declared = paths["/__probe_422"]["get"]["responses"]["422"]["content"]["application/json"]
    own = paths["/__probe_own_422"]["get"]["responses"]["422"]["content"]["application/json"]
    assert response.status_code == 422
    assert _conforms(response.json(), declared["schema"], components), declared
    assert own["schema"] == {"$ref": "#/components/schemas/_OwnReason"}
