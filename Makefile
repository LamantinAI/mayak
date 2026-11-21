.PHONY: upgrade-check diff-coverage-report bootstrap smoke-local verify install-hooks test-db-down check-product print-generated-paths refresh-generated-unless-strict ruff-fix-unless-strict gate-fast checksum-generated gate-lockfile gate-lint gate-format gate-types gate-tests help logs logs-raw init-project refresh-agent-docs ai-autofix quality-gates quality-gates-steps doctor doctor-json test test-all test-e2e diff-coverage smoke run-local migrate autogenerate-migration format-trace update-deps audit-deps security-scan ci-local db-up-worktree db-down-worktree

# Auto-discover uv; override with UV=/path/to/uv if needed.
UV := $(shell command -v uv 2>/dev/null || echo /opt/homebrew/bin/uv)

PYTHON_SOURCES = project tests validation_support scripts

# What mypy checks: every source root, every test suite, and alembic. Two entries are not
# directories. tests/functional is a second import root (pytest runs it with its own rootdir) and
# the one suite no local gate executes — `make test` skips it and `make test-e2e` needs Docker;
# type errors there used to surface twenty minutes into an e2e run. tests/conftest.py is listed so
# that reaching it does not depend on an import existing: mypy does follow it in from
# tests/application, which imports it, but not from tests/infrastructure, which does not —
# measured both ways. A project that drops the tests/application imports would otherwise stop
# checking its own fixtures without noticing.
#
# Running a test does not check an annotation. `adapter: SomePort = _Fake()` is an assertion no
# interpreter evaluates and no Protocol enforces at runtime, so a fake whose signature had drifted
# from LLMPort kept a green test that proved nothing.
MYPY_TARGETS = project scripts validation_support alembic tests/functional tests/application tests/infrastructure tests/integration tests/db tests/support tests/conftest.py $(wildcard tests/template)

# Does this project use a relational store? Asked through the same reader the migration gate
# uses (scripts/validate_migrations.py::postgres_is_enabled), so there is one implementation of
# "what POSTGRES_ENABLED means" rather than a second .env parser living in shell.
POSTGRES_ENABLED_VALUE = $(UV) run python -c "from scripts.validate_migrations import postgres_is_enabled; print(str(postgres_is_enabled()).lower())"

# A fresh checkout ships .env with placeholder credentials, so the first `make run-local` used to
# end in a raw sqlalchemy.exc.OperationalError traceback — the least useful possible answer to
# "I followed the instructions and it did not start". This reuses the migration gate's own
# reachability probe so both agree on what "the database is up" means.
POSTGRES_REACHABLE_CHECK = $(UV) run python -c "import sys; from scripts.validate_migrations import _is_database_reachable; sys.exit(0 if _is_database_reachable() else 1)"

# Smoke always uses its own Compose namespace. Host ports are ephemeral because the probes
# run inside the app container and a dev database may already be running in this worktree.
SMOKE_PROJECT = $(WORKTREE_PROJECT)-smoke
SMOKE_APP_PORT ?= 0
SMOKE_POSTGRES_PORT ?= 0

# Docker allocates a loopback port on first start. Remember its actual port for subsequent
# processes. PostgresSettings reads the saved port unless explicit connection settings win.
WORKTREE_HASH := $(shell printf '%s' "$(CURDIR)" | cksum | cut -d' ' -f1)
WORKTREE_PROJECT := wt-$(WORKTREE_HASH)
WORKTREE_DB_FILE := .mayak-postgres-port
WORKTREE_POSTGRES_PORT ?= 0
WORKTREE_DB_COMPOSE := docker compose -p $(WORKTREE_PROJECT) -f docker-compose.yml -f docker-compose.postgres.yml

# Prints every target carrying a `## Group | description` annotation. The wrapper points here
# rather than repeating the list, so this and the contract cannot disagree.
help:
	@grep -hE '^[a-z0-9-]+:[^#]*##' $(MAKEFILE_LIST) \
		| sed -E 's/^([a-z0-9-]+):[^#]*## *([^|]+)\| *(.*)$$/\2\t\1\t\3/' \
		| sort -t'	' -k1,1 -s \
		| awk -F'\t' '{ if ($$1 != g) { g = $$1; printf "\n%s\n", g } printf "  make %-24s %s\n", $$2, $$3 }'

