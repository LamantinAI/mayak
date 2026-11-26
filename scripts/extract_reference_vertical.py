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
# computed and checked before the first file is written: a path it creates must be free or already
# hold the same bytes, and it deletes only files the template shipped, unchanged, as listed in
# TEMPLATE_ONLY_SHA256. Bootstrap asks the same plan before it writes the identity.
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
    "project/domain/reference_task.py": "78b81dc89af353f3a12fd2d5e713b8bff4945e0acb048e2356106760dfed34a4",
    "project/application/reference_task_service.py": "6ceb1ba2d6d4ecb2b71564a8c4004594b3b940a42778f45444b197fa3159a689",
    "project/application/reference_task_dtos.py": "31f2578ce6404a3f9a8c6b3438adb9ba1da301a18ec616faec961fad9938c5bc",
    "project/infrastructure/persistence/reference_task_repository.py": "f0c7f2757b9ddd20dc179b1cd521677d70fdd87f4c180985dc7a03c2671b9dd2",
    "project/infrastructure/api/endpoints/reference_tasks.py": "cf4be1ec16fa1035a351a915600ae627c0e20fa2b8e1ede45a5c851d67845ba9",
    "tests/application/test_reference_task_vertical.py": "a9c3cfdf65b7ea2ff528f8d35cee74b86b5896b25141e5634c8a018eb4c859e1",
    "tests/db/test_reference_task_repository.py": "e742179476867ef93c2e6d498b5b109456071c26ffba8345e4da3c67de8be784",
    "tests/db/test_reference_tasks_api.py": "a52e31eaf49bb24096bbac8368ade29d1261537abc6f85457dcda013c771184c",
    "tests/functional/src/test_reference_tasks_api.py": "fa54fafdc5361aedc71c7ee27c0310dfe8fc13a42aa77c3b59ea125ebe56bc5a",
}

# Files the template needs and a project does not: removed, not kept.
TEMPLATE_ONLY = (
    "tests/template",
    "docs/mutations",
    "scripts/run_mutations.py",
    "scripts/check_product_from_template.py",
    "scripts/extract_reference_vertical.py",
)

# The extractor also needs protection when a product commits edits to it.
SELF = "scripts/extract_reference_vertical.py"

