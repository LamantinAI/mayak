# FILE: scripts/bootstrap_project.py
# SUMMARY: Turn a fresh Mayak copy into a named, runnable product without changing Git settings.

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from scripts.run_all_tests import _parse_env_file

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_REPOSITORY = "https://github.com/LamantinAI/mayak"
# Where `make db-up-worktree` records the loopback port Docker assigned (Makefile: WORKTREE_DB_FILE).
WORKTREE_PORT_FILE = ".mayak-postgres-port"


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(("git", *args), cwd=root, check=check, capture_output=True, text=True)
    return result.stdout.strip()


def _is_canonical(url: str) -> bool:
    if "://" in url:
        parsed = urlsplit(url)
        return (
            parsed.hostname == "github.com"
            and parsed.path.strip("/").lower().removesuffix(".git") == "lamantinai/mayak"
        )
    return url.lower().removesuffix(".git") == "git@github.com:lamantinai/mayak"


def _replace_once(text: str, pattern: str, replacement: str) -> str:
    updated, count = re.subn(pattern, lambda _: replacement, text, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"Expected one match for {pattern} in the template")
    return updated


def _env_assignment(text: str, key: str, value: str) -> str:
    pattern = rf"^\s*(?:export +)?{key}\s*=.*$"
    if not re.search(pattern, text, re.MULTILINE):
        return text + f"\n{key}={value}\n"
    return _replace_once(text, pattern, f"{key}={value}")


def _project_files(
    root: Path, context: dict, name: str, domain: str, database: str
) -> dict[str, str]:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug or slug == "mayak":
        raise ValueError("NAME must contain a distinct ASCII package name")
    postgres = database == "postgres"
    enabled = "true" if postgres else "false"
    encoded_name = json.dumps(name, ensure_ascii=False)
    if any(char in name for char in "$\\\"'"):
        raise ValueError("NAME must not contain quotes, dollar signs or backslashes")

    manifest = (root / "pyproject.toml").read_text(encoding="utf-8")
    template_version = tomllib.loads(manifest)["project"]["version"]
    for key, value in {
        "name": f'"{slug}"',
        "version": '"0.1.0"',
        "description": json.dumps(domain, ensure_ascii=False),
    }.items():
        manifest = _replace_once(manifest, rf"^{key} = .*", f"{key} = {value}")

    settings = (root / "project/core/config_settings_core.py").read_text(encoding="utf-8")
    settings = _replace_once(settings, r'default="Mayak",', f"default={encoded_name},")
    settings = _replace_once(
        settings,
        r"^    enabled: bool = Field\(\n        default=True,",
        f"    enabled: bool = Field(\n        default={postgres},",
    )
    context.update(
        project_name=name,
        domain=domain,
        is_template=False,
        database=database,
        template={
            "repository": TEMPLATE_REPOSITORY,
            "version": template_version,
            "source_tree": _git(root, "rev-parse", "HEAD^{tree}"),
        },
    )
    context["integrations"]["postgres"]["description"] = (
        "PostgreSQL backs the service and is required for readiness."
        if postgres
        else "This service starts without a relational database."
    )
    context["api_overview"]["notes"] = "Kernel health is available at /health/ and /health/ready."

    readme = (root / "README.md").read_text(encoding="utf-8")
    marker = "## Checking your work"
    tail = readme[readme.index(marker) :]
    run_commands = (
        "make init-project\nmake db-up-worktree\nmake run-local"
        if postgres
        else "make init-project\nmake run-local"
    )
    files = {
        "docs/project_context.json": json.dumps(context, ensure_ascii=False, indent=2) + "\n",
        "pyproject.toml": manifest,
        "project/core/config_settings_core.py": settings,
        "PROJECT.md": f"# {name}\n\n{domain}\n",
        "README.md": f"# {name}\n\n{domain}\n\n## Quick start\n\nRequires Python `3.13` (Python 3.13+).\n\n```sh\n{run_commands}\n```\n\n{tail}",
    }
    for path in (".env.sample", "tests/functional/.env.sample", ".env"):
        target = root / path
        if not target.exists():
            continue
        if path == ".env":
            existing = _parse_env_file(target)
            if existing.get("APP_NAME", "Mayak") not in {"Mayak", name}:
                raise ValueError("Existing .env has a different APP_NAME")
            if existing.get("POSTGRES_ENABLED", "true").lower() not in {"true", enabled}:
                raise ValueError("Existing .env has a conflicting POSTGRES_ENABLED")
        text = _env_assignment(target.read_text(encoding="utf-8"), "APP_NAME", encoded_name)
        files[path] = _env_assignment(text, "POSTGRES_ENABLED", enabled)
    return files


