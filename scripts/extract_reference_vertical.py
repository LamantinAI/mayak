#!/usr/bin/env python3
# FILE: extract_reference_vertical.py
# SUMMARY: Take the reference vertical out of a project made from the template — once, all or nothing.
# `make init-project` runs this. In the template (`is_template: true`) it does nothing. In a project
# it acts once the identity is replaced, the vertical's files are still as the template shipped them
# and no vertical of the project's own exists yet; otherwise it says why and changes nothing. Acting
# means: the vertical's files move to .agents/skills/add-vertical/scaffold/ (same relative paths,
# no longer imported, collected or checked), their fragments in shared files are cut and kept in
# the scaffold's README, a migration dropping the table is appended — history is append-only, so
# this is right whether or not a database already ran the vertical's revisions — every mention of a
# moved path is pointed at its copy, the template's own tools (tests/template, the mutation
# catalogue and its runner, this script) go, and the generated docs are rebuilt. Every edit is
# computed and checked before the first file is written.
#
#   uv run python scripts/extract_reference_vertical.py            # what init-project runs
#   uv run python scripts/extract_reference_vertical.py --dry-run  # say what would change
#   uv run python scripts/extract_reference_vertical.py --print-manifest   # after editing the sample

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

ROOT_DIR = Path(__file__).resolve().parent.parent
SCAFFOLD = ".agents/skills/add-vertical/scaffold"

# The vertical's own files, and the digest of each as the template ships it: a project that has
# edited one is building on it, and taking it away would take its work too. After editing the
# sample, regenerate the digests with --print-manifest; tests/template fails while they are stale.
SAMPLE_FILES = (
    "project/domain/reference_task.py",
    "project/application/reference_task_service.py",
    "project/application/reference_task_dtos.py",
    "project/infrastructure/persistence/reference_task_repository.py",
    "project/infrastructure/api/endpoints/reference_tasks.py",
    "tests/application/test_reference_task_vertical.py",
    "tests/db/test_reference_task_repository.py",
    "tests/db/test_reference_tasks_api.py",
    "tests/functional/src/test_reference_tasks_api.py",
)
SAMPLE_SHA256: dict[str, str] = {
    "project/domain/reference_task.py": "4c057d0e93ee00b1c084416c88c5717b97376d664c15333f0bafca109cd3642d",
    "project/application/reference_task_service.py": "900b34bb977a40fb219e848dbe6a14cc40bd127c02347e0ec0cd3ef54e7d09c7",
    "project/application/reference_task_dtos.py": "31f2578ce6404a3f9a8c6b3438adb9ba1da301a18ec616faec961fad9938c5bc",
    "project/infrastructure/persistence/reference_task_repository.py": "59eb61963d35dce2de4a23037216ab7d3ccde306749e4c5bbfaa8da6a5f71256",
    "project/infrastructure/api/endpoints/reference_tasks.py": "cf4be1ec16fa1035a351a915600ae627c0e20fa2b8e1ede45a5c851d67845ba9",
    "tests/application/test_reference_task_vertical.py": "666aa0a16bffd70617290b7bc6caf30f69c8d26b613e910089b0537141b0d781",
    "tests/db/test_reference_task_repository.py": "a94504ec248f2559951345e964507868d3b10817c80e954bad9b8e5adc7b1d3f",
    "tests/db/test_reference_tasks_api.py": "e5e287cd5bcfafd13df26e071617f5bf7ac98b2450060e4351f8e12a22275daa",
    "tests/functional/src/test_reference_tasks_api.py": "6d4ed6eb1f8583483b2737762f0046e5a3fbb807149c59c7689d20f9b63f896b",
}

# Files the template needs and a project does not: removed, not kept.
TEMPLATE_ONLY = (
    "tests/template",
    "docs/mutations",
    "scripts/run_mutations.py",
    "scripts/check_product_from_template.py",
    "scripts/extract_reference_vertical.py",
)

