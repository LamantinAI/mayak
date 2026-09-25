# FILE: tests/application/test_secret_leak_guards.py
# SUMMARY: Regression guards for the two paths that leaked secrets to clients and to logs.

import io
import logging
import re
import subprocess
from pathlib import Path

import orjson
import pytest
from starlette.testclient import TestClient

from project.core.composition_root import CompositionRoot
from project.core.config import (
    GUARDS_RELAXED_BY_DEBUG,
    clear_settings_override,
    set_settings_override,
)
from project.core.logging import get_logger
from project.core.logging.formatters import NDJSONFormatter
from project.core.logging.redaction import redact_secrets
from project.infrastructure.api.exception_handlers import SAFE_INTERNAL_ERROR_MESSAGE
from tests.conftest import _FixtureSettings as FixtureSettings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FAKE_DSN = (
    "postgresql://appuser:hunter2@db.internal:5432/app"  # allow-secret: fixture for the scrubber
)
_FAKE_KEY = "sk-abcdefghijklmnopqrstuvwxyz012345"  # allow-secret: fixture for the scrubber


# Guard the shipped .env.sample against re-enabling Starlette's traceback response.
class TestEnvSampleDebugFlag:
    @pytest.mark.unit
    def test_env_sample_does_not_enable_debug(self) -> None:
        # dev_setup.sh copies this file verbatim into .env for every new project,
        # and APP_DEBUG=true switches off every guard in GUARDS_RELAXED_BY_DEBUG at once. (It no
        # longer hands out Starlette's traceback page — the class below pins that — but the
        # guards alone are reason enough for the sample to ship it off.)
        assignments = [
            line.split("=", 1)[1].strip().strip("\"'").lower()
            for line in (_REPO_ROOT / ".env.sample").read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("APP_DEBUG=")
        ]

        assert assignments == ["false"]


# Verify exception text reaches logs scrubbed, matching the treatment of tracebacks.
class TestExceptionTextRedaction:
    @pytest.mark.unit
    def test_log_error_redacts_exception_message(self, log_capture: list[dict]) -> None:
        logger = get_logger("tests.application.test_secret_leak_guards")

        logger.log_error(
            error_type="llm_call_failed",
            message="LLM call failed permanently",
            exception=RuntimeError(f"auth rejected for api_key={_FAKE_KEY}"),
        )

        payload = log_capture[0]["kwargs"]["data"]
        assert _FAKE_KEY not in payload["exception_message"]
        assert "***REDACTED***" in payload["exception_message"]

    # Verify log_critical applies the same scrubbing as log_error.
    @pytest.mark.unit
    def test_log_critical_redacts_exception_message(self, log_capture: list[dict]) -> None:
        logger = get_logger("tests.application.test_secret_leak_guards")

        logger.log_critical(
            failure_type="unexpected_server_error",
            message="Unexpected server error occurred",
            impact_scope="request",
            exception=ConnectionError(f"could not connect to {_FAKE_DSN}"),
        )

        payload = log_capture[0]["kwargs"]["data"]
        assert "hunter2" not in payload["exception_message"]

    @pytest.mark.unit
    def test_log_error_redacts_message_argument(self, log_capture: list[dict]) -> None:
        # project/launcher/main.py logs message=str(e) with no exception object,
        # so the message field is a leak path in its own right.
        logger = get_logger("tests.application.test_secret_leak_guards")

        logger.log_error(error_type="configuration_error", message=f"bad dsn {_FAKE_DSN}")

        assert "hunter2" not in log_capture[0]["kwargs"]["data"]["message"]


