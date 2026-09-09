# Shared Agent Wrapper Source

This file is the shared source generating `CLAUDE.md` and `AGENTS.md` — one operational contract
under the two names the agents look for. Claude Code reads the first, Codex the second, neither
the other's; there is no second document to read first.

Start here:
- Copy the `reference_task` vertical. It is the one worked example and it exists to be copied — eleven files plus three wiring edits; `.agents/skills/add-vertical` carries the order and the removal list for when your own vertical replaces it. A project that has replaced it edits this line and nothing else: the wrapper's Quick Start is generated from these bullets.
- If `.env` is missing: `make init-project`. Nothing else creates it, and the app does not start without it. Idempotent — it never overwrites an existing `.env`.
- `make quality-gates` before committing. When the diff touched persistence, endpoints, wiring or a migration, `make test-e2e` too — the gates run none of your queries.
- Reach for `uv run python scripts/query_ai_context.py bootstrap` when you need the wiring map, and `workset diff` when you already have local edits — not as a ritual.

Source of truth order:
1. Runnable validators and `make quality-gates`
2. `docs/architecture_rules.json`
3. `scripts/query_ai_context.py`
4. `docs/ai_context_map.json` and `docs/ai_change_map.json`
5. `docs/agent_rules.md`, `docs/project_map.md`, ADRs

Task process:
- When one command fails three times running, stop editing: write down the assumption you now doubt, ask `query_ai_context.py failure rule <rule_id>` for its playbook, read only the files it names, and change one thing before rerunning.
- Never claim task completion without fresh `make quality-gates` evidence.
- Use a fresh subagent for independent verification — do not self-verify.
- A finished task ends as an open pull request, never a merge: branch `task/<ID>` → commit → push → `gh pr create --base main`. No deploy job ships — "done" means open and green.
- A fixed defect is proved by one test in the same pull request, failing on the defective code for the stated reason and passing once fixed — not by a test, an ADR, and a wrapper bullet all at once. A new blocking validator rule ships with one failing example and several correct ones it must pass — earned by a real defect, not symmetry.

Working notes:
- Use focused queries before broad scans — `workset`, `before-edit`, `failure`, `symbol` — instead of a repo-wide grep.
- `PROJECT.md` and `docs/project_context.json` carry the business domain; this file covers only the kernel.
- If `.env` is missing, run `make init-project` (idempotent) — nothing else creates it. `make run-local`/`make migrate` need reachable PostgreSQL unless `POSTGRES_ENABLED=false`; bring one up with `docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d db`.
- `make quality-gates` runs `make doctor` on failure and refreshes generated artifacts first, so a stale map is fixed, not red; `make ci-local` adds `STRICT_GENERATED=1` and fails on one instead. A narrow `pytest` run does not fail on total coverage — that floor lives in `scripts/run_all_tests.py`'s full run, not `pytest.ini`.
- Gates run none of your queries — a SQL defect is caught only if a test pins its clause as literal text, and a migration is verified only against a real database. See ADR-010.
- A write to a generated path is refused by `.agents/hooks/pre-edit-guard.sh`, naming the file — the list is `make print-generated-paths`, shared with the pre-commit hook and both agents.
- Finish with `make quality-gates`, and with `make test-e2e` as well when the diff touched persistence, endpoints, wiring, or a migration — a migration is the one change the gates cannot check at all without a database.
- Read a trace with `make format-trace ARGS="<logfile>"` or `make logs` (a running container); see `docs/tracing.md` for span names, outcome filtering, and two silent-failure traps.
- Repeatable workflows belong in a versioned skill under `.agents/skills/`, not in this file.
- `before-edit file <path>` returns the FILE_POLICY entry plus `derived: bool`. An unindexed path needs an explicit entry in `ai_context/file_policy.py`; add one, then `make refresh-ai-context`.

How the kernel is shaped:
- Dependency direction is `domain -> application -> infrastructure`, enforced by `scripts/validate_architecture.py`: the domain is an allowlist (stdlib plus `project.domain`); application and infrastructure are blacklists. `docs/architecture_rules.json` gives each rule one of three levels — `runtime_enforced`, `guidance_only`, `not_enforced_in_validator` (unchecked outside the domain) — check which.
- Four files carry every wiring edit: `project/core/composition_root.py`, `project/core/service_registration.py`, `project/infrastructure/api/router_registration.py`, `project/infrastructure/api/dependencies.py`.
- The kernel ships exactly one vertical, `reference_task`, meant to be copied — a second worked example would be a duplicate or a guess about your domain.
- The kernel ships no business pipeline. LLM access is `project/infrastructure/agents/llm_service.py`; `bind_tools` returns a bound copy — keep the return value. Mock mode replays every bound tool once before the summary — ADR-003. A provider failure becomes a domain error at the adapter boundary — ADR-009. Prompts live in `project/prompts/`; the kernel only checks at startup that the directory and the named file exist — loading them is the vertical's job.
- Heavy async resources open in the FastAPI lifespan and close via `cleanup_services()`; `scripts/validate_runtime_ownership.py` guards resource ownership, `app.state.services` writes, and env access.
- `/health/ready` reports every dependency but only some decide the verdict: services always, the database when used (ADR-006), LLM readiness only when `AGENT_LLM_READINESS_CRITICAL=true` (ADR-008, off by default).
- The Python version policy is ADR-002, its numbers in `pyproject.toml` and `.python-version`. Concurrency across rows and a foreign key's deletion policy are ADR-007.

Where a fact goes — one fact, one place:
- Ask when the fact will be needed and write it there once — two copies drift and nothing notices.
- Needed while editing this file → a code comment, in full: what and why — the channel that measurably reaches an agent, more than any generated map.
- Needed while editing several files under one convention → `docs/adr/`, one dated decision per document, not repeated in every file it governs.
- When a decision rests on dated, bulky evidence, keep the conclusion and the number, and say how to reproduce the measurement rather than pasting the table.
- Documentation costs nothing against the size budget — `scripts/validate_module_sizes.py` charges only executable lines. `# INPUT:`/`# OUTPUT:` earn their line only when they state something the signature cannot — ADR-001.
- The template ships no external memory integration; a project that wants one keeps it in that project, not the kernel.

Validator authoring conventions:
- A `validate_*.py` shipping `--json` exposes `def main(argv: Sequence[str] | None = None) -> int`, parsed with `args = parser.parse_args([] if argv is None else argv)` so pytest's argv cannot leak in; its test calls `main()` directly. Otherwise, a bare `def main() -> int` with `parser.parse_args()` is footgun-immune too.
- Each new validator must: define stable `RULE_ID` constants; give its Issue dataclass a `severity` field (`"info"` for non-blocking); provide `get_<X>_rule_playbook(rule_id)`; join `failure_playbook` in `ai_query/common.py`.