SAMPLE_REVISIONS = (
    "alembic/versions/001_initial_reference_tasks_schema.py",
    "alembic/versions/7300d4656a8d_add_updated_at_to_reference_tasks.py",
    "alembic/versions/b5e2c1a9d4f0_one_open_reference_task_per_title.py",
)
SAMPLE_HEAD = "b5e2c1a9d4f0"
DROP_REVISION = "e3b8d1f6a2c7"
DROP_MIGRATION = f"alembic/versions/{DROP_REVISION}_drop_reference_tasks.py"

# The kernel's own files in the directories a vertical writes to. Anything else there is a vertical
# of the project's own, and the edits below assume the shared files carry only the reference one.
KERNEL_FILES = {
    "project/application": {"__init__.py", "core_model.py", "dtos.py"},
    "project/domain": {"__init__.py", "exceptions.py", "ports.py"},
    "project/infrastructure/api/endpoints": {"__init__.py", "health.py"},
    "project/infrastructure/persistence": {"__init__.py", "orm_models.py"},
}

_POINTER = f"{SCAFFOLD}/README.md"
_QUICK_START = (
    "- Copy the `reference_task` vertical. It is the one worked example and it exists to be "
    "copied — eleven files plus three wiring edits; `.agents/skills/add-vertical` carries the "
    "order and the removal list for when your own vertical replaces it. A project that has "
    "replaced it edits this line and nothing else: the wrapper's Quick Start is generated from "
    "these bullets."
)


# Cut the lines from the one equal to `first` through the first later one equal to `last` (or to
# the end of the file when `last` is None) and put `replacement` there. Lines are matched whole, so
# an edit that no longer fits the file is refused rather than applied somewhere near it.
@dataclass(frozen=True)
class Cut:
    path: str
    first: str
    last: str | None
    replacement: str
    keep_in_readme: bool = True


