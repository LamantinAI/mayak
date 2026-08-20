# FILE: tests/application/test_validate_secrets.py
# SUMMARY: Tests for the credential scanner: what it must catch, and what it must not shout about.
#
# allow-secret-file: every credential-shaped literal here is a synthetic fixture written to exercise
# the scanner. Whole-file scope, not per-line, because `ruff format` reflows a parametrized case and
# carries a trailing marker onto the closing bracket, a line away from the literal it was marking.

from pathlib import Path

import pytest

from scripts.validate_secrets import (
    ALLOW_FILE_MARKER,
    ALLOW_MARKER,
    SECRET_RULE_ID,
    collect_secret_issues,
    get_secrets_rule_playbook,
    scan_line,
)


# CLASS: tests.application.test_validate_secrets.TestScanLine
# SUMMARY: Line-level decisions — the precision/recall trade-off lives here.
class TestScanLine:
    # FUNCTION: test_provider_key_shapes_are_caught
    # SUMMARY: Each pattern must fire on the shape it was written for.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            (
                "-----BEGIN RSA PRIVATE KEY-----",
                "private key block",
            ),
            ("-----BEGIN PRIVATE KEY-----", "private key block"),
            (
                'aws_key = "AKIAIOSFODNN7SELIPQR"',
                "AWS access key id",
            ),
            ("token: ghp_" + "a" * 36, "GitHub personal access token"),
            ("slack = xoxb-1234567890-abcdefgh", "Slack token"),
            ("glpat-" + "b" * 24, "GitLab personal access token"),
            ("OPENAI_COMPATIBLE_API_KEY=sk-or-v1-" + "0a1b2c3d" * 8, "OpenRouter API key"),
        ],
    )
    def test_provider_key_shapes_are_caught(self, line: str, expected: str) -> None:
        assert scan_line(line) == expected

    # FUNCTION: test_ordinary_lines_are_left_alone
    # SUMMARY: A scanner that cries wolf gets switched off, so these must stay silent.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "line",
        [
            "POSTGRES_PASSWORD=changeme",
            'model = "gpt-4o"',
            "# the key is read from the environment",
            "OPENAI_COMPATIBLE_API_KEY=",
            'base_url = "https://llm.example.invalid/v1"',
            "def sk_helper(value: str) -> str:",
        ],
    )
    def test_ordinary_lines_are_left_alone(self, line: str) -> None:
        assert scan_line(line) is None

    # FUNCTION: test_placeholder_hints_suppress_the_match
    # SUMMARY: A key shape that spells out that it is an example is not a finding.
    @pytest.mark.unit
    def test_placeholder_hints_suppress_the_match(self) -> None:
        assert scan_line("api_key = sk-example-0000000000000000000000") is None

    # FUNCTION: test_a_word_elsewhere_on_the_line_does_not_silence_a_real_key
    # SUMMARY: Regression guard: the hint check ran against the whole line, so an ordinary comment
    # word — "invalid", "dummy", "test-key" — hid a live credential sitting next to it.
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "line",
        [
            'token = "AKIAABCDEFGHIJKLMNOP"  # rotate immediately if invalid',
            'SLACK_TOKEN = "xoxb-1234567890-abcdefgh"  # posted to your-team channel',
            'headers = {"Authorization": "Bearer sk-aaaaaaaaaaaaaaaaaaaaaaaa"}  # dummy header',
        ],
    )
    def test_a_word_elsewhere_on_the_line_does_not_silence_a_real_key(self, line: str) -> None:
        assert scan_line(line) is not None

    # FUNCTION: test_dashed_internal_identifier_is_not_an_openai_key
    # SUMMARY: Regression guard: `sk-` followed by dashed words is an internal id, not a credential.
    @pytest.mark.unit
    def test_dashed_internal_identifier_is_not_an_openai_key(self) -> None:
        assert scan_line('session_key = "sk-onboarding-flow-token-abc123def456"') is None
        assert scan_line("api_key = sk-" + "a" * 24) == "OpenAI-style API key"

    # FUNCTION: test_the_openrouter_shape_needs_its_own_pattern
    # SUMMARY: Regression guard: the OpenAI rule cannot reach this shape, and must not be widened to.
    @pytest.mark.unit
    def test_the_openrouter_shape_needs_its_own_pattern(self) -> None:
        # **LOGIC_STEP**: The two halves of the trade-off, pinned together. An OpenRouter key is
        # caught by its own fixed-prefix pattern; the dashed identifiers that a relaxed `sk-` rule
        # would also catch stay silent. Remeasured on 2026-08-14 over every git-tracked file: the
        # shipped rule matches 3 strings, all fixtures, and allowing dashes in the tail takes that
        # to 7 — the four extra are ordinary hyphenated identifiers. Reproduce with:
        #   git ls-files -z | xargs -0 grep -onE 'sk-(proj-)?[A-Za-z0-9-]{20,}'
        # The negative case below is one of those four: a hyphenated name whose middle happens to
        # spell `sk-`, with twenty-five characters after it, so the relaxed rule fires on it and
        # the shipped one must not.
        assert scan_line("OPENAI_COMPATIBLE_API_KEY=sk-or-v1-" + "f" * 64) == "OpenRouter API key"
        assert scan_line("bucket = artifacts/task-proof-of-delivery-2026-08") is None

    # FUNCTION: test_allow_marker_clears_one_line
    # SUMMARY: The escape hatch works, and only for the line carrying it.
    @pytest.mark.unit
    def test_allow_marker_clears_one_line(self) -> None:
        real = "api_key = sk-" + "a" * 24

        assert scan_line(real) == "OpenAI-style API key"
        assert scan_line(f"{real}  # {ALLOW_MARKER}: fixture") is None