# Verify the formatter scrubs text fields assembled outside the semantic logger.
class TestFormatterRedactionNet:
    @pytest.mark.unit
    def test_formatter_redacts_exception_message_from_raw_payload(self) -> None:
        record = logging.LogRecord(
            name="tests.raw",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=None,
        )
        record.payload = {
            "event_id": "error.raw",
            "exception_message": f"token={_FAKE_KEY}",
        }

        entry = orjson.loads(NDJSONFormatter().format(record))

        assert _FAKE_KEY not in entry["data"]["exception_message"]

    # The record's own text: a library's logging.warning(...) never passes the semantic logger.
    @pytest.mark.unit
    @pytest.mark.parametrize("level", [logging.WARNING, logging.ERROR])
    def test_formatter_redacts_the_text_of_a_warning_or_worse(self, level: int) -> None:
        record = logging.LogRecord(
            name="psycopg.pool",
            level=level,
            pathname=__file__,
            lineno=1,
            msg="error connecting: %s",
            args=(_FAKE_DSN,),
            exc_info=None,
        )

        entry = orjson.loads(NDJSONFormatter().format(record))

        assert "hunter2" not in entry["msg"]

    # INFO is most of the volume and left as it is — the cost is in the formatter's comment.
    @pytest.mark.unit
    def test_formatter_leaves_info_text_as_it_is(self) -> None:
        record = logging.LogRecord(
            name="tests.raw",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="opened %s",
            args=(_FAKE_DSN,),
            exc_info=None,
        )

        entry = orjson.loads(NDJSONFormatter().format(record))

        assert entry["msg"] == f"opened {_FAKE_DSN}"

    @pytest.mark.unit
    def test_log_warning_keeps_a_dsn_out_of_the_record_text(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(NDJSONFormatter())
        target = logging.getLogger("project.tests.warning_probe")
        target.addHandler(handler)
        try:
            get_logger("project.tests.warning_probe").log_warning(
                warning_type="database_health_check_failed",
                message=f"Database health check failed: {_FAKE_DSN}",
                affected_component="database",
            )
        finally:
            target.removeHandler(handler)

        assert "hunter2" not in stream.getvalue()


class TestDebugFlagCannotPublishTracebacks:
    @pytest.mark.unit
    def test_application_never_runs_with_starlette_debug(self) -> None:
        # Starlette answers unhandled exceptions in ServerErrorMiddleware, which
        # checks its own debug flag first and renders the full traceback — file paths, locals, and
        # whatever secret was in scope — without ever reaching the application's registered
        # Exception handler. APP_DEBUG is documented as verbose logging and a single worker, so
        # wiring it into FastAPI(debug=...) meant one documented convenience silently published
        # internals over HTTP.
        settings = FixtureSettings()
        settings.project.debug = True
        set_settings_override(settings)
        try:
            app = CompositionRoot().build_application()
        finally:
            clear_settings_override()

        assert app.debug is False

    @pytest.mark.unit
    def test_the_startup_event_names_the_guards_debug_relaxed(
        self, log_capture: list[dict]
    ) -> None:
        # README described the flag wrongly for months and nothing noticed; a
        # container started with APP_DEBUG=true now names the three guards itself, in the same
        # event that already reported `debug: true`, so the operator reading the log at the
        # moment it matters does not depend on the README having been right.
        settings = FixtureSettings()
        settings.project.debug = True
        set_settings_override(settings)
        try:
            CompositionRoot().build_application()
        finally:
            clear_settings_override()

        built = [
            event
            for event in log_capture
            if event["kwargs"].get("event_id") == "system.fastapi_application_built"
        ]
        assert len(built) == 1
        assert built[0]["kwargs"]["data"]["new_value"]["guards_relaxed_by_debug"] == list(
            GUARDS_RELAXED_BY_DEBUG
        )

    @pytest.mark.unit
    def test_a_production_build_reports_no_relaxed_guard(self, log_capture: list[dict]) -> None:
        settings = FixtureSettings()
        settings.project.debug = False
        set_settings_override(settings)
        try:
            CompositionRoot().build_application()
        finally:
            clear_settings_override()

        built = next(
            event
            for event in log_capture
            if event["kwargs"].get("event_id") == "system.fastapi_application_built"
        )
        assert built["kwargs"]["data"]["new_value"]["guards_relaxed_by_debug"] == []

    @pytest.mark.unit
    def test_unhandled_exception_answers_with_the_safe_message(self) -> None:
        settings = FixtureSettings()
        settings.project.debug = True
        set_settings_override(settings)
        try:
            app = CompositionRoot().build_application()

            @app.get("/boom")
            async def _boom() -> None:
                raise RuntimeError(f"connection failed for {_FAKE_DSN}")

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/boom")
        finally:
            clear_settings_override()

        assert response.status_code == 500
        assert response.json()["error"]["message"] == SAFE_INTERNAL_ERROR_MESSAGE
        assert "hunter2" not in response.text
        assert "Traceback" not in response.text


class TestRedactionCoversRealCredentialShapes:
    @pytest.mark.unit
    def test_driver_qualified_dsn_is_redacted(self) -> None:
        # The pattern required the scheme name to touch `://`, so a driver-qualified
        # scheme like `postgresql+psycopg://` was the single DSN form that passed through
        # untouched. This is the shape config_builders.py produces, and the one most likely to
        # surface in a traceback here.
        text = "url = postgresql+psycopg://appuser:hunter2@db.internal:5432/app"  # allow-secret: fixture for the scrubber

        assert "hunter2" not in redact_secrets(text)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "secret",
        [
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",  # allow-secret: fixture for the scrubber
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",  # allow-secret: fixture for the scrubber
            "glpat-abcdefghijklmnopqrst",  # allow-secret: fixture for the scrubber
            "xoxb-1234567890-abcdefghij",  # allow-secret: fixture for the scrubber
            "AKIAIOSFODNN7EXAMPLE",  # allow-secret: fixture for the scrubber
            # to verify this fixture catches a regression, delete the one line it
            # exists to prove in redaction.py, `re.compile(r"sk-[A-Za-z0-9]{20,}")`, and rerun the
            # two dedicated secret-guard files with `--no-cov`:
            # `uv run python -m pytest tests/application/test_secret_leak_guards.py
            # tests/application/test_logging_redaction.py -q --no-cov`
            # Only this one parametrize case fails. Every other test in both files, every other
            # test under tests/application, and scripts/validate_secrets.py stay green — nothing
            # else in the suite covers this pattern. Restore the deleted line afterwards with
            # `git checkout -- project/core/logging/redaction.py`. The module-level `_FAKE_KEY`
            # fixtures above never exercised this pattern: every use embeds it as
            # `api_key={_FAKE_KEY}`, an assignment shape the separate
            # `(api[_-]?key|token|secret|password|authorization)\s*[=:]\s*...` pattern catches on
            # its own, so those tests stayed green for the wrong reason. A bare key with no
            # `key=` prefix is also the shape most likely to actually leak here — it is what an
            # `openai.AuthenticationError` message carries, and this template's default provider
            # is OpenAI-compatible. Thirty-two characters after the prefix, not GitHub push
            # protection's forty-eight (see BLOCKED_SHAPES below): still clears the `{20,}` in
            # `redaction.py` while staying pushable.
            # an absolute test count for "the rest of the suite" drifts as unrelated
            # concurrent work adds tests elsewhere under tests/application, and no gate rereads
            # a comment to catch a stale one — so the claim above needs only the shape of the
            # result, which stays true at any suite size.
            "sk-" + "a" * 32,  # allow-secret: fixture for the scrubber
            # `sk_test_` with sixteen characters, not `sk_live_` with twenty-four.
            # The longer live shape is what GitHub push protection matches, and it refused the
            # first push of this repository over this very line — a template whose first push is
            # blocked is broken for everyone who clones it. Sixteen still clears the `{16,}` in
            # `redaction.py`, so the fixture proves exactly what it did before.
            "sk_test_abcdefghijklmnop",  # allow-secret: fixture for the scrubber
        ],
    )
    def test_bare_credentials_without_an_assignment_are_redacted(self, secret: str) -> None:
        assert secret not in redact_secrets(f"provider rejected {secret} at 12:00")