bootstrap: export MAYAK_PROJECT_NAME := $(value NAME)
bootstrap: export MAYAK_PROJECT_DOMAIN := $(value DOMAIN)
bootstrap: export MAYAK_PROJECT_DB := $(value DB)
bootstrap: ## Scaffolding | Initialize and check a new service (NAME, DOMAIN, DB=postgres or none)
	$(UV) run --no-project python -m scripts.bootstrap_project --uv "$(UV)"

install-hooks: ## Scaffolding | Opt in to the repository's Git hooks for all its worktrees
	git config --local core.hooksPath .githooks

upgrade-check: ## Scaffolding | Inspect provenance and known updates for this service
	$(UV) run --no-project python -m scripts.check_template_updates

init-project: ## Scaffolding | Install dependencies and create .env (idempotent: never overwrites one)
	./dev_setup.sh
	@# The identity decision cannot be guessed: a checkout named anything at all may be the template
	@# itself or a project built from it. Guessing wrong is worse than asking, so init states the
	@# open question instead of silently leaving the template's name in place forever.
	@if ! grep -q '"is_template": false' docs/project_context.json 2>/dev/null; then \
		echo ""; \
		echo "DECISION REQUIRED: docs/project_context.json still describes the Mayak template."; \
		echo "  If this repository IS the template, nothing to do."; \
		echo "  If it is a project built from it, set project_name and domain to this service's own,"; \
		echo "  then set \"is_template\": false. The project_context.template_identity_not_replaced"; \
		echo "  rule enforces the pair from that point on. The name lives in five more places —"; \
		echo "  .agents/skills/initialize-project/SKILL.md lists them."; \
		echo ""; \
	fi
	@# In a project, once the identity is replaced, the reference vertical moves out of the
	@# application into .agents/skills/add-vertical/scaffold/ — once; the script removes itself.
	@if [ -f scripts/extract_reference_vertical.py ]; then \
		$(UV) run python scripts/extract_reference_vertical.py $(EXTRACT_FLAGS); \
	fi
	@if [ "$(VERIFY)" = "1" ]; then \
		$(MAKE) quality-gates; \
	fi

refresh-agent-docs: ## Refresh generated artifacts | Regenerate CLAUDE.md and AGENTS.md from docs/agent_rules.md
	$(UV) run python scripts/sync_agent_docs.py

# Template only: builds a throwaway project from this checkout, takes the reference vertical out,
# counts the Python it inherits and runs its gates and e2e. A project has no script to run.
check-product: ## Validation | Make a project from this template and run its gates
	@if [ -f scripts/check_product_from_template.py ]; then \
		$(UV) run python scripts/check_product_from_template.py --gates --e2e --db postgres && \
		$(UV) run python scripts/check_product_from_template.py --gates --e2e --db none; \
	else \
		echo "check-product belongs to the template; this project was made from it already."; \
	fi

# Fix, then format — a removed import can leave a line the formatter still has to close. The
# formatter runs even when lint leaves something only a person can fix; that exit status is kept.
ai-autofix: ## Validation | Auto-format + fix lint
	@$(UV) run ruff check --fix $(PYTHON_SOURCES); status=$$?; \
	$(UV) run ruff format $(PYTHON_SOURCES) && exit $$status

# The five steps that run a tool rather than one of this repository's validators, each as its own
# target. scripts/doctor_ai_context.py runs these same targets when diagnosing a failed gate, so
# the flags and the source lists have exactly one definition — a doctor that reimplemented these
# checks in Python could silently drift from the Makefile and report "ok" while the suite is red;
# copying `ruff check $(PYTHON_SOURCES)` into Python would have made that two copies to keep in
# step instead of one.
gate-lockfile:
	@$(UV) lock --check

gate-lint:
	@$(UV) run ruff check $(PYTHON_SOURCES)

gate-format:
	@$(UV) run ruff format --check $(PYTHON_SOURCES)

gate-types:
	@$(UV) run mypy $(MYPY_TARGETS)

gate-tests:
	@$(UV) run python scripts/run_all_tests.py --skip-functional

