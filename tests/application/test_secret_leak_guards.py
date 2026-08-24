# FILE: tests/application/test_secret_leak_guards.py
# SUMMARY: Regression guards for the two paths that leaked secrets to clients and to logs.

import logging
import re
import subprocess
from pathlib import Path

import orjson
import pytest
from starlette.testclient import TestClient

from project.core.composition_root import CompositionRoot
from project.core.config import clear_settings_override, set_settings_override
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


# CLASS: tests.application.test_secret_leak_guards.TestEnvSampleDebugFlag
# SUMMARY: Guard the shipped .env.sample against re-enabling Starlette's traceback response.
class TestEnvSampleDebugFlag:
    # FUNCTION: test_env_sample_does_not_enable_debug
    # SUMMARY: Verify .env.sample ships APP_DEBUG disabled so fresh projects never expose tracebacks.
    @pytest.mark.unit
    def test_env_sample_does_not_enable_debug(self) -> None:
        # **LOGIC_STEP**: dev_setup.sh copies this file verbatim into .env for every new project,
        # and APP_DEBUG=true makes Starlette answer 500s with its own traceback page while
        # silently disabling the wildcard-CORS and default-password guards in validate_runtime().
        assignments = [
            line.split("=", 1)[1].strip().strip("\"'").lower()
            for line in (_REPO_ROOT / ".env.sample").read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("APP_DEBUG=")
        ]

        assert assignments == ["false"]


# CLASS: tests.application.test_secret_leak_guards.TestExceptionTextRedaction
# SUMMARY: Verify exception text reaches logs scrubbed, matching the treatment of tracebacks.
class TestExceptionTextRedaction:
    # FUNCTION: test_log_error_redacts_exception_message
    # SUMMARY: Verify log_error scrubs secrets embedded in the exception's own text.
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

    # FUNCTION: test_log_critical_redacts_exception_message
    # SUMMARY: Verify log_critical applies the same scrubbing as log_error.
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

    # FUNCTION: test_log_error_redacts_message_argument
    # SUMMARY: Verify the free-form message is scrubbed too, since callers pass str(exception) into it.
    @pytest.mark.unit
    def test_log_error_redacts_message_argument(self, log_capture: list[dict]) -> None:
        # **LOGIC_STEP**: project/launcher/main.py logs message=str(e) with no exception object,
        # so the message field is a leak path in its own right.
        logger = get_logger("tests.application.test_secret_leak_guards")

        logger.log_error(error_type="configuration_error", message=f"bad dsn {_FAKE_DSN}")

        assert "hunter2" not in log_capture[0]["kwargs"]["data"]["message"]


# CLASS: tests.application.test_secret_leak_guards.TestFormatterRedactionNet
# SUMMARY: Verify the formatter scrubs text fields assembled outside the semantic logger.
class TestFormatterRedactionNet:
    # FUNCTION: test_formatter_redacts_exception_message_from_raw_payload
    # SUMMARY: Verify a hand-built payload bypassing the semantic logger is still scrubbed.
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


# CLASS: tests.application.test_secret_leak_guards.TestDebugFlagCannotPublishTracebacks
# SUMMARY: Verify APP_DEBUG=true no longer turns the HTTP surface into a traceback page.
class TestDebugFlagCannotPublishTracebacks:
    # FUNCTION: test_application_never_runs_with_starlette_debug
    # SUMMARY: Verify the assembled app keeps Starlette's debug off even when APP_DEBUG is on.
    @pytest.mark.unit
    def test_application_never_runs_with_starlette_debug(self) -> None:
        # **LOGIC_STEP**: Starlette answers unhandled exceptions in ServerErrorMiddleware, which
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

    # FUNCTION: test_unhandled_exception_answers_with_the_safe_message
    # SUMMARY: Verify a raising route returns the fixed envelope, not the exception text.
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


