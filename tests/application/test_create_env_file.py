# FILE: tests/application/test_create_env_file.py
# SUMMARY: Tests for the .env creation step — what it generates, what it copies, what it refuses to touch.

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from project.core.config_runtime import _sample_placeholder_secrets
from scripts.create_env_file import create_env_file, render_env_from_sample
from tests.conftest import _FixtureSettings as FixtureSettings

# ATTRIBUTE: _REPO_ROOT (Path)
# SUMMARY: Repository root, so the tests read the sample file this repository actually ships.
_REPO_ROOT = Path(__file__).resolve().parents[2]


# FUNCTION: _password_in
# SUMMARY: Read the value of the POSTGRES_PASSWORD assignment out of an env-file body.
# INPUT: text (str): Full text of an env file.
# OUTPUT: (str): The assigned value, without its line ending.
def _password_in(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("POSTGRES_PASSWORD="):
            return line.split("=", 1)[1]
    raise AssertionError("no POSTGRES_PASSWORD assignment in the rendered file")


# FUNCTION: _checkout
# SUMMARY: Build a throwaway checkout holding this repository's real .env.sample.
# INPUT: tmp_path (Path): pytest's per-test directory.
# OUTPUT: (Path): Root of the throwaway checkout.
def _checkout(tmp_path: Path) -> Path:
    sample = (_REPO_ROOT / ".env.sample").read_text(encoding="utf-8")
    (tmp_path / ".env.sample").write_text(sample, encoding="utf-8")
    return tmp_path


# CLASS: tests.application.test_create_env_file.TestTheGeneratedPassword
# SUMMARY: Verify the one value that is not copied is generated, and that it satisfies the guard.
# NOTE: A plain `cp .env.sample .env` carried the placeholder password into a fresh checkout, and
# validate_runtime() refuses to start on it. The container exited 1 during the README's own quick
# start, which is the worst possible place for a correct guard to fire.
class TestTheGeneratedPassword:
    # FUNCTION: test_the_sample_placeholder_never_reaches_the_env_file
    # SUMMARY: Verify the value the guard rejects is not what a fresh checkout receives.
    @pytest.mark.unit
    def test_the_sample_placeholder_never_reaches_the_env_file(self, tmp_path: Path) -> None:
        root = _checkout(tmp_path)

        create_env_file(root)

        placeholder = _sample_placeholder_secrets()["POSTGRES_PASSWORD"]
        assert _password_in((root / ".env").read_text(encoding="utf-8")) != placeholder

    # FUNCTION: test_the_generated_password_starts_a_production_service
    # SUMMARY: Verify the produced value passes the very guard that rejected the placeholder.
    # **LOGIC_STEP**: Asserting "not the placeholder" alone would pass for an empty string or the
    # image's own `postgres`, both of which the guard also rejects. Feeding the value to
    # validate_runtime() is the assertion that matches what a fresh checkout actually needs.
    @pytest.mark.unit
    def test_the_generated_password_starts_a_production_service(self, tmp_path: Path) -> None:
        root = _checkout(tmp_path)
        create_env_file(root)

        settings = FixtureSettings()
        settings.project.debug = False
        settings.server.cors_origins = ["https://example.com"]
        settings.postgres.enabled = True
        settings.agent.llm_mode = "mock"
        settings.postgres.password = SecretStr(
            _password_in((root / ".env").read_text(encoding="utf-8"))
        )

        settings.validate_runtime()

        assert settings.postgres.password.get_secret_value() != ""

    # FUNCTION: test_two_checkouts_do_not_share_a_password
    # SUMMARY: Verify the value is generated per checkout rather than a constant that looks random.
    @pytest.mark.unit
    def test_two_checkouts_do_not_share_a_password(self, tmp_path: Path) -> None:
        sample = (_REPO_ROOT / ".env.sample").read_text(encoding="utf-8")

        first = render_env_from_sample(sample)
        second = render_env_from_sample(sample)

        assert _password_in(first) != _password_in(second)


# CLASS: tests.application.test_create_env_file.TestEverythingElseIsLeftAlone
# SUMMARY: Verify the step copies the sample faithfully and never overwrites existing credentials.
class TestEverythingElseIsLeftAlone:
    # FUNCTION: test_only_the_password_line_differs_from_the_sample
    # SUMMARY: Verify comments, blank lines and every other variable survive the copy untouched.
    @pytest.mark.unit
    def test_only_the_password_line_differs_from_the_sample(self) -> None:
        sample = (_REPO_ROOT / ".env.sample").read_text(encoding="utf-8")

        rendered = render_env_from_sample(sample)

        differing = [
            (before, after)
            for before, after in zip(sample.splitlines(), rendered.splitlines())
            if before != after
        ]
        assert len(sample.splitlines()) == len(rendered.splitlines())
        assert [before.split("=", 1)[0] for before, _ in differing] == ["POSTGRES_PASSWORD"]

    # FUNCTION: test_a_commented_example_is_not_rewritten
    # SUMMARY: Verify a commented-out assignment stays documentation instead of becoming a value.
    @pytest.mark.unit
    def test_a_commented_example_is_not_rewritten(self) -> None:
        commented = "# POSTGRES_PASSWORD=what-this-looks-like\nPOSTGRES_PASSWORD=placeholder\n"

        rendered = render_env_from_sample(commented)

        assert rendered.startswith("# POSTGRES_PASSWORD=what-this-looks-like\n")

    # FUNCTION: test_an_existing_env_file_is_left_exactly_as_it_was
    # SUMMARY: Verify rerunning setup cannot destroy credentials someone already put in place.
    @pytest.mark.unit
    def test_an_existing_env_file_is_left_exactly_as_it_was(self, tmp_path: Path) -> None:
        root = _checkout(tmp_path)
        mine = "POSTGRES_PASSWORD=the-one-my-database-actually-uses\n"
        (root / ".env").write_text(mine, encoding="utf-8")

        outcome = create_env_file(root)

        assert outcome == "exists"
        assert (root / ".env").read_text(encoding="utf-8") == mine
