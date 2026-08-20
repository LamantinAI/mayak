# Project Context

> This file describes **WHAT** the project does (business domain).
> `CLAUDE.md` describes **HOW** the kernel works (technical structure).
>
> The machine-readable counterpart lives in `docs/project_context.json`.

## Project Name

Mayak

## Domain Description

Mayak is a reusable AI-friendly FastAPI backend template. It ships
the kernel infrastructure that every project built on top of it inherits:

- `CompositionRoot` that wires shared services (Postgres pool, LLM service) into
  `app.state.services`.
- Semantic NDJSON logging with LLM-friendly trace formatting.
- `LLMService` (live + mock mixin) with **mock-first** default per ADR-003,
  so the kernel boots and CI runs deterministically without a real provider.
- Mayak AI tooling: query CLI (`scripts/query_ai_context.py`),
  validators (`scripts/validate_*.py`), doctor (`scripts/doctor_ai_context.py`),
  skills under `.agents/skills/`, and generated AI artifacts under `docs/`.

Beyond health checks the kernel exposes one worked example. Concrete features
(chat, RAG, agentic pipelines, CRUD APIs, …) are added by **verticals** on top of
the kernel — see "How to extend" below.

## Verticals / Features

One, and it exists to be copied: **`reference_task`** — domain model, port,
psycopg repository, application service, DTOs, endpoint, unit tests, functional
tests, plus the three wiring edits. Storage-only on purpose; it touches no LLM,
and its routes register only when `POSTGRES_ENABLED` is true.

Every file is named `reference_task*`, so a project deletes the example in one
command once its own vertical works. `.agents/skills/add-vertical/SKILL.md` carries both
the copying instructions and the removal list.

The recommended pattern is:

1. Add the domain model and its port.
2. Add the application service and its DTOs.
3. Add the infrastructure adapters — repository, endpoint.
4. Wire the services in `project/core/service_registration.py:build_reference_services`
   (or add a vertical-specific builder and call it from `CompositionRoot`).
5. Expose typed dependency aliases in `project/infrastructure/api/dependencies.py`.
6. Include the endpoint's router in `project/infrastructure/api/router_registration.py`.
7. Cover with unit and functional tests, regenerate AI artifacts
   (`make refresh-generated-docs`), and run `make quality-gates`.

**Where the files go depends on how many verticals the project has.** One or two verticals stay
flat, named by prefix: `project/domain/<vertical>.py`, `project/application/<vertical>_service.py`,
`project/infrastructure/persistence/<vertical>_repository.py`,
`tests/application/test_<vertical>_vertical.py`. That is what the shipped `reference_task` vertical
does, and `.agents/skills/add-vertical/SKILL.md` maps its eleven files onto the steps above. A project that
grows past a handful of verticals moves each into its own package —
`project/domain/<vertical>/`, `project/application/<vertical>/` — and the steps are unchanged;
only the paths get one level deeper.

Scaffolding is manual either way: there is no generator, because the shape of a vertical depends on
the project's domain. But it is copying, not invention.

## Integrations

| Name | Type | Description |
|------|------|-------------|
| postgres | database | Optional relational store, on by default. The kernel manages an `AsyncConnectionPool` in `CompositionRoot`; verticals add ORM models and migrations on top. `POSTGRES_ENABLED=false` starts the kernel without a pool, skips Alembic, and drops the database from the readiness contract — see `docs/adr/ADR-006-optional-postgres.md`. |
| openai_compatible_llm | llm | Consumed by `project.infrastructure.agents.LLMService`. Default mode is `mock` (ADR-003); set `AGENT_LLM_MODE=live` plus `OPENAI_COMPATIBLE_API_KEY` to call a real provider. |

## Business Rules

_No formal business rules in the kernel. Verticals introduce them with `BR-NNN`
identifiers when needed._

## Glossary

| Term | Definition |
|------|-----------|
| vertical | A self-contained feature slice spanning domain, application, infrastructure, and test layers. |
| composition_root | The single location (`project/core/composition_root.py`) where shared services are constructed and injected into FastAPI's `app.state`. |
| service_registration | Per-vertical service-construction helpers in `project/core/service_registration.py`. |
| wiring_hotspot | One of the four files that bind layers together; the list is in CLAUDE.md under "How the kernel is shaped". |
| edit_zone | Risk classification of a file: safe / caution / expert / generated_do_not_edit. |
| kernel | The reusable template surface — AI tooling, validators, composition root, semantic logging — inherited by every project built on Mayak. |
| mayak | This template repository itself. The kernel referenced above. |
| reference_implementation | An example feature added on top of the kernel. The scaffold ships exactly one — the `reference_task` vertical — as the shape to copy and then delete. |
| mock_mode | Deterministic `LLMService` behaviour controlled by `AGENT_LLM_MODE=mock`. ADR-003 makes mock the default for local dev and CI. |

## Project-Specific Tech Decisions

- FastAPI + uvicorn as the HTTP runtime (`project/launcher/main.py`).
- Clean architecture with vertical slicing: domain → application → infrastructure,
  composed in `project/core/composition_root.py`.
- Semantic NDJSON logging (`project/core/logging/`)
  and `prepend_trace_summary` on shutdown for LLM-friendly post-mortems.
- Mock-first LLMService default (ADR-003) so the kernel is runnable and CI is
  deterministic without a real provider.
- `langchain-core` and `langchain-openai` are dependencies because `LLMService`
  imports them. Nothing else from that ecosystem ships by default — a vertical
  that wants graph orchestration adds `langgraph` itself.
