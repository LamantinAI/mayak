from __future__ import annotations

from pathlib import Path


SCHEMA_VERSION = 7

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT_DIR / "docs" / "ai_context_map.json"
CHANGE_MAP_OUTPUT_PATH = ROOT_DIR / "docs" / "ai_change_map.json"
ARCHITECTURE_RULES_OUTPUT_PATH = ROOT_DIR / "docs" / "architecture_rules.json"

COMPOSITION_ROOT_PATH = ROOT_DIR / "project" / "core" / "composition_root.py"
SERVICE_REGISTRATION_PATH = ROOT_DIR / "project" / "core" / "service_registration.py"
ROUTER_REGISTRATION_PATH = (
    ROOT_DIR / "project" / "infrastructure" / "api" / "router_registration.py"
)
DEPENDENCIES_PATH = ROOT_DIR / "project" / "infrastructure" / "api" / "dependencies.py"
ARCHITECTURE_ANTI_PATTERNS = (
    "Do not instantiate application services directly inside FastAPI endpoints.",
    "Do not bypass typed dependency aliases from project/infrastructure/api/dependencies.py.",
    "Do not read environment variables in business logic; use project.core.config.get_settings.",
    "Do not register routers outside project/infrastructure/api/router_registration.py.",
    "Do not create alternate service registries outside composition_root and service_registration.",
)

EDIT_ZONES = {
    "safe": [
        "project/application/",
        "project/infrastructure/api/endpoints/",
        "tests/application/",
        "PROJECT.md",
        "docs/project_context.json",
        # Catch-all for the remaining suites (infrastructure, integration, functional). Explicit
        # entries above still win. Without it, before-edit refused every test outside
        # tests/application/ — including the functional suite the change protocol now requires.
        "tests/",
        # Prose. Editing a document breaks nothing at runtime, and a reader who asks about one
        # should be told that rather than met with `Unknown or unindexed file policy path` — the
        # answer README.md, every ADR and docs/adr/README.md gave until now. The generated files
        # under docs/ keep their own zone: the longest matching pattern wins.
        "docs/",
        "README.md",
        "LICENSE",
        "CODEOWNERS",
    ],
    "caution": [
        "project/infrastructure/api/dependencies.py",
        "project/core/service_registration.py",
        "project/infrastructure/api/router_registration.py",
        # The three paths a typical vertical touches first. before-edit used to exit 1 with
        # `Unknown or unindexed file policy path` on all of them, so the guidance channel was
        # missing exactly where the change protocol sends the agent. Their own evolution reports
        # named this the largest single source of friction three times running.
        "project/domain/",
        "project/infrastructure/persistence/",
        # The whole alembic tree, not only versions/: env.py sits outside it and was unindexed.
        "alembic/",
        # Deployment surfaces. Unindexed until POSTGRES_ENABLED made them part of an ordinary
        # change: an agent making the database optional edits all four and would otherwise hit
        # `Unknown or unindexed file policy path` on every one of them.
        "docker-compose.yml",
        "docker-compose.postgres.yml",
        "entrypoint.sh",
        "Dockerfile",
        # Top-level config and tooling surfaces — derived guidance via EDIT_ZONES (no explicit entry).
        "Makefile",
        ".gitignore",
        ".github/workflows/",
        ".agents/skills/",
        # The hand-written source CLAUDE.md and AGENTS.md are generated from. It reads like an
        # ordinary document and is not one: an edit here is only half the change until
        # `make refresh-agent-docs` runs, and the pre-commit hook overwrites the wrappers.
        "docs/agent_rules.md",
        # Every project created from this template starts from these values, and the settings
        # models refuse some of them — a defaults file is not prose.
        ".env.sample",
        # Hooks, agent configuration and the runners that install them. A wrong line here changes
        # what every later edit is allowed to do.
        ".githooks/",
        ".agents/",
        ".codex/",
        ".claude/",
        "dev_setup.sh",
        # Build and test configuration read by tools rather than by the application.
        "alembic.ini",
        "pytest.ini",
        ".dockerignore",
        ".python-version",
        # Resolved by `uv lock`, not by hand, but a hand edit is a supply-chain change rather
        # than a formatting slip, so it is called out rather than filed under generated.
        "uv.lock",
        # Catch-all for the application package, mirroring the `tests/` and `scripts/` ones above.
        # Without it, 21 of 59 files under project/ — every config module, the launcher, the LLM
        # adapters — had no policy at all: before-edit exited 1 with `Unknown or unindexed file
        # policy path`, and validate_file_policy.py stayed green because it only checks the
        # explicit index. Safe only because zone_via_edit_zones_patterns picks the LONGEST
        # matching pattern: `project/core/logging/` and the rest keep their own zones.
        "project/",
    ],
    "expert": [
        "project/core/logging/",
        "project/core/composition_root.py",
        "project/core/lifecycle.py",
        "scripts/generate_ai_context.py",
        "scripts/query_ai_context.py",
        "scripts/structure_builder.py",
        "ai_context/",
        "ai_query/",
        "scripts/validate_runtime_ownership.py",
        # Catch-all for the rest of the kernel's tooling. Without it, before-edit answered
        # `Unknown or unindexed file policy path` for any script not named above — which silently
        # cost those files every guidance channel the kernel has. Explicit entries above still win
        # over this prefix.
        "scripts/",
    ],
    "generated_do_not_edit": [
        "CLAUDE.md",
        # The Codex half of the same generated pair. `make print-generated-paths` has always
        # listed it and the pre-edit guard has always refused writes to it, but this list named
        # only its Claude counterpart — so `before-edit AGENTS.md` exited 1 rather than saying
        # the one thing that matters about the file.
        "AGENTS.md",
        "docs/ai_context_map.json",
        "docs/ai_change_map.json",
        "docs/architecture_rules.json",
        "docs/project_map.md",
    ],
}