# Before a local check: apply ruff's safe fixes to the changed Python and then format it — in that
# order, since a removed import can leave a line the formatter still has to close — and name
# every file that rewrote — before the checks, so the list is shown even when one fails. Changed
# files only — tracked ones that differ from HEAD, staged or not, and new ones git does not ignore:
# an untouched file rewritten here would land in a diff about something else, and `make ai-autofix`
# is the command for the whole tree. The list travels NUL-separated, never through $(shell), which
# joins lines with spaces: a path with a space in it was split into words and never fixed.
# STRICT_RUFF=1 — the pre-commit hook, ci-local and CI — skips it, and there a fixable finding is
# red. ADR-012.
ruff-fix-unless-strict:
	@if [ "$(STRICT_RUFF)" = "1" ]; then \
		exit 0; \
	fi; \
	list=$$(mktemp); before=$$(mktemp); after=$$(mktemp); \
	trap 'rm -f "$$list" "$$before" "$$after"' EXIT; \
	{ git diff -z --name-only HEAD -- $(PYTHON_SOURCES) 2>/dev/null; \
		git ls-files -z --others --exclude-standard -- $(PYTHON_SOURCES); } \
		| tr '\0' '\n' | grep '\.py$$' | sort -u \
		| while IFS= read -r file; do [ -f "$$file" ] && printf '%s\0' "$$file"; done > "$$list"; \
	if [ ! -s "$$list" ]; then \
		exit 0; \
	fi; \
	xargs -0 shasum < "$$list" > "$$before"; \
	xargs -0 $(UV) run ruff check --fix --no-unsafe-fixes --quiet < "$$list" >/dev/null 2>&1; \
	xargs -0 $(UV) run ruff format --quiet < "$$list"; \
	xargs -0 shasum < "$$list" > "$$after"; \
	changed=$$(diff "$$before" "$$after" | sed -n 's/^> [0-9a-f]*  //p'); \
	if [ -n "$$changed" ]; then \
		echo "--- ruff rewrote these files; read the change before committing ---"; \
		echo "$$changed" | sed 's/^/  /'; \
	fi

# While editing. The four checks that turned most full gates red, in seconds and without Docker;
# a green run finishes nothing — quality-gates, with the tests and the other validators, does.
gate-fast: ## Validation | Format, lint, types (tests included) and layers in seconds — run it while editing
	@$(MAKE) --no-print-directory ruff-fix-unless-strict
	@$(MAKE) --no-print-directory gate-format
	@$(MAKE) --no-print-directory gate-lint
	@$(MAKE) --no-print-directory gate-types
	$(UV) run python scripts/validate_architecture.py

# The one command that says a change is done. If the suite ever grows past ~30 s, the answer is
# test selection from coverage data, not a hand-maintained file→test map that goes stale silently.
quality-gates: ## Validation | The validation suite — run it before committing
	@$(MAKE) --no-print-directory refresh-generated-unless-strict
	@$(MAKE) --no-print-directory ruff-fix-unless-strict
	@$(UV) run python -m scripts.doctor_ai_context --run-quality-gates

# ATTRIBUTE: GENERATED_PATHS
# Every output of `refresh-agent-docs`, declared once. Two hooks read this list through
# `make print-generated-paths` rather than repeating it: `.githooks/pre-commit`, which stages what
# the refresh rewrote, and `.agents/hooks/pre-edit-guard.sh`, which refuses a write to any of them.
# The pre-commit hook's own copy had already drifted once, missing two entries, so a regenerated
# file went unstaged and the commit went through green with the output left behind.
GENERATED_PATHS = CLAUDE.md AGENTS.md

print-generated-paths:
	@echo "$(GENERATED_PATHS)"

# Regenerate the wrappers before checking and print what changed, unless STRICT_GENERATED=1 —
# ci-local and CI set it, and there a stale wrapper is a failure. The `--check` step in
# quality-gates-steps then passes, and the doctor still models it for the case where the
# generator genuinely breaks. Why a check whose fix is always one command became a fix: ADR-012.
refresh-generated-unless-strict:
	@if [ "$(STRICT_GENERATED)" = "1" ]; then \
		exit 0; \
	fi; \
	before=$$(mktemp); after=$$(mktemp); \
	$(MAKE) --no-print-directory checksum-generated > "$$before"; \
	$(MAKE) --no-print-directory refresh-agent-docs >/dev/null; \
	$(MAKE) --no-print-directory checksum-generated > "$$after"; \
	changed=$$(diff "$$before" "$$after" | grep '^>' | awk '{print $$3}'); \
	rm -f "$$before" "$$after"; \
	if [ -n "$$changed" ]; then \
		echo "--- generated artifacts were stale and have been refreshed ---"; \
		echo "$$changed" | sed 's/^/  /'; \
	fi

