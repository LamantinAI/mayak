# FILE: tests/functional/src/test_health_api.py
# SUMMARY: The kernel over HTTP in the built image: it serves, and it finds its migrated database ready.
# A project keeps this when the reference vertical goes: without it, `make test-e2e` would send no
# request at all through the image, its entrypoint and the migrations that entrypoint runs.

import pytest

from utils.helpers import SendRequest

pytestmark = pytest.mark.e2e


async def test_the_image_serves_and_reports_its_database_ready(
    make_get_request: SendRequest,
) -> None:
    live = await make_get_request("/health/")
    ready = await make_get_request("/health/ready")

    assert (live["status"], ready["status"]) == (200, 200)
    assert ready["body"]["checks"]["database"]["status"] == "healthy"