CUTS: tuple[Cut, ...] = (
    Cut(
        "project/core/service_registration.py",
        "from project.application.reference_task_service import ReferenceTaskService",
        "from project.application.reference_task_service import ReferenceTaskService",
        "",
        keep_in_readme=False,
    ),
    Cut(
        "project/core/service_registration.py",
        "from project.infrastructure.persistence.reference_task_repository import (",
        ")",
        "",
        keep_in_readme=False,
    ),
    Cut(
        "project/core/service_registration.py",
        "    # None when the project runs without the relational store: there is no pool to give it.",
        None,
        "    # A vertical builds its service here and registers it under its own key; the reference\n"
        f"    # vertical's builder is in {_POINTER}. Keep the key in the dict when the value is\n"
        "    # None: validate_endpoint_wiring.py learns the service exists from the literal, and\n"
        "    # router_registration.py reads the value to decide whether the routes are reachable.\n"
        "    _ = settings, llm_service, db_pool\n"
        "    services: dict[str, Any] = {}\n"
        "    return services\n",
    ),
    Cut(
        "project/infrastructure/api/router_registration.py",
        "from project.infrastructure.api.endpoints.reference_tasks import reference_tasks_router",
        "from project.infrastructure.api.endpoints.reference_tasks import reference_tasks_router",
        "",
        keep_in_readme=False,
    ),
    Cut(
        "project/infrastructure/api/router_registration.py",
        "    # The reference vertical needs the relational store, so its routes exist only",
        None,
        "    # A router whose service needs a subsystem that can be off is registered only when the\n"
        '    # service exists — `if (services or {}).get("<name>_service") is not None:` — so with the\n'
        "    # subsystem off its paths are absent rather than answering 500. The reference vertical's\n"
        f"    # registration is in {_POINTER}.\n"
        "    _ = services\n",
    ),
    Cut(
        "project/infrastructure/api/dependencies.py",
        "from typing import Annotated, TypeVar",
        "from typing import Annotated, TypeVar",
        "from typing import TypeVar\n",
        keep_in_readme=False,
    ),
    Cut(
        "project/infrastructure/api/dependencies.py",
        "from fastapi import Depends, Request",
        "from fastapi import Depends, Request",
        "from fastapi import Request\n",
        keep_in_readme=False,
    ),
    Cut(
        "project/infrastructure/api/dependencies.py",
        "from project.application.reference_task_service import ReferenceTaskService",
        "from project.application.reference_task_service import ReferenceTaskService",
        "",
        keep_in_readme=False,
    ),
    Cut(
        "project/infrastructure/api/dependencies.py",
        "# Returns the service built by service_registration.build_reference_services.",
        None,
        "# A vertical adds its getter and its typed alias here, in this two-part shape — the reference\n"
        f"# vertical's pair is in {_POINTER}:\n"
        "#\n"
        "#     def get_<name>_service(request: Request) -> <Name>Service:\n"
        '#         return _get_service(request, "<name>_service", <Name>Service)\n'
        "#\n"
        "#     <Name>ServiceDep = Annotated[<Name>Service, Depends(get_<name>_service)]\n"
        "#\n"
        "# validate_endpoint_wiring.py recognises exactly that shape to link alias -> getter -> service\n"
        "# key, and refuses an endpoint whose alias chain does not resolve.\n",
    ),
    Cut(
        "project/domain/ports.py",
        "from datetime import datetime",
        "from datetime import datetime",
        "",
        keep_in_readme=False,
    ),
    Cut(
        "project/domain/ports.py",
        "from project.domain.reference_task import ReferenceTask",
        "from project.domain.reference_task import ReferenceTask",
        "",
        keep_in_readme=False,
    ),
    Cut(
        "project/domain/ports.py",
        "# Canonical data-access boundary. Verticals define one of these per aggregate.",
        None,
        "# A vertical's data-access boundary goes here, one Protocol per aggregate, speaking only in\n"
        f"# domain types; the reference vertical's, with the reasons, is in {_POINTER}.\n",
    ),
    Cut(
        "project/infrastructure/persistence/orm_models.py",
        "from datetime import datetime",
        "from project.domain.reference_task import CLOSED_STATUS, MAX_TITLE_LENGTH",
        "from sqlalchemy.orm import DeclarativeBase\n",
        keep_in_readme=False,
    ),
    Cut(
        "project/infrastructure/persistence/orm_models.py",
        "# Alembic reads this metadata; nothing reads the ORM at runtime, because "
        "ReferenceTaskRepository speaks raw psycopg. Verticals add their own tables alongside it "
        "and delete this one with the rest of the example.",
        None,
        "# Alembic reads this metadata; nothing reads the ORM at runtime — repositories speak raw\n"
        "# psycopg. A vertical declares its tables here before it autogenerates its migration; the\n"
        f"# reference vertical's model and its partial unique index are in {_POINTER}.\n",
    ),
    Cut(
        "project/infrastructure/persistence/__init__.py",
        "# writes go through psycopg against the shared pool; reference_task_repository.py is the worked",
        "# example of that shape, including the row -> domain conversion the driver makes necessary.",
        "# writes go through psycopg against the shared pool; the reference vertical's repository under\n"
        f"# {SCAFFOLD}/ is the worked example, row -> domain conversion included.\n",
        keep_in_readme=False,
    ),
    Cut(
        "docs/agent_rules.md",
        _QUICK_START,
        _QUICK_START,
        "- Build a vertical with `.agents/skills/add-vertical`: it carries the order of work, and "
        f"`{SCAFFOLD}/` holds the reference vertical this project was made from — eleven files plus "
        "three wiring edits — to copy from. This line is this project's to edit: the wrapper's Quick "
        "Start is generated from these bullets.\n",
        keep_in_readme=False,
    ),
    Cut(
        ".agents/skills/add-vertical/SKILL.md",
        "# Add Vertical",
        "# Add Vertical",
        "# Add Vertical\n"
        "\n"
        "> `make init-project` has taken the reference vertical out of this project's application:\n"
        "> its files are in `scaffold/` next to this file, at the same relative paths, and every path\n"
        '> below that names one of them points there. "Deleting the reference vertical" is done.\n',
        keep_in_readme=False,
    ),
    # The way by hand, for a project where this script refused. Here it has acted, and the files
    # that section tells an agent to `git rm` are gone already.
    Cut(
        ".agents/skills/add-vertical/SKILL.md",
        "## Deleting the reference vertical",
        None,
        "## Deleting the reference vertical\n"
        "\n"
        "Done in this project by `make init-project`. The vertical's files are in `scaffold/`, and the\n"
        "lines it added to shared files are in `scaffold/README.md`. Its table is dropped by\n"
        f"`{DROP_MIGRATION}`, appended after the three revisions\n"
        "that created it; those stay, since every database made from the template ran them.\n",
        keep_in_readme=False,
    ),
)