# Checksums before and after, not `git status`: comparing the working tree against HEAD is a
# different question — an artifact regenerated back to its committed content is invisible to git
# and would be refreshed in silence. What the reader needs to know is that the refresh CHANGED
# something, whichever direction it moved.
checksum-generated:
	@find $(GENERATED_PATHS) -type f 2>/dev/null | sort | xargs shasum 2>/dev/null || true

# The doctor is wired into the failure path above rather than living behind a second target name:
# a habit of reaching for the plain target regardless made a second name for "check my work" — one
# that differs only in whether you get the diagnosis — pointless to maintain. Recursion is not a
# concern: the doctor spawns
# the `gate-*` targets with MAYAK_DOCTOR_SUBPROCESS=1, and a doctor running inside that process
# skips its tool layers instead of shelling out again.
quality-gates-steps:
	@echo '[mayak-gate] gate-lockfile'
	@$(MAKE) --no-print-directory gate-lockfile
	@echo '[mayak-gate] gate-lint'
	@$(MAKE) --no-print-directory gate-lint
	@echo '[mayak-gate] gate-format'
	@$(MAKE) --no-print-directory gate-format
	@echo '[mayak-gate] gate-types'
	@$(MAKE) --no-print-directory gate-types
	@echo '[mayak-gate] validate_cbm'
	$(UV) run python scripts/validate_cbm.py
	@echo '[mayak-gate] validate_architecture'
	$(UV) run python scripts/validate_architecture.py
	@echo '[mayak-gate] validate_endpoint_wiring'
	$(UV) run python scripts/validate_endpoint_wiring.py
	@echo '[mayak-gate] validate_runtime_ownership'
	$(UV) run python scripts/validate_runtime_ownership.py
	@echo '[mayak-gate] validate_migrations'
	$(UV) run python scripts/validate_migrations.py
	@echo '[mayak-gate] validate_module_sizes'
	$(UV) run python scripts/validate_module_sizes.py
	@echo '[mayak-gate] validate_test_quality'
	$(UV) run python scripts/validate_test_quality.py
	@echo '[mayak-gate] validate_dependencies'
	$(UV) run python scripts/validate_dependencies.py
	@echo '[mayak-gate] validate_repository_metadata'
	$(UV) run python scripts/validate_repository_metadata.py
	@echo '[mayak-gate] validate_secrets'
	$(UV) run python scripts/validate_secrets.py
	@echo '[mayak-gate] security-scan'
	@$(MAKE) --no-print-directory security-scan
	@echo '[mayak-gate] sync_agent_docs'
	$(UV) run python scripts/sync_agent_docs.py --check
	@echo '[mayak-gate] gate-tests'
	@$(MAKE) --no-print-directory gate-tests

# No separate `quality-gates-no-regen` twin of the target above exists for the pre-commit hook to
# call, even though `refresh-agent-docs` has just run there and re-checking the wrappers is
# redundant: the `--check` costs a tenth of a second. A second copy of a twenty-step recipe, kept
# in step by hand, is not worth that — a hand-kept copy is exactly what drifts silently.

# Intentionally update pyproject.toml dependencies and refresh uv.lock.
# Default `quality-gates` enforces `uv lock --check` to detect accidental drift —
# this target is the documented escape hatch for deliberate updates.
update-deps:
	$(UV) lock