# CLASS: tests.application.test_validate_secrets.TestCollect
# SUMMARY: File-level behaviour: reporting, and never echoing the value.
class TestCollect:
    # FUNCTION: test_issue_names_the_shape_without_the_value
    # SUMMARY: The report must not repeat the secret into a CI log.
    @pytest.mark.unit
    def test_issue_names_the_shape_without_the_value(self, tmp_path: Path) -> None:
        secret = "AKIAIOSFODNN7SELIPQR"
        (tmp_path / "config.py").write_text(f'AWS = "{secret}"\n', encoding="utf-8")

        issues = collect_secret_issues(tmp_path, ["config.py"])

        assert len(issues) == 1
        assert issues[0].rule_id == SECRET_RULE_ID
        assert issues[0].line == 1
        assert issues[0].kind == "AWS access key id"
        assert secret not in issues[0].message

    # FUNCTION: test_marker_on_the_previous_line_clears_the_match
    # SUMMARY: The formatter-proof form of the escape hatch.
    @pytest.mark.unit
    def test_marker_on_the_previous_line_clears_the_match(self, tmp_path: Path) -> None:
        (tmp_path / "config.py").write_text(
            f"# {ALLOW_MARKER}: fixture\nAWS = 'AKIAIOSFODNN7SELIPQR'\n", encoding="utf-8"
        )

        assert collect_secret_issues(tmp_path, ["config.py"]) == []

    # FUNCTION: test_file_marker_clears_every_line
    # SUMMARY: Whole-file scope for fixture collections, and it must not leak to other files.
    @pytest.mark.unit
    def test_file_marker_clears_every_line(self, tmp_path: Path) -> None:
        body = "AWS = 'AKIAIOSFODNN7SELIPQR'\nAWS2 = 'AKIAJKLMNOPQR7STUVWX'\n"
        (tmp_path / "fixtures.py").write_text(f"# {ALLOW_FILE_MARKER}: fakes\n{body}", "utf-8")
        (tmp_path / "real.py").write_text(body, encoding="utf-8")

        issues = collect_secret_issues(tmp_path, ["fixtures.py", "real.py"])

        assert {issue.source_file for issue in issues} == {"real.py"}
        assert len(issues) == 2

    # FUNCTION: test_clean_tree_reports_nothing
    # SUMMARY: The common case is silence.
    @pytest.mark.unit
    def test_clean_tree_reports_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "config.py").write_text("KEY = os.environ['API_KEY']\n", encoding="utf-8")

        assert collect_secret_issues(tmp_path, ["config.py"]) == []

    # FUNCTION: test_repository_itself_is_clean
    # SUMMARY: The gate this validator joins must be green on the tree that ships.
    @pytest.mark.unit
    def test_repository_itself_is_clean(self) -> None:
        root = Path(__file__).resolve().parents[2]

        assert collect_secret_issues(root) == []

    # FUNCTION: test_rule_has_a_playbook
    # SUMMARY: Every rule id an agent can hit must answer `failure rule <id>` with remediation.
    @pytest.mark.unit
    def test_rule_has_a_playbook(self) -> None:
        playbook = get_secrets_rule_playbook(SECRET_RULE_ID)

        assert playbook is not None
        assert "rotate" in str(playbook["stop_widening_condition"]).lower()
        assert get_secrets_rule_playbook("secrets.nope") is None
