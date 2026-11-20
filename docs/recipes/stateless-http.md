# Stateless HTTP

Bootstrap with `DB=none`. For an endpoint with no service dependencies, add a router in
`project/infrastructure/api/endpoints/preview.py`:

```python
# FILE: project/infrastructure/api/endpoints/preview.py
# SUMMARY: Validate and normalize a title without storage.

from fastapi import APIRouter
from pydantic import BaseModel, Field


class PreviewRequest(BaseModel):
    title: str = Field(min_length=1)
    limit: int = Field(default=80, ge=1, le=200, strict=True)
    uppercase: bool = Field(default=False, strict=True)


class PreviewResponse(BaseModel):
    title: str


preview_router = APIRouter()


@preview_router.post("/preview")
async def preview(body: PreviewRequest) -> PreviewResponse:
    title = body.title.upper() if body.uppercase else body.title.lower()
    return PreviewResponse(title=title[:body.limit])
```

Keep wire types strict where the distinction matters. Otherwise JSON `true` can become
an integer or `"false"` can become a boolean before domain validation.

Import and include `preview_router` unconditionally in
`project/infrastructure/api/router_registration.py`. Add the HTTP test under `tests/application`:

```python
# FILE: tests/application/test_preview.py
# SUMMARY: Check the stateless router and invalid input through the ASGI app.

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


async def test_title_preview(app_without_postgres: FastAPI) -> None:
    transport = ASGITransport(app=app_without_postgres)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        result = await client.post("/preview", json={"title": "Room A"})
        assert result.status_code == 200
        assert result.json() == {"title": "room a"}
        invalid = await client.post("/preview", json={"title": ""})
        assert invalid.status_code == 422
```

Add a test under `tests/functional/src/test_preview_api.py` that checks the new behavior in the
built image as well as input rejection:

```python
# FILE: tests/functional/src/test_preview_api.py
# SUMMARY: Check the new preview options over HTTP in the built image.

import pytest

from utils.helpers import SendRequest

pytestmark = pytest.mark.e2e


async def test_preview_options_in_image(make_post_request: SendRequest) -> None:
    preview = await make_post_request("/preview", {"title": "Room A", "limit": 4, "uppercase": True})
    assert (preview["status"], preview["body"]) == (200, {"title": "ROOM"})

    invalid = await make_post_request("/preview", {"title": "Room A", "limit": True})
    assert invalid["status"] == 422
    invalid = await make_post_request("/preview", {"title": "Room A", "uppercase": "false"})
    assert invalid["status"] == 422
```

Use `make smoke-local` to check the running service and `make verify` to exercise the working
change. For a committed change, pass `DIFF_COMPARE_BRANCH=<baseline>` to reproduce coverage and
E2E selection. If business rules grow, put them in an application service and use the typed
dependency and registry shape from the vertical skill.