# Run the dependency CVE audit. Hard gate as of 2026-08-24: any pip-audit finding fails the
# build. It was informational from introduction until then, because legacy pinned versions of
# langchain/fastapi/etc. carried known CVEs that needed per-CVE triage before they could fail a
# build outright. That list is empty now — verified with `make audit-deps`, which prints
# "No known vulnerabilities found" and exits 0 — and this target's own note above said to promote
# it the day the list emptied, so this is that promotion, not a change of policy.
# If a new CVE lands that cannot be fixed the same day, the exception belongs here, pinned with a
# date and a link to the advisory and the tracking issue — never by lowering this back to
# informational, which is how a real CVE and an unreachable tool look identical again.
#
# `--with pip-audit` matters: pip-audit is declared nowhere as a project dependency, so calling it
# bare (`$(UV) run pip-audit`) never starts it, and a trailing `|| true` would swallow the spawn
# error — leaving both this target and the CI job that calls it green while auditing nothing. Any
# non-zero exit is fatal for the same reason: pip-audit exits 1 when it finds a CVE and above 1
# when the tool itself failed to run, and a build that tolerates either shape of failure is a
# build that cannot tell "vulnerable" from "did not check."
#
# The requirements file goes to a `mktemp` path, not a fixed /tmp name. The fixed name carried
# the template's own name into every project built from it, and two checkouts auditing at the
# same time wrote over each other's export. A failed export stops the audit: pip-audit reading
# what was left of the file would judge a list nobody meant to hand it.
#
# Both overlay tools are pinned, because `--with` re-resolves on every run: unpinned, a new release
# changes what the gate reports on a day no commit changed anything. Raise a pin on purpose.
PIP_AUDIT := pip-audit==2.10.1
DIFF_COVER := diff-cover==10.6.0
audit-deps: ## Validation | Audit the locked dependencies for known CVEs (pip-audit)
	@set +e; \
	requirements=$$(mktemp); \
	trap 'rm -f "$$requirements"' EXIT; \
	if ! $(UV) export --no-emit-project --frozen > "$$requirements"; then \
		echo "audit-deps: uv export failed, so nothing was audited"; \
		exit 1; \
	fi; \
	$(UV) run --with $(PIP_AUDIT) pip-audit --disable-pip --requirement "$$requirements"; \
	audit_status=$$?; \
	if [ $$audit_status -ne 0 ]; then \
		echo "audit-deps: pip-audit exited $$audit_status"; \
		exit $$audit_status; \
	fi

# bandit lives here rather than only in a CI job, so the security scan is runnable by the person
# who can act on it. It is a pinned dev dependency, not `--with bandit`: the gate runs this, and an
# overlay dependency re-resolves against the network every time. The `@` matters too — the doctor
# reads this target's output and reports its first meaningful line, and an echoed command line is
# what it would report instead of the finding. `-ll` reports medium severity and above.
# Keep SQL statements as literals with bound parameters, as in the reference repository.
security-scan: ## Validation | bandit security scan over project/
	@$(UV) run bandit -q -r project -ll

# Everything the pipeline runs, in one local command, cheapest lane first so it fails fast.
# It exists because the pipeline stopped being reachable and "green" has to keep meaning
# something. Each lane below corresponds to a job in .github/workflows/ci.yml; adding a job
# there without adding it here puts the two back out of step, which is how a gate starts lying.
#
# There is no security lane either: `quality-gates` runs bandit itself, so a separate invocation
# here would buy nothing. `make security-scan` remains as a target for anyone who wants only that
# check.
#
# There is no minimum-Python lane. requires-python and .python-version now name the same version,
# so such a lane could only re-run the suite on the interpreter lane 1 already used — a gate that
# cannot fail. Reinstate it the day the two numbers diverge again; ADR-002 says how.
#
# Lane 2 runs only `gate-tests` and the migration validator under POSTGRES_ENABLED=false, mirroring
# the `no-postgres-path` CI job rather than the whole gate a second time: nothing else under that
# flag reads it — the test run pins POSTGRES_ENABLED=true for itself either way
# (tests/conftest.py's pin_postgres_toggle), and validate_migrations.py treats the flag as "no
# relational store, nothing to verify" and never opens a connection regardless. Re-running every
# DB-independent step — lint, mypy, architecture, bandit, and the rest — a second time would
# check nothing that flag actually changes.
#
# STRICT_GENERATED=1 stays on lane 1 only: that command answers "would the pipeline pass?", and
# there a stale generated artifact is a failure, not something to fix on the fly. Plain
# `make quality-gates` refreshes instead — by the time you reach ci-local the artifacts are
# already fresh, so this lane only fires for someone who skipped the gate entirely. Lane 2 sets
# nothing of the sort: `gate-tests` never touches a generated artifact, so there is nothing to guard.
ci-local: ## Validation | Everything CI runs, locally: the full gate, the no-Postgres path, audit, e2e, a project from the template
	@echo "===> 1/6 quality-gates"
	@STRICT_GENERATED=1 STRICT_RUFF=1 $(MAKE) --no-print-directory quality-gates
	@echo "===> 2/6 tests and migrations without PostgreSQL"
	@POSTGRES_ENABLED=false $(MAKE) --no-print-directory gate-tests && POSTGRES_ENABLED=false $(UV) run python scripts/validate_migrations.py && POSTGRES_ENABLED=false $(MAKE) --no-print-directory test-e2e
	@echo "===> 3/6 dependency audit"
	@$(MAKE) --no-print-directory audit-deps
	@echo "===> 4/6 diff coverage"
	@$(MAKE) --no-print-directory diff-coverage
	@echo "===> 5/6 functional tests"
	@$(MAKE) --no-print-directory test-e2e
	@echo "===> 6/6 a project made from the template"
	@$(MAKE) --no-print-directory check-product
	@echo "===> ci-local: all lanes passed"

