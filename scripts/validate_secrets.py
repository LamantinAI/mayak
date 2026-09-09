#!/usr/bin/env python3
# FILE: validate_secrets.py
# SUMMARY: Scan tracked text files for credential-shaped literals so a key cannot reach a commit.
#
# Nothing in this repository looked for secrets before: not the pre-commit hook, not any of the
# eight CI jobs. A key pasted into a config file or a test fixture would have travelled to the
# remote with no warning at any point, and with branch protection unavailable on this plan there is
# no later reviewer to catch it either.
#
# The patterns below are deliberately high-precision — provider-issued key shapes and private key
# headers — because a scanner that cries wolf gets disabled. It will not catch a hand-rolled secret
# that looks like ordinary text; a dedicated tool such as gitleaks in CI is the upgrade path when
# that matters. This one runs offline, in the same gate ladder as everything else.

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from ai_context.validator_contract import build_validator_issue_payload

ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: SECRET_RULE_ID (str)
# SUMMARY: Stable rule id reported when a credential-shaped literal is found.
SECRET_RULE_ID = "secrets.credential_literal"

# ATTRIBUTE: ALLOW_MARKER (str)
# SUMMARY: Inline marker that clears one line, for placeholders a pattern cannot tell from the real thing.
ALLOW_MARKER = "allow-secret"

# ATTRIBUTE: ALLOW_FILE_MARKER (str)
# SUMMARY: Marker that clears a whole file, for fixture collections where every literal is synthetic.
# NOTE: Line-level marking loses to the formatter in a parametrized test: `ruff format` reflows the
# case across several lines and the trailing comment lands on the closing bracket, a line away from
# the literal. Whole-file scope is the honest granularity for a file that exists to hold fake keys.
# Write it with a reason, and never on a file that also holds real configuration.
ALLOW_FILE_MARKER = "allow-secret-file"

# ATTRIBUTE: _SKIPPED_SUFFIXES (frozenset[str])
# SUMMARY: Binary and vendored file types that never hold reviewable source text.
_SKIPPED_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2", ".lock", ".ndjson"}
)

# ATTRIBUTE: _SKIPPED_DIRS (tuple[str, ...])
# SUMMARY: Path prefixes excluded from the scan. A project that vendors third-party blobs adds
#          their directory here rather than teaching the patterns to tolerate binary noise.
_SKIPPED_DIRS: tuple[str, ...] = ("logs/",)