# Every file under TEMPLATE_ONLY the template ships, with its digest: the only files the extraction
# deletes there. Until 1.3.2 it deleted what `git ls-files` listed, and a project's own commit is
# listed as readily as the template's — a note committed in tests/template went with it, exit 0.
# A repository made with GitHub's template button has its own first commit and no Mayak tag, so
# ownership cannot be read from history; this list travels with the files. A file found there that
# is not in it, or one in it whose bytes differ, stops the extraction. Regenerate with
# --print-manifest after editing any of them; tests/template fails while it is stale.
TEMPLATE_ONLY_SHA256: dict[str, str] = {
    "docs/mutations/reference_task.json": "87fde1370e37eb60dc0e076341aded2d383ce21421191d80d5f4b265bada23f8",
    "scripts/check_product_from_template.py": "35cfa1f27f2649be0eebedfcc4689032f5ae558b6a3473f919a31211deab86b4",
    "scripts/extract_reference_vertical.py": "6820311678848897e52f442d846ab656c09681297bef864e44a61dc8ad32e534",
    "scripts/run_mutations.py": "3ce90ff6906daff31c69115f19a9f9707df1ee22e9999e6e1468f5e13d12de71",
    "tests/template/test_agent_onboarding.py": "d0804469addaebf1b276a34508f2cabd701ee6b50bf434185df19c5e95c69cc0",
    "tests/template/test_create_env_file.py": "070a36a2c576bed235fe17ced50656fae4a173b59d109b0ebf961849500a4cd2",
    "tests/template/test_doctor_ai_context.py": "0b5888edd08358fa77147667a6f5f2ebc93942e805ef2f25a8122d7d7c467a4d",
    "tests/template/test_extract_reference_vertical.py": "f1b0c500e7ed364be6a836adc7abc2bed65d68e72c8aafbea126d0aa6ba72c32",
    "tests/template/test_gate_recipes.py": "12f538fddb03c38fcae5a98517411832939c7f3ae3877c0c15958bd8af4829ba",
    "tests/template/test_optional_postgres_tooling.py": "efe50706fb2b2883da8aef28bb28868bfcaeb6c64d209371d4a15528ccee2f61",
    "tests/template/test_product_line_count.py": "81c2fe58d852aa1fd3187c8ba13d4d864ba16d5493b80f1173967e3557ef69b0",
    "tests/template/test_run_all_tests.py": "d6de51619ee8f7e9b259392f702ef2b8eabddcd5078ee76f7c94b1dbfac34254",
    "tests/template/test_run_mutations.py": "d6c53e6d464027b1ebb13c1cd4a9a9e6bee49e020fb6dcca6af7bc66e7b9c0e8",
    "tests/template/test_skill_texts_match_reality.py": "0420cfc872f133cff67a8d8996c13270aaaceec606a6de4e12c0987bbb109738",
    "tests/template/test_sync_agent_docs.py": "c2625e2c9654d0dbffcc604e44f49ea1718fd3cf7a493c5479bd21b53e3311b6",
    "tests/template/test_template_neutrality.py": "3da43661a83a0a220891bfdd8e0f27445ff23021e4e0916ffe0decd213420221",
    "tests/template/test_validate_architecture.py": "692538f49552b0372f730102bd63c7b771299a0273d3e6477c452624a3dac560",
    "tests/template/test_validate_cbm.py": "9f795ca6b3fea0efbcfc4a336a925b981e013d74ec5c32f9ab4779dbc1426997",
    "tests/template/test_validate_dependencies.py": "969bc7b858c7b22e0390017e0b7c1f1d92a592f6f1233c7f8ce68e81ecc5df57",
    "tests/template/test_validate_endpoint_wiring.py": "3927886f1f7f4952b6f1a31e5e62f95524e08ace3d14b172a6923211ae326122",
    "tests/template/test_validate_migrations.py": "99bcbebd7f1211123bcaaf6d9aa9c8b5f69003f34d3e5b0d9619ebdcc3063925",
    "tests/template/test_validate_module_sizes.py": "9170f4ba8c1d00cbd0fe19178d61e15a5716ee36cbf254abc220e63fdd951b3a",
    "tests/template/test_validate_repository_metadata.py": "62d4466021142087d9336929cdc970af7734188ebf1ada3b8e1ab0ef6063991b",
    "tests/template/test_validate_runtime_ownership.py": "6b5dd1c70fcd25b0fd62e175a904ed61f0e165b61cdd80afb763501aa549c1e2",
    "tests/template/test_validate_secrets.py": "3ffd5e701066ac6f47fea7e2fa560c2a80a0adb651bec0a18489db335f0c2674",
    "tests/template/test_validate_test_quality.py": "aec440fbfa42e237c39af516287e558982aba1639ca08569c505ed05e970cce8",
    "tests/template/test_validator_error_contract.py": "8c978439e040d46ff25eaa6b04cbf652afdebf266a853dbc429eef68159ffb5d",
}

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
_QUICK_START = '- **New service.** Create a repository with GitHub’s template action, then run `make bootstrap NAME="Service Name" DOMAIN="What it does" DB=postgres` (or `DB=none`). Bootstrap checks readiness and leaves the result for review.'


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
        "from sqlalchemy import MetaData\nfrom sqlalchemy.orm import DeclarativeBase\n",
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
        "- **Existing service.** Start with `PROJECT.md`. The preserved example is in "
        f"`{SCAFFOLD}/` if you need to add a PostgreSQL feature.\n",
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
    # The job that makes a project from the template has nothing to make in a project: it started
    # a PostgreSQL service, installed everything and printed "belongs to the template" on every
    # push of every project (round-4 finding K1, its second half).
    Cut(
        ".github/workflows/ci.yml",
        "  # A project made from this template today: its identity replaced, the reference vertical taken",
        "        run: make check-product",
        "",
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
    "project/infrastructure/persistence/orm_models.py: from datetime import datetime": "dceb2a5582f6c2da0537b89ae0c0c8cb4b180b7b7aeeb7e8f41c7fd697ab7f6b",
    "project/infrastructure/persistence/orm_models.py: # Alembic reads this metadata; nothing reads the ORM at runtime, because ReferenceTaskRepository speaks raw psycopg. Verticals add their own tables alongside it and delete this one with the rest of the example.": "ac37054fd19d77cc08654e55e4ba5a2938d8f7a25f6d357712e03be906060704",
    "project/infrastructure/persistence/__init__.py: # writes go through psycopg against the shared pool; reference_task_repository.py is the worked": "65e8404cb3529b13c13657d8c32f2dcf7b38c6de1ac7246f73cf93f25341ba60",
    'docs/agent_rules.md: - **New service.** Create a repository with GitHub’s template action, then run `make bootstrap NAME="Service Name" DOMAIN="What it does" DB=postgres` (or `DB=none`). Bootstrap checks readiness and leaves the result for review.': "39a5ce93e62ce55ad01787cfc13564ea983f82cb97785c0708908287b19a152b",
    ".agents/skills/add-vertical/SKILL.md: # Add Vertical": "ea123ba5901f1bcc464ff4cb4060688b2793f1870dfead15c1b909435f86c289",
    ".github/workflows/ci.yml:   # A project made from this template today: its identity replaced, the reference vertical taken": "55b36f3e5e38d563f0a2630cc6834b7073ebc65d1dc97b3b08730aed7278b550",
    ".agents/skills/add-vertical/SKILL.md: ## Deleting the reference vertical": "62015290e767a42e5baa92a5e8141d7bf687760d484323cb6d545bb3b7dfdbab",
}