# MAYAK_DOCTOR_SUBPROCESS is cleared, not merely unset-by-default. The doctor sets it on the
# children it spawns so "the doctor runs the test suite, and the suite exercises the doctor"
# terminates — but a shell that exports it for any other reason would silently disable five layers
# and get a bare "ok" back in a second. This is the entry point a human uses; it starts clean.
doctor: ## Validation | Diagnose a red gate: each failed layer with its rule ID and playbook
	@MAYAK_DOCTOR_SUBPROCESS= $(UV) run python scripts/doctor_ai_context.py

doctor-json:
	@MAYAK_DOCTOR_SUBPROCESS= $(UV) run python scripts/doctor_ai_context.py --json

test: ## Testing | Unit, integration and the db tier (starts its own PostgreSQL; skips functional)
	$(UV) run python scripts/run_all_tests.py --skip-functional

test-all: ## Testing | All tests including functional
	$(UV) run python scripts/run_all_tests.py

# The db tier's PostgreSQL stays up between runs, so the next `make test` skips its start; this is
# how to take it down. Its data lives in tmpfs, so nothing is lost that a test needs.
test-db-down: ## Testing | Stop this checkout's db-tier PostgreSQL
	@$(UV) run python tests/db/stack.py down

test-e2e: ## Testing | Functional tests only (Docker)
	$(UV) run python scripts/run_all_tests.py --functional-only

# The standalone job produces a fresh report. Verify reuses the exact report its full gate
# just produced. diff-cover includes untracked files without staging them.
DIFF_COMPARE_BRANCH ?=
export DIFF_COMPARE_BRANCH

diff-coverage: ## Validation | Test and check coverage of the complete application diff
	@$(MAKE) --no-print-directory gate-tests
	@$(MAKE) --no-print-directory diff-coverage-report

VERIFY_DIFF_ARGS = --base "$$DIFF_COMPARE_BRANCH" --uv "$(UV)" --diff-cover "$(DIFF_COVER)"

diff-coverage-report:
	$(UV) run python -m scripts.verify_change $(VERIFY_DIFF_ARGS) --coverage-only

verify: ## Validation | Strict gates, complete diff coverage and applicable Docker E2E
	$(UV) run python -m scripts.verify_change $(VERIFY_DIFF_ARGS) $(if $(filter 1,$(E2E)),--e2e)