# What each cut takes from a shared file as the template ships it, keyed "<path>: <first line>".
# A cut is matched by its first line, and one that runs to the end of the file takes everything
# below it — so a line a project added there went with the vertical's, silently: the independent
# check of 2026-09-25 lost three of three. Anything but the shipped text now refuses instead.
# Regenerate with --print-manifest after editing a shared file; tests/template fails while stale.
CUT_SHA256: dict[str, str] = {
    "project/core/service_registration.py: from project.application.reference_task_service import ReferenceTaskService": "c2d1578871d269e933963d3810fd1d165aaddb0b74c7e990bbceb195e1afb376",
    "project/core/service_registration.py: from project.infrastructure.persistence.reference_task_repository import (": "77055d131f9e4fc85e52805966d4cc8fc7494116cea3ffca71f4023eebbbd7bc",
    "project/core/service_registration.py:     # None when the project runs without the relational store: there is no pool to give it.": "2c86a9cf93d841fad6b5aaafc9c8db4b9b7e07e904a59edf79eb6714981d46f3",
    "project/infrastructure/api/router_registration.py: from project.infrastructure.api.endpoints.reference_tasks import reference_tasks_router": "f40804ebc62d1536fe3b0e0fb17d956ac9cc41e372162cdee3c0cf92540fc4e9",
    "project/infrastructure/api/router_registration.py:     # The reference vertical needs the relational store, so its routes exist only": "8ee7e3f10f9372ad080c4f8ff192c371d6455b1ae7029b1465ee3a32255c5ebd",
    "project/infrastructure/api/dependencies.py: from typing import Annotated, TypeVar": "202ef05b2e8a534ff3b37bbd9497e170b18f31b6ab5c9a94f85446af54940ee6",
    "project/infrastructure/api/dependencies.py: from fastapi import Depends, Request": "f6cdda0f1cc3add875d477429f2d5a92e4afb84574487822522d93bdb99cd877",
    "project/infrastructure/api/dependencies.py: from project.application.reference_task_service import ReferenceTaskService": "c2d1578871d269e933963d3810fd1d165aaddb0b74c7e990bbceb195e1afb376",
    "project/infrastructure/api/dependencies.py: # Returns the service built by service_registration.build_reference_services.": "59c8b1bf2616390fe6f9c20faaa98a3c7b82eb71ed00572f18061a57cf3a8324",
    "project/domain/ports.py: from datetime import datetime": "1c80ee441a56d69acae99266605441b0bb38a078dd24cafe32771d718f2b86a4",
    "project/domain/ports.py: from project.domain.reference_task import ReferenceTask": "187181b41049f2a8f9f9c630c724547e2ddd8ef9821a44e122247f96aa8f7afc",
    "project/domain/ports.py: # Canonical data-access boundary. Verticals define one of these per aggregate.": "0b92a33017edb7b1f0b4c96337e5256015273de609378b3f22ca2b818e3b1858",
    "project/infrastructure/persistence/orm_models.py: from datetime import datetime": "0da6047c5ab62b685d806fac9973d83e6db326e082942f4e98c98ca95c95e599",
    "project/infrastructure/persistence/orm_models.py: # Alembic reads this metadata; nothing reads the ORM at runtime, because ReferenceTaskRepository speaks raw psycopg. Verticals add their own tables alongside it and delete this one with the rest of the example.": "ac37054fd19d77cc08654e55e4ba5a2938d8f7a25f6d357712e03be906060704",
    "project/infrastructure/persistence/__init__.py: # writes go through psycopg against the shared pool; reference_task_repository.py is the worked": "65e8404cb3529b13c13657d8c32f2dcf7b38c6de1ac7246f73cf93f25341ba60",
    "docs/agent_rules.md: - Copy the `reference_task` vertical. It is the one worked example and it exists to be copied — eleven files plus three wiring edits; `.agents/skills/add-vertical` carries the order and the removal list for when your own vertical replaces it. A project that has replaced it edits this line and nothing else: the wrapper's Quick Start is generated from these bullets.": "9b7ab18e46dabcc13dfaafab0156bfb41bf50a91b6b422495ec674d218108d48",
    ".agents/skills/add-vertical/SKILL.md: # Add Vertical": "ea123ba5901f1bcc464ff4cb4060688b2793f1870dfead15c1b909435f86c289",
    ".agents/skills/add-vertical/SKILL.md: ## Deleting the reference vertical": "62015290e767a42e5baa92a5e8141d7bf687760d484323cb6d545bb3b7dfdbab",
}


