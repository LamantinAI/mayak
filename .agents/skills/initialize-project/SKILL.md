---
name: initialize-project
description: Use this skill when asked to initialize, bootstrap, or set up a repository created from the Mayak template so local development is ready. Trigger it for requests like "initialize the project", "bootstrap this repo", "set up the project locally", or "prepare the repo for work".
triggers: [init, bootstrap, setup, initialize]
minimal_read_set:
  - CLAUDE.md
  - docs/project_context.json
validation_command: make quality-gates
---

# Initialize Project

One-shot bootstrap of a checkout: install the toolchain, then replace the template's identity with
this service's own. The second half is the part that gets skipped, and the cost lands later — an
OpenAPI page titled "Mayak API" in production, or `uv lock --check` failing in a gate that has
nothing to do with what you were changing.

## When to use it

- The user asks to initialize or bootstrap the repository.
- The user wants local development setup.
- The user wants `.env` created from `.env.sample` without overwriting an existing file.

## Workflow

1. `make init-project`. It runs `./dev_setup.sh` (dependencies, git hooks, `.env` from
   `.env.sample` only if absent), then `bootstrap` and `overview`, then `workset diff` when the
   worktree already has changes. It is idempotent — an existing `.env` is never overwritten.
2. Decide whether this repository **is** the template or a project built from it. If it is the
   template, stop here. Everything below assumes it is a new service.
3. Replace the identity in all six places below.
4. `make update-deps` — this is `uv lock`, and it is what makes step 3's `pyproject.toml` rename
   real. Skipping it leaves `uv.lock` naming the template package, and `uv lock --check` — the
   first line of `make quality-gates` — fails on a file you did not touch.
5. `make refresh-generated-docs`. `docs/project_map.md` renders the project name from
   `docs/project_context.json`, and `CLAUDE.md` is generated too; without this the next gate fails
   on artifact drift rather than on your work.
6. Decide whether this project needs a relational store. If it does not — a vector-search-only or
   stateless service — set `POSTGRES_ENABLED=false` in `.env` and say so in the `postgres` entry of
   `docs/project_context.json`. The kernel then starts without a connection pool, runs no
   migrations, and stops reporting the database as a critical readiness check. Leaving the default
   `true` keeps PostgreSQL required, which is right for most services. See
   `docs/adr/ADR-006-optional-postgres.md`.
7. `make quality-gates`, then continue with `.agents/skills/add-vertical` for the first feature.

## The six places the template's identity lives

| # | Where | What to change | What notices if you don't |
|---|-------|----------------|---------------------------|
| 1 | `docs/project_context.json` | `project_name`, `domain`, and `is_template: false` | `validate_project_context.py` — `project_context.template_identity_not_replaced`. The only one that fails a gate on its own. |
| 2 | `pyproject.toml` | `name` and `description` | `uv lock --check`, but only after step 4 above regenerates the lockfile. |
| 3 | `.env` and `.env.sample` | `APP_NAME` | Nothing. It becomes the OpenAPI title at runtime — `f"{settings.project.name} API"` in `composition_root.py`. |
| 4 | `project/core/config_settings_core.py` | the `default=` of `ProjectSettings.name` | `tests/application/test_config.py::test_default_values`, which compares it with #1. It is the fallback when `APP_NAME` is unset, so a deployment without that variable would otherwise serve the template's name. |
| 5 | `PROJECT.md` | the whole business description — name, what the service does, its glossary | Nothing. It is what the next agent reads to learn what this project is. |
| 6 | `README.md` | more than the name — see below | Nothing. |

`tests/functional/.env.sample` carries `APP_NAME` too; change it with #3.

**Row 6 is a rewrite, not a rename, and it is the only one that takes longer than a minute.**
`git grep -n <template name> README.md` finds three lines; renaming those three leaves a README
that calls your service a template, tells the reader it ships one vertical to copy, lists what it
deliberately does not include, and counts how many services have been built on it. All of that is
written about the template and none of it contains the name, so no grep surfaces it. What a project
needs in its place is short: what the service does, its routes, and how to run it — the Quick start,
Checking your work, Reading a running service and Environment configuration sections carry over
unchanged. Budget fifteen minutes and write it after the first vertical exists, when there is
something true to say. Three tests read this file, so keep the phrase `Python 3.13+` with `3.13` in
backticks, the `| \`KEY\` | ... | \`value\` |` shape of the environment tables, and every skill name
in backticks.

Kernel files that mention Mayak in their own docstrings — `ai_query/`, `scripts/`, ADRs — are
naming the template they came from. Leave them alone.

Two places used to belong on this list and no longer do, because carrying a name that can go
stale was the defect: `project/__init__.py`'s SUMMARY and the temporary file `make audit-deps`
writes are both generic now. `tests/application/test_template_neutrality.py` still checks the
operational contract's heading and the audit-deps temp path. It no longer forbids the project's
own name elsewhere under `project/` — a project is free to name itself in its own prompt file and
in code comments; only the fallback default in #4 is pinned, by
`tests/application/test_config.py::test_default_values`.

## Expected output

- Dependencies installed, git hooks configured, `.env` present.
- `docs/project_context.json` describing this service, with `is_template: false`.
- `uv.lock` and the generated artifacts regenerated.
- `make quality-gates` green.