# Guarantees teardown even if a health probe fails, and fails fast with a clear
# message if .env is missing (docker compose's own "env file not found" error is
# noisy and appears after unrelated POSTGRES_* warnings). Probes go through
# `docker compose exec` (not host curl) because this is more portable — it works
# whether or not APP_PORT's host mapping is reachable from the calling shell.
smoke: ## Run | Docker-based health check
	@if [ ! -f .env ]; then \
		echo ".env not found — run 'make init-project' first"; \
		exit 2; \
	fi
	set -eu; \
	files="-f docker-compose.yml"; \
	enabled="$$($(POSTGRES_ENABLED_VALUE))"; \
	if [ "$$enabled" = true ]; then files="$$files -f docker-compose.postgres.yml"; fi; \
	export APP_PORT="$(SMOKE_APP_PORT)" APP_BIND_HOST=127.0.0.1; \
	export POSTGRES_PORT="$(SMOKE_POSTGRES_PORT)" POSTGRES_BIND_HOST=127.0.0.1; \
	compose="docker compose -p $(SMOKE_PROJECT) $$files"; \
	trap 'ec=$$?; $$compose down -v || true; exit $$ec' EXIT; \
	$$compose up -d --build --wait; \
	$$compose exec -T app sh -c 'curl -fsS "http://localhost:$${SERVER_PORT:-8000}/health/"'; \
	$$compose exec -T app sh -c 'curl -fsS "http://localhost:$${SERVER_PORT:-8000}/health/ready"'

# The guard and Compose commands must share a shell. A recipe line exiting zero does not
# stop make from executing the next line.
db-up-worktree: ## Run | Start this worktree's PostgreSQL and remember its loopback port
	@set -eu; \
	enabled="$$($(POSTGRES_ENABLED_VALUE))"; \
	if [ "$$enabled" = false ]; then \
		echo "POSTGRES_ENABLED=false — this project declares no relational store, nothing to start."; \
		exit 0; \
	fi; \
	if [ ! -f .env ]; then \
		echo ".env not found — run 'make init-project' first"; exit 2; \
	fi; \
	export POSTGRES_PORT="$(WORKTREE_POSTGRES_PORT)" POSTGRES_BIND_HOST=127.0.0.1; \
	$(WORKTREE_DB_COMPOSE) up -d --wait db; \
	address="$$($(WORKTREE_DB_COMPOSE) port db 5432)"; \
	port="$${address##*:}"; \
	case "$$port" in ''|*[!0-9]*) echo "Cannot read the worktree database port: $$address" >&2; exit 1 ;; esac; \
	tmp="$$(mktemp $(WORKTREE_DB_FILE).XXXXXX)"; trap 'rm -f "$$tmp"' EXIT; \
	printf '%s\n' "$$port" > "$$tmp"; mv "$$tmp" "$(WORKTREE_DB_FILE)"; \
	echo "Worktree database at 127.0.0.1:$$port"

db-down-worktree: ## Run | Remove this worktree's own database, network and volume
	$(WORKTREE_DB_COMPOSE) down -v
	rm -f "$(WORKTREE_DB_FILE)"

migrate: ## Run | Apply pending Alembic migrations
	@set -e; enabled="$$($(POSTGRES_ENABLED_VALUE))"; \
	if [ "$$enabled" = true ]; then \
		if $(POSTGRES_REACHABLE_CHECK); then \
			$(UV) run alembic upgrade head; \
		else \
			echo "PostgreSQL is not reachable with the credentials in .env."; \
			echo "  start one for this checkout alone:  make db-up-worktree"; \
			echo "  or declare this project needs none:  POSTGRES_ENABLED=false in .env"; \
			exit 1; \
		fi; \
	else \
		echo "POSTGRES_ENABLED=false — no relational store, nothing to migrate."; \
	fi

# The Docker image migrates in entrypoint.sh. `run-local` depends on `migrate` above so a fresh
# checkout cannot serve a green /health/ready against a database with no tables. Same order both
# ways.
run-local: migrate ## Run | Migrate, then start the app locally (needs a reachable PostgreSQL unless POSTGRES_ENABLED=false)
	SERVER_HOST=127.0.0.1 $(UV) run python -m project.launcher.main

smoke-local: migrate ## Run | Check a short-lived loopback application over HTTP
	$(UV) run python -m scripts.smoke_local

format-trace: ## Reading a running service | Render a local NDJSON file: ARGS="<logfile>"
	$(UV) run python -c "from project.core.logging.trace_formatter import _cli; _cli()" $(ARGS)