def _cut_key(cut: Cut) -> str:
    return f"{cut.path}: {cut.first}"


@dataclass
class Plan:
    writes: dict[str, str] = field(default_factory=dict)
    deletes: list[str] = field(default_factory=list)
    # Tool leftovers git ignores under TEMPLATE_ONLY: removed with it, never staged.
    generated: list[str] = field(default_factory=list)
    # What every written or deleted path held when the plan was made: a digest, or None for a path
    # that was free. apply_plan writes nothing unless all of them still hold it.
    expect: dict[str, str | None] = field(default_factory=dict)


# What a tool run leaves there and nobody writes by hand. Any other file git ignores may be the
# project's own (a .env, notes kept out of git): it stops the extraction like other local work.
_GENERATED_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})


def _generated(relative: str) -> bool:
    path = Path(relative)
    return path.suffix in {".pyc", ".pyo"} or not _GENERATED_DIRS.isdisjoint(path.parts[:-1])


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


# Every file now on disk under TEMPLATE_ONLY including this script, tracked or not, tool leftovers aside:
# what the extraction has to account for there before it deletes anything.
def _template_only_files(root: Path) -> list[str]:
    found: list[str] = []
    for path in TEMPLATE_ONLY:
        target = root / path
        if target.is_dir() and not target.is_symlink():
            found += [
                p.relative_to(root).as_posix()
                for p in target.rglob("*")
                if p.is_symlink() or not p.is_dir()
            ]
        elif target.exists() or target.is_symlink():
            found.append(path)
    return sorted(name for name in found if not _generated(name))


def _template_only_digest(root: Path, name: str) -> str:
    path = root / name
    if name != SELF:
        return _digest(path)
    # Hash the source including its manifest, masking only its own digest slot.
    # After refreshing other manifest entries, rerun --print-manifest for this entry.
    slot = f'    "{SELF}": "{TEMPLATE_ONLY_SHA256[SELF]}",'.encode()
    return hashlib.sha256(path.read_bytes().replace(slot, b"<self digest>", 1)).hexdigest()