def bootstrap(root: Path, *, name: str, domain: str, database: str, uv: str) -> None:
    for flags in ((), ("--push",)):
        if _is_canonical(_git(root, "remote", "get-url", *flags, "origin", check=False)):
            raise ValueError(
                "origin is the Mayak template. Create your own repository from it first."
            )
    if not name.strip() or not domain.strip() or "\n" in name or "\r" in name:
        raise ValueError("Provide NAME and DOMAIN, with NAME on one line")
    if database not in {"postgres", "none"}:
        raise ValueError("Set DB=postgres or DB=none")

    context_path = root / "docs/project_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    extractor = root / "scripts/extract_reference_vertical.py"
    if context.get("is_template") is True:
        extraction = importlib.import_module("scripts.extract_reference_vertical")
        plan = extraction.build_plan(root, converting=True)
        if isinstance(plan, str):
            raise ValueError(plan)
        files = _project_files(root, context, name, domain, database)
        dirty = _git(root, "diff", "--name-only", "HEAD", "--", *(files.keys() - {".env"}))
        if dirty:
            raise ValueError("Bootstrap would replace edited files: " + dirty.replace("\n", ", "))
        # The metadata written last lets a lock or readiness failure resume as the same product.
        marker = files.pop("docs/project_context.json")
        for path, content in files.items():
            (root / path).write_text(content, encoding="utf-8")
        context_path.write_text(marker, encoding="utf-8")
    elif (context.get("project_name"), context.get("domain"), context.get("database")) != (
        name,
        domain,
        database,
    ) or "template" not in context:
        raise ValueError("This checkout already has its own project identity")

    if extractor.exists():
        subprocess.run((uv, "lock"), cwd=root, check=True)
    subprocess.run(("make", "init-project", "EXTRACT_FLAGS=--no-stage"), cwd=root, check=True)
    if extractor.exists():
        raise ValueError("Reference extraction did not complete. See the diagnostic above.")
    # A developer or CI shell may point at another database. Bootstrap owns a local
    # database and must migrate that one, including when called from an outer make.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("POSTGRES_") and key not in {"MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES"}
    }
    env["POSTGRES_ENABLED"] = "true" if database == "postgres" else "false"
    if database == "postgres":
        env = env | _start_worktree_database(root, env)
    subprocess.run(("make", "smoke-local"), cwd=root, check=True, env=env)
    print("Ready. Read the matching feature path in AGENTS.md, then run make verify.")


# Start this worktree's PostgreSQL and return the target every later step must use: the loopback
# port the start just wrote, passed explicitly. Leaving the choice to the settings loader was not
# enough: it applies the worktree port only when .env names a host it knows as loopback, so a
# .env with POSTGRES_HOST=localhost. (or any host of its own) took the reachability probe, Alembic
# and the smoke server to that database instead — v1.3.1 migrated a database it did not start,
# dropped a table holding a row there and printed Ready. Explicit host and port win over .env in
# PostgresSettings, and the name and credentials still come from .env, as the container's did.
# .env itself is left as it is: POSTGRES_HOST and POSTGRES_PORT there stay for everyday commands.
def _start_worktree_database(root: Path, env: dict[str, str]) -> dict[str, str]:
    marker = root / WORKTREE_PORT_FILE
    before = _marker_identity(marker)
    subprocess.run(("make", "db-up-worktree"), cwd=root, check=True, env=env)
    # A marker this start did not write is another start's port: refuse before migrating.
    if _marker_identity(marker) in {before, None}:
        raise ValueError(
            f"make db-up-worktree did not record a port in {WORKTREE_PORT_FILE}; nothing was migrated"
        )
    text = marker.read_text(encoding="utf-8", errors="replace").strip()
    # The same rule PostgresSettings applies to the marker; this script runs before the
    # project's dependencies are installed, so it cannot import that one.
    if not (text.isascii() and text.isdecimal() and len(text) <= 5 and 1 <= int(text) <= 65535):
        raise ValueError(
            f"make db-up-worktree recorded an invalid port in {WORKTREE_PORT_FILE}; nothing was migrated"
        )
    print(
        f"bootstrap: using this worktree's database at 127.0.0.1:{text} (make db-up-worktree)",
        flush=True,
    )
    return {"POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": text}


def _marker_identity(marker: Path) -> tuple[int, int] | None:
    try:
        status = marker.stat()
    except FileNotFoundError:
        return None
    return status.st_ino, status.st_mtime_ns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    try:
        bootstrap(
            ROOT,
            name=os.environ.get("MAYAK_PROJECT_NAME", "").strip(),
            domain=os.environ.get("MAYAK_PROJECT_DOMAIN", "").strip(),
            database=os.environ.get("MAYAK_PROJECT_DB", ""),
            uv=args.uv,
        )
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"bootstrap: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