# Pull a running container's semantic log and render it as a trace tree.
# LINES=N limits how far back to read (default 2000). ARGS is passed to the formatter,
# e.g. make logs ARGS="--trace a1b2c3d4".
# By default only the LAST HTTP trace is rendered; a failure in an earlier request would not
# appear. The footer says how many traces were hidden and how many of them failed — use
# ARGS="--all" to render every trace in the window.
# --no-log-prefix matters: without it compose prepends the service name to every line and the
# NDJSON parser skips all of them.
# The compose output lands in a temporary file, not in a pipe. A pipe's exit status is the
# formatter's, so a broken compose file or a stopped stack printed "(no events found)" and
# exited 0 — the same answer as a service that logged nothing. Measured with
# COMPOSE_FILE=nonexistent.yml. `set -o pipefail` would be the one-line fix, and the /bin/sh
# that make uses is dash on Debian and Ubuntu, where that is an illegal option.
# Which Compose project the two recipes below read. Three projects appear in this Makefile and
# they are not interchangeable: `$(SMOKE_PROJECT)` (ephemeral, torn down by `make smoke` itself),
# `$(WORKTREE_PROJECT)` (what `db-up-worktree` starts the database under), and Compose's own
# directory-derived default (what a bare `docker compose up -d` from README uses).
# The default here is the worktree's, because that is where the app actually runs once a checkout
# has a worktree database: measured in the field, two agents each built a project on this
# template and both ended up with `wt-<checkout>-<digest>-app-1`, having extended
# `db-up-worktree`'s own `-p` to the whole stack. Without any `-p`, `docker compose logs` falls
# back to the directory-derived name — the exact collision `db-up-worktree`'s comment documents
# for `up`: two worktrees agree on one name, so this read whichever project owned it, another
# checkout's container or nothing at all, silently.
# By default select the same Compose project as a plain up. Use LOGS_PROJECT for a custom -p.
LOGS_PROJECT ?=
LOGS_COMPOSE = docker compose $(if $(LOGS_PROJECT),-p "$(LOGS_PROJECT)")
define require-log-container
container="$$($(LOGS_COMPOSE) ps -aq app)" || exit $$?; \
if [ -z "$$container" ]; then \
	echo "No app container for Compose project $(or $(LOGS_PROJECT),in this directory). Set LOGS_PROJECT for a custom project." >&2; \
	exit 1; \
fi
endef

logs: ## Reading a running service | Render the container's semantic log as a trace tree
	@$(require-log-container); \
	tmp="$$(mktemp)"; trap 'rm -f "$$tmp"' EXIT; \
	$(LOGS_COMPOSE) logs --no-color --no-log-prefix --tail $(or $(LINES),2000) app > "$$tmp" && \
	$(UV) run python -c "from project.core.logging.trace_formatter import _cli; _cli()" "$$tmp" $(ARGS)

# Where logs-raw writes. `?=` so an environment variable overrides it — make imports the
# environment before reading the makefile, and a conditional assignment leaves an already-set
# variable alone. The template's tests of the log targets use exactly that: pointing LOGS_DIR at
# a tmp_path keeps those tests from reading this checkout's own
# logs/, which whatever container is actually running writes into concurrently — a before/after
# glob compared against the shared directory went red whenever that happened mid-test, for a
# reason that had nothing to do with the recipe under test.
LOGS_DIR ?= logs

# Same source, unrendered, for grepping. Writes to $(LOGS_DIR)/container-<timestamp>.ndjson.
# A failed compose call removes the file it was writing and exits non-zero — otherwise the recipe
# would print the path of an empty file and exit 0, claiming success.
logs-raw: ## Reading a running service | Dump the container's raw NDJSON under $(LOGS_DIR)
	@mkdir -p $(LOGS_DIR)
	@$(require-log-container); \
	out="$(LOGS_DIR)/container-$$(date -u +%Y-%m-%dT%H-%M-%S).ndjson"; \
	$(LOGS_COMPOSE) logs --no-color --no-log-prefix --tail $(or $(LINES),2000) app > "$$out" \
		|| { rm -f "$$out"; exit 1; }; \
	echo "$$out"

autogenerate-migration: ## Scaffolding | Alembic migration: MSG="description"
	@test -n "$(MSG)" || (echo "MSG is required, e.g. make autogenerate-migration MSG=create_customer_table" && exit 1)
	$(UV) run alembic revision --autogenerate -m "$(MSG)"