# CLASS: tests.application.test_secret_leak_guards.TestRedactionCoversRealCredentialShapes
# SUMMARY: Verify the scrubber matches the credential shapes this project actually handles.
class TestRedactionCoversRealCredentialShapes:
    # FUNCTION: test_driver_qualified_dsn_is_redacted
    # SUMMARY: Verify the SQLAlchemy async DSN form is scrubbed, not only the bare scheme.
    @pytest.mark.unit
    def test_driver_qualified_dsn_is_redacted(self) -> None:
        # **LOGIC_STEP**: The pattern required the scheme name to touch `://`, so a driver-qualified
        # scheme like `postgresql+psycopg://` was the single DSN form that passed through
        # untouched. This is the shape config_builders.py produces, and the one most likely to
        # surface in a traceback here.
        text = "url = postgresql+psycopg://appuser:hunter2@db.internal:5432/app"  # allow-secret: fixture for the scrubber

        assert "hunter2" not in redact_secrets(text)

    # FUNCTION: test_bare_credentials_without_an_assignment_are_redacted
    # SUMMARY: Verify tokens carrying no `key=` prefix are still scrubbed.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "secret",
        [
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",  # allow-secret: fixture for the scrubber
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",  # allow-secret: fixture for the scrubber
            "glpat-abcdefghijklmnopqrst",  # allow-secret: fixture for the scrubber
            "xoxb-1234567890-abcdefghij",  # allow-secret: fixture for the scrubber
            "AKIAIOSFODNN7EXAMPLE",  # allow-secret: fixture for the scrubber
            # **LOGIC_STEP**: measured 2026-08-24 — delete the one line in redaction.py this
            # fixture exists to prove, `re.compile(r"sk-[A-Za-z0-9]{20,}")`, and rerun the two
            # dedicated secret-guard files with `--no-cov`:
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
            # NOTE: this comment used to state "the rest of the suite" as an absolute test
            # count. It drifted twice in one session (760 -> 737 -> 741) as unrelated concurrent
            # work added tests elsewhere under tests/application, and no gate rereads a comment
            # to catch a stale one — so the count is gone; the claim above needs only the shape
            # of the result, which stays true at any suite size.
            "sk-" + "a" * 32,  # allow-secret: fixture for the scrubber
            # **LOGIC_STEP**: `sk_test_` with sixteen characters, not `sk_live_` with twenty-four.
            # The longer live shape is what GitHub push protection matches, and it refused the
            # first push of this repository over this very line — a template whose first push is
            # blocked is broken for everyone who clones it. Sixteen still clears the `{16,}` in
            # `redaction.py`, so the fixture proves exactly what it did before.
            "sk_test_abcdefghijklmnop",  # allow-secret: fixture for the scrubber
        ],
    )
    def test_bare_credentials_without_an_assignment_are_redacted(self, secret: str) -> None:
        assert secret not in redact_secrets(f"provider rejected {secret} at 12:00")


# CLASS: tests.application.test_secret_leak_guards.TestNothingTrippedByGithubPushProtection
# SUMMARY: Verify no tracked file carries a credential shape that GitHub refuses to accept on push.
# NOTE: The first push of this repository was rejected: GH013, "Push cannot contain secrets",
# pointing at a synthetic `sk_live_` fixture in this file. It was never a real key — GitHub matches
# the shape, not the account — but the shape is enough to block the push, and a template that
# cannot be pushed is broken for everyone who clones it. The provider patterns below are the ones
# with a fixed, publicly documented prefix and length; a scanner-shaped fixture must sit outside
# them, which for a test costs one character of length or one letter of prefix.
class TestNothingTrippedByGithubPushProtection:
    # ATTRIBUTE: BLOCKED_SHAPES (tuple[tuple[str, str], ...])
    # SUMMARY: Provider name paired with the pattern GitHub's push protection matches.
    BLOCKED_SHAPES = (
        ("Stripe live key", r"sk_live_[0-9a-zA-Z]{24,}"),
        ("Stripe restricted key", r"rk_live_[0-9a-zA-Z]{24,}"),
        ("Slack bot token", r"xoxb-[0-9]{11,}-[0-9]{11,}-[0-9a-zA-Z]{24,}"),
        ("OpenAI key", r"sk-[a-zA-Z0-9]{48}"),
    )

    # FUNCTION: test_no_tracked_file_carries_a_blocked_credential_shape
    # SUMMARY: Verify a fresh clone of this template can be pushed to GitHub without an unblock click.
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
            # **LOGIC_STEP**: This file names the shapes it forbids, so it is the one exemption —
            # the same carve-out the sweep guard above makes for itself.
            if name == "tests/application/test_secret_leak_guards.py":
                continue
            if pattern.search(body):
                offenders.append(name)

        assert offenders == [], (
            f"{provider} shape found in tracked files; GitHub push protection will refuse the "
            f"push: {offenders}"
        )