# ATTRIBUTE: _PATTERNS (tuple[tuple[str, re.Pattern[str]], ...])
# SUMMARY: Credential shapes issued by a provider, each named so the report says what was found.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key block", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # An OpenAI key is `sk-` (optionally `sk-proj-`) followed by an unbroken run of base62. Allowing
    # dashes inside the tail made `sk-onboarding-flow-token-abc123` — an ordinary internal id — read
    # as a credential, which is the crying-wolf failure this file warns about at the top.
    ("OpenAI-style API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{20,}\b")),
    # OpenRouter needs its own entry because the pattern above cannot be widened to reach it. Its
    # keys are `sk-or-v1-<hex>`, and the dashes in `or-v1-` are exactly what the rule above excludes
    # on purpose. Remeasured on 2026-08-14 across every git-tracked file: the shipped rule matches
    # 3 strings, all of them test fixtures; allowing dashes in the tail takes it to 7, and the four
    # it adds are ordinary hyphenated identifiers shaped like the internal id quoted above — the
    # crying-wolf failure that comment describes. Reproduce with:
    #   git ls-files -z | xargs -0 grep -onE 'sk-(proj-)?[A-Za-z0-9-]{20,}'
    #
    # This provider in particular, because `.env.sample` points OPENAI_COMPATIBLE_BASE_URL at
    # openrouter.ai — so the one key a project built from this template is most likely to hold was
    # the one shape the scanner could not see.
    ("OpenRouter API key", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{32,}\b")),
    ("GitHub personal access token", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    ("GitLab personal access token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    # Of the shapes this scanner checks, a connection string is the most likely credential to be
    # committed from this template specifically — it ships a DSN in .env.sample and builds one in
    # config — and it carries the password in the clear.
    #
    # The character class excludes `{ } $ % < >` from both userinfo halves, which is what keeps
    # this from crying wolf: every DSN the repository legitimately contains is an interpolation
    # template — `${POSTGRES_PASSWORD}` in alembic.ini and compose, `{encoded_password}` in the
    # f-string that builds the real URL. Those carry no credential and firing on them would train
    # the reader to ignore this rule. A `/` is excluded too, so an ordinary URL path of the shape
    # `https://host/a:b@c` cannot match.
    (
        "connection string with password",
        re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/'\"{}$%<>]+:[^\s@/'\"{}$%<>]{3,}@"),
    ),
    ("Stripe-style secret key", re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+")),
)

# ATTRIBUTE: _PLACEHOLDER_HINTS (tuple[str, ...])
# SUMMARY: Substrings that mark a matched value as an obvious stand-in rather than a live credential.
# NOTE: These are tested against the MATCHED VALUE, never against the whole line. Matching the line
# silenced real keys whenever an ordinary word happened to share it: `AKIA... # rotate immediately
# if invalid` was skipped because the comment said "invalid", and the same went for "dummy",
# "your-" and "test-key" appearing anywhere in a sentence. A hint has to be inside the credential
# itself to mean anything.
_PLACEHOLDER_HINTS: tuple[str, ...] = (
    "example",
    "changeme",
    "placeholder",
    "xxxx",
    "dummy",
    "fake",
    "redacted",
    "test-key",
    "sk-test",
)

# ATTRIBUTE: _SECRETS_RULE_PLAYBOOKS (dict[str, dict[str, object]])
# SUMMARY: Stable rule_id → playbook mapping consumed by `query_ai_context.py failure rule`.
_SECRETS_RULE_PLAYBOOKS: dict[str, dict[str, object]] = {
    SECRET_RULE_ID: {
        "meaning": (
            "A literal matching a provider-issued credential shape was found in a tracked file. "
            "Committing it publishes it, and rotating is the only real remedy once that happens."
        ),
        "suggested_fix": (
            "Remove the literal and read the value from the environment through project/core/config*. "
            "If the value is a placeholder the pattern cannot distinguish from a real key, append "
            f"`{ALLOW_MARKER}: <why it is safe>` on the same line."
        ),
        "read_first": [
            ".env.sample",
            "project/core/config.py",
        ],
        "smallest_command_to_rerun": "uv run python scripts/validate_secrets.py",
        "likely_fix_shape": (
            "Replace the literal with an environment lookup, or mark the line as a placeholder."
        ),
        "next_checks": [
            "uv run python scripts/validate_secrets.py",
            "make quality-gates",
        ],
        "stop_widening_condition": (
            "Stop once no tracked file carries a credential-shaped literal. If the value was ever "
            "committed, rotate it — deleting the line does not remove it from history."
        ),
    },
}


# CLASS: SecretIssue
# SUMMARY: One credential-shaped literal found in a tracked file.
@dataclass(frozen=True)
class SecretIssue:
    # ATTRIBUTE: rule_id (str)
    # SUMMARY: Stable identifier of the violated rule.
    rule_id: str

    # ATTRIBUTE: source_file (str)
    # SUMMARY: Repo-relative path of the file holding the literal.
    source_file: str

    # ATTRIBUTE: line (int)
    # SUMMARY: 1-based line number of the match.
    line: int

    # ATTRIBUTE: kind (str)
    # SUMMARY: Which credential shape matched, e.g. "AWS access key id".
    kind: str

    # ATTRIBUTE: message (str)
    # SUMMARY: Human-readable description naming the shape but never the value.
    message: str

    # ATTRIBUTE: severity (str)
    # SUMMARY: 'error' (blocks the gate), 'warning', or 'info'.
    severity: str = "error"


# FUNCTION: get_secrets_rule_playbook
# SUMMARY: Return a copy of the playbook for a secrets rule_id, or None if unknown.
def get_secrets_rule_playbook(rule_id: str) -> dict[str, object] | None:
    playbook = _SECRETS_RULE_PLAYBOOKS.get(rule_id)
    if playbook is None:
        return None
    return dict(playbook)


# FUNCTION: _tracked_files
# SUMMARY: List the repository's tracked files, so untracked scratch work is never scanned.
# OUTPUT: (list[str]): Repo-relative paths, empty when git is unavailable.
def _tracked_files(root_dir: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root_dir), "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line for line in completed.stdout.splitlines() if line]


# FUNCTION: is_allowed
# SUMMARY: Report whether the marker clears this match, on the line itself or the one just above.
# NOTE: A same-line marker alone is not enough: `ruff format` reflows a long line and carries the
# trailing comment to the closing bracket, which is a different line from the literal. The
# preceding-line form is the one that survives the formatter.
def is_allowed(line: str, previous_line: str = "") -> bool:
    return ALLOW_MARKER in line or ALLOW_MARKER in previous_line


# FUNCTION: scan_line
# SUMMARY: Return the credential shape matched on a line, or None when the line is clean.
# NOTE: The matched value is never returned or printed. A validator that echoes the secret it found
# writes it into CI logs, which is the one place it must not end up.
def scan_line(line: str, previous_line: str = "") -> str | None:
    if is_allowed(line, previous_line):
        return None
    for kind, pattern in _PATTERNS:
        match = pattern.search(line)
        if match is None:
            continue
        # **LOGIC_STEP**: Judge the matched value, not its neighbours on the line.
        value = match.group(0).lower()
        if any(hint in value for hint in _PLACEHOLDER_HINTS):
            continue
        return kind
    return None


# FUNCTION: collect_secret_issues
# SUMMARY: Scan every tracked text file and report credential-shaped literals.
# INPUT: files (Sequence[str] | None): Optional explicit file list, used by tests and hooks.
def collect_secret_issues(root_dir: Path, files: Sequence[str] | None = None) -> list[SecretIssue]:
    own_path = Path(__file__).resolve()
    candidates = list(files) if files is not None else _tracked_files(root_dir)
    issues: list[SecretIssue] = []

    for relative in sorted(candidates):
        if relative.startswith(_SKIPPED_DIRS) or Path(relative).suffix in _SKIPPED_SUFFIXES:
            continue
        path = root_dir / relative
        # **LOGIC_STEP**: Skip this file's own pattern table, which is credential-shaped by design.
        if not path.is_file() or path.resolve() == own_path:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if ALLOW_FILE_MARKER in text:
            continue
        file_lines = text.splitlines()
        for number, line in enumerate(file_lines, start=1):
            previous_line = file_lines[number - 2] if number > 1 else ""
            kind = scan_line(line, previous_line)
            if kind is None:
                continue
            issues.append(
                SecretIssue(
                    rule_id=SECRET_RULE_ID,
                    source_file=relative,
                    line=number,
                    kind=kind,
                    message=(
                        f"Line looks like a {kind}. Move the value to the environment, or mark the "
                        f"line `{ALLOW_MARKER}: <reason>` if it is a placeholder."
                    ),
                )
            )
    return issues


# FUNCTION: _issue_to_payload
# SUMMARY: Render one issue in the shared machine-readable validator shape.
def _issue_to_payload(issue: SecretIssue) -> dict[str, object]:
    return build_validator_issue_payload(
        rule_id=issue.rule_id,
        category="secrets",
        file=issue.source_file,
        line=issue.line,
        message=issue.message,
        severity=issue.severity,
        playbook=get_secrets_rule_playbook(issue.rule_id),
        extra=asdict(issue),
    )


# FUNCTION: main
# SUMMARY: Run the secret scan and return a process exit code.
# OUTPUT: (int): Zero when no tracked file carries a credential-shaped literal.
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan tracked files for credential-shaped literals."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured machine-readable validation results.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Optional explicit file list; defaults to every tracked file.",
    )
    args = parser.parse_args([] if argv is None else argv)

    issues = collect_secret_issues(ROOT_DIR, args.files or None)
    if not issues:
        if args.json:
            print(json.dumps({"status": "ok", "issues": []}, indent=2), end="")
        else:
            print("Secret scan passed.")
        return 0

    if args.json:
        payload = {"status": "error", "issues": [_issue_to_payload(issue) for issue in issues]}
        print(json.dumps(payload, indent=2), end="")
        return 1

    for issue in issues:
        print(f"{issue.source_file}:{issue.line}: [{issue.rule_id}] {issue.message}")
    print(f"\n{len(issues)} issue(s) found.")
    return 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