# The first push of this repository was rejected: GH013, "Push cannot contain secrets",
# pointing at a synthetic `sk_live_` fixture in this file. It was never a real key — GitHub matches
# the shape, not the account — but the shape is enough to block the push, and a template that
# cannot be pushed is broken for everyone who clones it. The provider patterns below are the ones
# with a fixed, publicly documented prefix and length; a scanner-shaped fixture must sit outside
# them, which for a test costs one character of length or one letter of prefix.
class TestNothingTrippedByGithubPushProtection:
    # Provider name paired with the pattern GitHub's push protection matches.
    BLOCKED_SHAPES = (
        ("Stripe live key", r"sk_live_[0-9a-zA-Z]{24,}"),
        ("Stripe restricted key", r"rk_live_[0-9a-zA-Z]{24,}"),
        ("Slack bot token", r"xoxb-[0-9]{11,}-[0-9]{11,}-[0-9a-zA-Z]{24,}"),
        ("OpenAI key", r"sk-[a-zA-Z0-9]{48}"),
    )

    @pytest.mark.unit
    @pytest.mark.parametrize("provider,shape", BLOCKED_SHAPES)
    def test_no_tracked_file_carries_a_blocked_credential_shape(
        self, provider: str, shape: str
    ) -> None:
        listed = subprocess.run(
            ["git", "ls-files"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        pattern = re.compile(shape)
        offenders = []
        for name in listed.stdout.split():
            path = _REPO_ROOT / name
            try:
                body = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            # This file names the shapes it forbids, so it is the one exemption —
            # the same carve-out the sweep guard above makes for itself.
            if name == "tests/application/test_secret_leak_guards.py":
                continue
            if pattern.search(body):
                offenders.append(name)

        assert offenders == [], (
            f"{provider} shape found in tracked files; GitHub push protection will refuse the "
            f"push: {offenders}"
        )