# TEMPLATE_ONLY_SHA256 as `root` ships it: what git lists there, ignored files left out, so a
# leftover of the template author's own does not enter the list. Outside a repository, the disk.
def template_only_digests(root: Path) -> dict[str, str]:
    try:
        listed = set(
            subprocess.run(
                [
                    "git",
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "--",
                    *TEMPLATE_ONLY,
                ],
                cwd=root,
                env=hermetic_env(),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split("\n")
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        listed = set(_template_only_files(root))
    return {
        name: _template_only_digest(root, name)
        for name in _template_only_files(root)
        if name in listed and (root / name).is_file()
    }


# What a path holds, as Plan.expect records it: the digest of a regular file, None when nothing is
# there, and a marker for anything else — a directory, a link — that no write may replace.
def _state(target: Path) -> str | None:
    if target.is_symlink() or (target.exists() and not target.is_file()):
        return "not a regular file"
    return _digest(target) if target.exists() else None


# Why a new file cannot be put at `relative`, or None when the path is free or already holds
# exactly `text` — a step done before is done. Nothing in the way is overwritten: a draft an agent
# kept in the scaffold's README went under the extraction's own until 1.3.2, exit 0.
def _destination_problem(root: Path, relative: str, text: str) -> str | None:
    for parent in reversed(Path(relative).parents[:-1]):
        held = root / parent
        if held.is_symlink() or (held.exists() and not held.is_dir()):
            return f"{relative} (create: {parent.as_posix()} is not a directory)"
    target = root / relative
    state = _state(target)
    if state is None or state == hashlib.sha256(text.encode("utf-8")).hexdigest():
        return None
    if state == "not a regular file":
        return f"{relative} (create: something other than a file is there)"
    return f"{relative} (create: a different file is there)"


# Under TEMPLATE_ONLY: (local work deleting would lose, tool leftovers git ignores). Local work is a
# file git does not know, an uncommitted or staged edit, an ignored file that is not a leftover; a
# file the project deleted is not. Until 1.3.1 the directories went whole, and with them a test or a
# note no plan had named. Outside a repository nothing tells local work apart: leftovers only.
def _template_only_state(root: Path) -> tuple[list[str], list[str]]:
    command = ["git", "status", "--porcelain", "-z", "--no-renames", "--untracked-files=all"]
    try:
        status = subprocess.run(
            [*command, "--ignored", "--", *TEMPLATE_ONLY],
            cwd=root,
            env=hermetic_env(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        found = (p for path in TEMPLATE_ONLY for p in (root / path).rglob("*") if p.is_file())
        return [], sorted(n for n in (str(p.relative_to(root)) for p in found) if _generated(n))
    local: list[str] = []
    generated: list[str] = []
    for entry in filter(None, status.split("\0")):
        code, name = entry[:2], entry[3:]
        if code == "!!" and _generated(name):
            generated.append(name)
        elif code.strip(" D"):
            local.append(name)
    return sorted(local), sorted(generated)


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
        if relative not in SAMPLE_REVISIONS and relative != DROP_MIGRATION:
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
def build_plan(root: Path, *, converting: bool = False) -> Plan | str:
    context_path = root / "docs/project_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    if not converting and context.get("is_template") is not False:
        # bench3's agent met this line in a project and went on to read the script; name the route.
        return (
            "this is the template (is_template is not false): nothing to take out. In a project "
            "made from it, replace the identity first — .agents/skills/initialize-project, steps "
            "3-5 — then run `make init-project`"
        )
    if all(not (root / path).exists() for path in SAMPLE_SHA256) and (root / SCAFFOLD).is_dir():
        return "already done: the reference vertical is in " + SCAFFOLD

    reasons: list[str] = []
    if not converting and context.get("project_name") == "Mayak":
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
    local, generated = _template_only_state(root)
    if local:
        reasons.append(
            "the template's own paths, which the extraction deletes, hold local work git has not "
            "committed: "
            + ", ".join(local)
            + ". Move it out of those paths, restore or delete it, "
            "then run again"
        )
    present = [name for name in _template_only_files(root) if name not in local]
    unshipped = [name for name in present if name not in TEMPLATE_ONLY_SHA256]
    if unshipped:
        reasons.append(
            "the template's own paths, which the extraction deletes, hold files the template did "
            "not ship, committed or not: "
            + ", ".join(unshipped)
            + ". They are the project's: move "
            "them out of those paths, then run again"
        )
    edited_shipped = [
        name
        for name in present
        if name in TEMPLATE_ONLY_SHA256
        and (
            (root / name).is_symlink()
            or _template_only_digest(root, name) != TEMPLATE_ONLY_SHA256[name]
        )
    ]
    if edited_shipped:
        reasons.append(
            "files the template ships in its own paths, which the extraction deletes, differ from "
            "what it shipped, committed or not: " + ", ".join(edited_shipped) + ". Keep the "
            "change outside those paths or restore the file, then run again"
        )
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

    # A shipped file the project already deleted is skipped, as before. Bytecode a test run left
    # there (bench3, 2026-09-27) failed the whole staging step when named, so it goes in
    # Plan.generated, never here.
    deleted = set(SAMPLE_SHA256) | {name for name in present if name in TEMPLATE_ONLY_SHA256}

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

    created = {f"{SCAFFOLD}/{path}": _read(root, path) for path in SAMPLE_SHA256}
    created[f"{SCAFFOLD}/README.md"] = _readme(removed)
    created[DROP_MIGRATION] = _drop_migration()
    blocked = [
        problem
        for relative, text in created.items()
        if (problem := _destination_problem(root, relative, text))
    ]
    if blocked:
        return (
            "the paths the extraction creates hold something else, and it overwrites nothing: "
            + ", ".join(blocked)
            + ". Move what is there elsewhere, then run again"
        )
    plan.writes.update(created)
    plan.writes.update(edited)
    plan.deletes = sorted(deleted)
    plan.generated = generated
    plan.expect = {path: _state(root / path) for path in (*plan.writes, *plan.deletes)}
    return plan


# Deletes only what the plan names, then the directories that leaves empty: a file that appeared
# after the plan was made keeps its directory and is reported, never removed unseen.
# A path that changed since the plan was made — a file that appeared where one is created, an edit
# to one about to be rewritten or deleted — stops it before the first write: ValueError naming it.
def apply_plan(root: Path, plan: Plan, *, stage: bool = True) -> list[str]:
    moved = [path for path, held in plan.expect.items() if _state(root / path) != held]
    moved += [
        problem
        for path, text in plan.writes.items()
        if plan.expect.get(path) is None and (problem := _destination_problem(root, path, text))
    ]
    if moved:
        raise ValueError(
            "these paths changed after the plan was made, so nothing was written: "
            + ", ".join(sorted(set(moved)))
            + ". Run again"
        )
    for relative, text in plan.writes.items():
        target = root / relative
        if _state(target) == hashlib.sha256(text.encode("utf-8")).hexdigest():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative.startswith(SCAFFOLD + "/") or relative == DROP_MIGRATION:
            # A file may have appeared after the plan was checked. Never truncate a destination.
            try:
                with target.open("x", encoding="utf-8") as stream:
                    stream.write(text)
            except FileExistsError as exc:
                raise ValueError(f"{relative} changed during extraction; file kept") from exc
        else:
            target.write_text(text, encoding="utf-8")
    for relative in (*plan.deletes, *plan.generated):
        (root / relative).unlink(missing_ok=True)
    failures: list[str] = []
    for directory in TEMPLATE_ONLY:
        target = root / directory
        if not target.is_dir():
            continue
        for emptied in [
            *sorted((p for p in target.rglob("*") if p.is_dir()), reverse=True),
            target,
        ]:
            try:
                emptied.rmdir()
            except OSError:
                failures.append(
                    f"{emptied.relative_to(root)} kept: holds files the plan did not name"
                )
                break
    # The project's own modules first. An editable install puts the checkout the interpreter was
    # installed from on sys.path, so run against a copy — by the tests, by check-product — a
    # generator imported the template's modules and rewrote the template's files, leaving the
    # copy's stale: measured on 2026-09-24.
    env = hermetic_env()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(root), env.get("PYTHONPATH")]))
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
    )
    if stage:
        steps += (
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
    parser.add_argument("--no-stage", action="store_true")
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
        print("TEMPLATE_ONLY_SHA256:")
        for path, digest in template_only_digests(root).items():
            print(f'    "{path}": "{digest}",')
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
        print(f"  and {len(plan.generated)} tool leftovers git ignores there (bytecode, caches)")
        return 0
    try:
        failures = apply_plan(root, plan, stage=not args.no_stage)
    except ValueError as moved:
        print(f"reference vertical kept: {moved}")
        return 1
    review = (
        "Review with `git diff`."
        if args.no_stage
        else "Review the staged changes with `git diff --cached`."
    )
    print(
        f"reference vertical taken out: {len(plan.deletes)} files removed, its copy is in "
        f"{SCAFFOLD}/, {DROP_MIGRATION} drops its table. {review}"
    )
    for failure in failures:
        print(f"  follow-up failed, the changes above stay for you to inspect: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