TEMPLATE_KERNEL_PATHS = [
    "scripts/query_ai_context.py",
    "scripts/generate_ai_context.py",
    "scripts/doctor_ai_context.py",
    "scripts/sync_agent_docs.py",
    "scripts/structure_builder.py",
    "scripts/validate_architecture.py",
    "scripts/validate_endpoint_wiring.py",
    "scripts/validate_runtime_ownership.py",
    "scripts/validate_cbm.py",
    "scripts/validate_migrations.py",
    "scripts/validate_module_sizes.py",
    "ai_context/",
    "ai_query/",
    "project/core/",
    "project/infrastructure/api/dependencies.py",
    "project/infrastructure/api/router_registration.py",
    "project/infrastructure/api/exception_handlers.py",
]

REFERENCE_IMPLEMENTATION_PATHS = [
    "project/prompts/",
]

QUERY_SHORTCUTS = [
    "workset",
    "before-edit",
    "failure",
]

# ATTRIBUTE: QUERY_COMMAND_NAMES (list[str])
# SUMMARY: Every top-level command scripts/query_ai_context.py accepts, named once.
# NOTE: Two copies of this list existed, and both had rotted in different directions. The one in
# scripts/generate_ai_context.py still offered `service`, `dependency`, `route`, `task`, `plan`,
# `recipe`, `impact`, `tests-for`, `next-checks`, `regenerate-for`, `policy`, `why`, `classify` and
# `minimal-context` as "temporarily unavailable" — fourteen commands removed long ago — so an agent
# meeting a degraded context was handed a menu of things that do not exist.
QUERY_COMMAND_NAMES = [
    "bootstrap",
    "overview",
    "workset",
    "before-edit",
    "failure",
    "symbol",
]

QUERY_SUPPORTED_COMMANDS = [
    "bootstrap",
    "overview",
    "workset diff",
    "before-edit file project/infrastructure/api/dependencies.py",
    "failure rule endpoint.no_depends_without_alias",
    "failure rule runtime_ownership.env_access_restricted",
    "failure rule drift.agent_docs.outdated",
    "symbol AgentSettings",
]

ZONE_RISK = {
    "safe": "low",
    "caution": "medium",
    "expert": "high",
    "generated_do_not_edit": "do-not-edit",
}

CANONICAL_EXAMPLES = {
    "thin_endpoint": "project/infrastructure/api/endpoints/health.py",
    "typed_dependency_aliases": "project/infrastructure/api/dependencies.py",
    "router_registration": "project/infrastructure/api/router_registration.py",
    "shared_service_wiring": "project/core/composition_root.py",
}

COLD_PATHS = [
    "tests/functional/",
    "docs/project_map.md",
    "project/core/logging/",
    "alembic/versions/",
]

READ_LAST_PATHS = [
    "CLAUDE.md",
    "docs/agent_rules.md",
    "docs/adr/",
]
