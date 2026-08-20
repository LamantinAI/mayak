#!/usr/bin/env python3
# FILE: create_env_file.py
# SUMMARY: Create .env from .env.sample on a fresh checkout, replacing the sample's placeholder
#          database password with a generated one so the container starts.

from __future__ import annotations

import secrets
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

# ATTRIBUTE: _GENERATED_KEYS (tuple[str, ...])
# SUMMARY: Sample values that must not be copied verbatim, because the runtime guard rejects them.
# NOTE: Only the database password belongs here. `.env.sample` ships APP_DEBUG=false beside
# POSTGRES_PASSWORD=your_postgres_password, and validate_runtime() refuses to start on that pair —
# correctly, since a placeholder must never reach a deployment. Copying the file verbatim therefore
# handed a fresh checkout a container that exits 1 during `docker compose up`, with the explanation
# buried in a log nobody reads before the README's promised Swagger page fails to load. Generating
# the value keeps the guard exactly as strict while removing the one case where it fires on someone
# who has done nothing wrong. The provider API key is deliberately not here: no generated string
# would be a working credential, and the empty sample value is honest about that.
_GENERATED_KEYS: tuple[str, ...] = ("POSTGRES_PASSWORD",)


# FUNCTION: render_env_from_sample
# SUMMARY: Return the .env body for a sample file, with every generated key given a fresh value.
# INPUT: sample_text (str): Full text of .env.sample.
# OUTPUT: (str): Text to write to .env.
def render_env_from_sample(sample_text: str) -> str:
    rendered: list[str] = []
    for line in sample_text.splitlines(keepends=True):
        # **LOGIC_STEP**: Match on the assignment, not on the key anywhere in the line, so a
        # commented example (`# POSTGRES_PASSWORD=...`) and a prose mention both survive untouched.
        # The sample explains its own variables in comments, and rewriting those would strip the
        # documentation the file exists to carry.
        key = line.split("=", 1)[0]
        if key in _GENERATED_KEYS:
            ending = line[len(line.rstrip("\r\n")) :]
            rendered.append(f"{key}={secrets.token_hex(16)}{ending}")
            continue
        rendered.append(line)
    return "".join(rendered)


# FUNCTION: create_env_file
# SUMMARY: Write .env from .env.sample unless one already exists.
# INPUT: root (Path): Repository root holding .env.sample.
# OUTPUT: (str): "created" when a file was written, "exists" when one was already there.
def create_env_file(root: Path) -> str:
    env_path = root / ".env"
    # **LOGIC_STEP**: An existing .env is never touched, not even to fill a missing key. It holds
    # credentials this script cannot reconstruct, and `make init-project` is documented as
    # idempotent — a setup step that silently rewrites secrets is one nobody can afford to rerun.
    if env_path.exists():
        return "exists"
    sample_text = (root / ".env.sample").read_text(encoding="utf-8")
    env_path.write_text(render_env_from_sample(sample_text), encoding="utf-8")
    return "created"


# FUNCTION: main
# SUMMARY: Create the file and report what happened, for `dev_setup.sh` to print.
# OUTPUT: (int): Process exit code.
def main() -> int:
    if create_env_file(ROOT_DIR) == "exists":
        print(".env already exists, leaving it alone.")
        return 0
    print("Created .env from .env.sample, with a generated local database password.")
    print("Edit it with your API keys.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