def _cut_key(cut: Cut) -> str:
    return f"{cut.path}: {cut.first}"


@dataclass
class Plan:
    writes: dict[str, str] = field(default_factory=dict)
    deletes: list[str] = field(default_factory=list)


# Git's own variables left out. Inside a hook git exports GIT_DIR and GIT_INDEX_FILE, and a git
# command run with them in another directory acts on the repository that ran the hook: measured on
# 2026-09-24, the pre-commit hook's test made a copy, committed it into the outer repository,
# re-ran the hook there, recursed eighteen levels and left core.bare=true behind.
def hermetic_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _read(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Trailing whitespace left out: an editor that adds or drops the blank line at the end of a file
# changes nothing a cut takes, and refusing a project over it would be a false alarm.
def _text_digest(text: str) -> str:
    return hashlib.sha256(text.rstrip().encode("utf-8")).hexdigest()


# Returns the edited text, or a reason the cut does not fit.
def _apply_cut(text: str, cut: Cut) -> tuple[str, str] | str:
    lines = text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.rstrip("\n") == cut.first]
    if len(starts) != 1:
        return f"{cut.path}: expected one line {cut.first!r}, found {len(starts)}"
    start = starts[0]
    if cut.last is None:
        end = len(lines) - 1
    else:
        later = [i for i in range(start, len(lines)) if lines[i].rstrip("\n") == cut.last]
        if not later:
            return f"{cut.path}: no line {cut.last!r} after {cut.first!r}"
        end = later[0]
    removed = "".join(lines[start : end + 1])
    return "".join(lines[:start]) + cut.replacement + "".join(lines[end + 1 :]), removed


# The digest of what each cut takes from `root`'s shared files, in CUTS order — CUT_SHA256 is this
# run on the template.
def cut_digests(root: Path) -> dict[str, str]:
    edited: dict[str, str] = {}
    digests: dict[str, str] = {}
    for cut in CUTS:
        result = _apply_cut(edited.get(cut.path, _read(root, cut.path)), cut)
        if isinstance(result, str):
            raise ValueError(result)
        edited[cut.path], taken = result
        digests[_cut_key(cut)] = _text_digest(taken)
    return digests


def _tracked_text_files(root: Path) -> list[str]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            env=hermetic_env(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\n")
    except (subprocess.CalledProcessError, FileNotFoundError):
        listed = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
    suffixes = {".py", ".md", ".json", ".toml", ".yml", ".yaml", ".sh", ".txt", ".ini", ".cfg"}
    return sorted(
        name
        for name in listed
        if name
        and (root / name).is_file()
        and (Path(name).suffix in suffixes or Path(name).name == "Makefile")
        and not name.startswith((".venv/", "logs/"))
    )


# Recursive: a vertical kept in a package of its own (project/domain/booking/model.py) is a
# vertical too, and a search of the top level alone let the extraction go ahead past it.
def _own_vertical_files(root: Path) -> list[str]:
    found: list[str] = []
    for directory, kernel in KERNEL_FILES.items():
        for path in sorted((root / directory).rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            nested = path.parent != root / directory
            if (nested or path.name not in kernel) and relative not in SAMPLE_SHA256:
                found.append(relative)
    for path in sorted((root / "alembic/versions").glob("*.py")):
        relative = f"alembic/versions/{path.name}"
        if relative not in SAMPLE_REVISIONS:
            found.append(relative)
    return found


def _drop_migration() -> str:
    return f'''"""drop the reference vertical's table

Revision ID: {DROP_REVISION}
Revises: {SAMPLE_HEAD}
Create Date: appended by scripts/extract_reference_vertical.py

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "{DROP_REVISION}"
down_revision: Union[str, None] = "{SAMPLE_HEAD}"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The three revisions before this one created reference_tasks and every database made from the
# template ran them, so the table is dropped here rather than those revisions deleted. A database
# that stored rows in it loses them at `make migrate`.
def upgrade() -> None:
    op.drop_table("reference_tasks")


def downgrade() -> None:
    op.create_table(
        "reference_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=50), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_reference_tasks_status_created_at",
        "reference_tasks",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_reference_tasks_open_title",
        "reference_tasks",
        [sa.text("lower(title)")],
        unique=True,
        postgresql_where=sa.text("status <> 'done'"),
    )
'''


def _project_context(text: str) -> str:
    data = json.loads(text)
    data["verticals"].pop("reference_task", None)
    data["business_rules"] = {
        rule: entry
        for rule, entry in data["business_rules"].items()
        if not (isinstance(entry, dict) and entry.get("vertical") == "reference_task")
    }
    overview = data.get("api_overview", {})
    notes = overview.get("notes")
    if isinstance(notes, str):
        kept = [s for s in notes.split("; ") if "reference vertical" not in s]
        overview["notes"] = "; ".join(kept)
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _readme(removed: list[tuple[str, str]]) -> str:
    moved = "\n".join(f"- `{path}`" for path in SAMPLE_FILES)
    fragments = "\n\n".join(
        f"### `{path}`\n\n```{'python' if path.endswith('.py') else ''}\n{text.rstrip()}\n```"
        for path, text in removed
    )
    return f"""# The reference vertical this project was made from

`make init-project` took the template's worked example out of the application. Its files are here
exactly as the template shipped them, at the same relative paths; nothing imports, collects or
checks them. Copy from them — `.agents/skills/add-vertical/SKILL.md` gives the order of work.

{moved}

The three migrations that created its table stay in `alembic/versions/` — every database made from
the template ran them — and `{DROP_MIGRATION}` drops the table after them.

## Cut from shared files

What the vertical added to files every vertical shares, as it stood there.

{fragments}
"""


# Everything the extraction would write and delete, or the reason it will not act.
def build_plan(root: Path) -> Plan | str:
    context_path = root / "docs/project_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    if context.get("is_template") is not False:
        return "this is the template (is_template is not false): nothing to take out"
    if all(not (root / path).exists() for path in SAMPLE_SHA256) and (root / SCAFFOLD).is_dir():
        return "already done: the reference vertical is in " + SCAFFOLD

    reasons: list[str] = []
    if context.get("project_name") == "Mayak":
        reasons.append("docs/project_context.json still names the template (project_name)")
    changed = [
        path
        for path, digest in SAMPLE_SHA256.items()
        if not (root / path).is_file() or _digest(root / path) != digest
    ]
    if changed:
        reasons.append(
            "these files of the vertical differ from the template's: " + ", ".join(changed)
        )
    own = _own_vertical_files(root)
    if own:
        reasons.append("the project already has files of its own vertical: " + ", ".join(own))
    if reasons:
        return "; ".join(reasons)

    plan = Plan()
    edited: dict[str, str] = {}
    removed: list[tuple[str, str]] = []
    foreign: list[str] = []
    for cut in CUTS:
        current = edited.get(cut.path, _read(root, cut.path))
        result = _apply_cut(current, cut)
        if isinstance(result, str):
            return "the template's text changed under this script — " + result
        edited[cut.path], taken = result
        if _text_digest(taken) != CUT_SHA256.get(_cut_key(cut)):
            foreign.append(f"{cut.path} (from {cut.first.strip()!r})")
        if cut.keep_in_readme:
            removed.append((cut.path, taken))
    if foreign:
        return (
            "these shared files carry lines the template did not ship where the vertical's lines "
            "would be cut, and cutting would take them too: "
            + ", ".join(foreign)
            + ". Move them out of those spans, or take the vertical out by hand: "
            "`.agents/skills/add-vertical/SKILL.md`, 'Deleting the reference vertical'"
        )

    edited["docs/project_context.json"] = _project_context(_read(root, "docs/project_context.json"))

    deleted = set(SAMPLE_SHA256)
    for path in TEMPLATE_ONLY:
        target = root / path
        if target.is_dir():
            deleted.update(str(p.relative_to(root)) for p in target.rglob("*") if p.is_file())
        elif target.is_file():
            deleted.add(path)

    # Every mention of a moved file now names its copy, so a reference stays one that resolves.
    # Not in tests: a path there is as often fixture text — a recorded traceback, a log line — as a
    # reference, and rewriting fixture text changes what the test asserts.
    for name in _tracked_text_files(root):
        if name in deleted or name.startswith((SCAFFOLD, "tests/")) or name in SAMPLE_REVISIONS:
            continue
        text = edited.get(name, _read(root, name))
        pointed = text
        for path in SAMPLE_SHA256:
            pointed = pointed.replace(f"{SCAFFOLD}/{path}", "\0").replace(
                path, f"{SCAFFOLD}/{path}"
            )
            pointed = pointed.replace("\0", f"{SCAFFOLD}/{path}")
        if pointed != text:
            edited[name] = pointed

    for path in SAMPLE_SHA256:
        plan.writes[f"{SCAFFOLD}/{path}"] = _read(root, path)
    plan.writes[f"{SCAFFOLD}/README.md"] = _readme(removed)
    plan.writes[DROP_MIGRATION] = _drop_migration()
    plan.writes.update(edited)
    plan.deletes = sorted(deleted)
    return plan


def apply_plan(root: Path, plan: Plan) -> list[str]:
    for relative, text in plan.writes.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for relative in plan.deletes:
        (root / relative).unlink(missing_ok=True)
    for directory in TEMPLATE_ONLY:
        target = root / directory
        if target.is_dir():
            for leftover in sorted(target.rglob("*"), reverse=True):
                leftover.rmdir() if leftover.is_dir() else leftover.unlink()
            target.rmdir()
    # The project's own modules first. An editable install puts the checkout the interpreter was
    # installed from on sys.path, so run against a copy — by the tests, by check-product — a
    # generator imported the template's modules and rewrote the template's files, leaving the
    # copy's stale: measured on 2026-09-24.
    env = hermetic_env()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(root), env.get("PYTHONPATH")]))
    failures: list[str] = []
    steps = (
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--quiet",
            *sorted(p for p in plan.writes if p.endswith(".py")),
        ],
        [sys.executable, "scripts/sync_agent_docs.py"],
        ["git", "add", "-A", "--", *sorted(set(plan.writes) | set(plan.deletes)), "AGENTS.md"],
    )
    for command in steps:
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            failures.append(
                f"{' '.join(command[:3])}: {(result.stderr or result.stdout).strip()[:300]}"
            )
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-manifest", action="store_true")
    args = parser.parse_args([] if argv is None else argv)
    root = args.root.resolve()

    if args.print_manifest:
        print("SAMPLE_SHA256:")
        for path in SAMPLE_FILES:
            print(f'    "{path}": "{_digest(root / path)}",')
        print("CUT_SHA256:")
        for key, digest in cut_digests(root).items():
            print(f"    {json.dumps(key, ensure_ascii=False)}: {json.dumps(digest)},")
        return 0

    plan = build_plan(root)
    if isinstance(plan, str):
        print(f"reference vertical kept: {plan}")
        return 0
    if args.dry_run:
        print(f"would write {len(plan.writes)} files and delete {len(plan.deletes)}:")
        for path in sorted(plan.writes):
            print(f"  write  {path}")
        for path in plan.deletes:
            print(f"  delete {path}")
        return 0
    failures = apply_plan(root, plan)
    print(
        f"reference vertical taken out: {len(plan.deletes)} files removed, its copy is in "
        f"{SCAFFOLD}/, {DROP_MIGRATION} drops its table. The changes are staged; review them "
        "with `git diff --cached`, then run `make quality-gates`."
    )
    for failure in failures:
        print(f"  follow-up failed, the changes above stay for you to inspect: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
