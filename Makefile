.PHONY: print-generated-paths refresh-generated-unless-strict checksum-generated gate-lockfile gate-lint gate-format gate-types gate-tests help logs logs-raw init-project refresh-ai-context refresh-agent-docs refresh-project-map refresh-generated-docs ai-autofix quality-gates quality-gates-steps doctor doctor-json test test-all test-e2e diff-coverage smoke run-local migrate autogenerate-migration format-trace update-deps audit-deps security-scan ci-local

# Auto-discover uv; override with UV=/path/to/uv if needed.
UV := $(shell command -v uv 2>/dev/null || echo /opt/homebrew/bin/uv)

PYTHON_SOURCES = project tests ai_context ai_query scripts

# What mypy checks: every source root, every test suite, and alembic. Two entries are not
# directories. tests/functional is a second import root (pytest runs it with its own rootdir) and
# the one suite no local gate executes — `make test` skips it and `make test-e2e` needs Docker;
# type errors there used to surface twenty minutes into an e2e run. tests/conftest.py is listed so
# that reaching it does not depend on an import existing: mypy does follow it in from
# tests/application, which imports it, but not from tests/infrastructure, which does not — measured
# both ways on 2026-08-18. A project that drops the tests/application imports would otherwise stop
# checking its own fixtures without noticing.
#
# The unit suites were outside this list until 2026-08-18, on the theory that running a test checks
# it. Running does not check an annotation. `adapter: SomePort = _Fake()` is an assertion no
# interpreter evaluates and no Protocol enforces at runtime, so a fake whose signature had drifted
# from LLMPort kept a green test that proved nothing. Adding the suites cost 240 one-time fixes.
MYPY_TARGETS = project scripts ai_context ai_query alembic tests/functional tests/application tests/infrastructure tests/integration tests/conftest.py

# Does this project use a relational store? Asked through the same reader the migration gate
# uses (scripts/validate_migrations.py::postgres_is_enabled), so there is one implementation of
# "what POSTGRES_ENABLED means" rather than a second .env parser living in shell.
POSTGRES_ENABLED_CHECK = $(UV) run python -c "import sys; from scripts.validate_migrations import postgres_is_enabled; sys.exit(0 if postgres_is_enabled() else 1)"

# A fresh checkout ships .env with placeholder credentials, so the first `make run-local` used to
# end in a raw sqlalchemy.exc.OperationalError traceback — the least useful possible answer to
# "I followed the instructions and it did not start". This reuses the migration gate's own
# reachability probe so both agree on what "the database is up" means.
POSTGRES_REACHABLE_CHECK = $(UV) run python -c "import sys; from scripts.validate_migrations import _is_database_reachable; sys.exit(0 if _is_database_reachable() else 1)"

# Smoke runs in its own Compose project so its `down -v` cannot delete the volumes of a stack
# started by hand in this same directory. Compose derives the project name from the directory,
# which meant `make smoke` and a plain `docker compose up` shared postgres_data and app_logs.
# Lowercased because Compose rejects uppercase in a project name, and a repository directory
# may be capitalised (Mayak).
SMOKE_PROJECT = $(shell echo $(notdir $(CURDIR)) | tr '[:upper:]' '[:lower:]')-smoke

# Smoke publishes on its own host ports. The stack is ephemeral and probed from inside the
# container, so the mappings exist only to avoid colliding with whatever the developer already
# runs: a locally running PostgreSQL on 5432 made `make smoke` fail with "port is already
# allocated" — a failure that says nothing about the service under test. Override on the
# command line if these are taken too.
SMOKE_APP_PORT ?= 18000
SMOKE_POSTGRES_PORT ?= 15432

# Prints every target carrying a `## Group | description` annotation. The same annotations are the
# only source of the command list in CLAUDE.md, so this and the wrapper can never disagree.
help:
	@grep -hE '^[a-z0-9-]+:[^#]*##' $(MAKEFILE_LIST) \
		| sed -E 's/^([a-z0-9-]+):[^#]*## *([^|]+)\| *(.*)$$/\2\t\1\t\3/' \
		| sort -t'	' -k1,1 -s \
		| awk -F'\t' '{ if ($$1 != g) { g = $$1; printf "\n%s\n", g } printf "  make %-24s %s\n", $$2, $$3 }'

init-project:
	./dev_setup.sh
	$(UV) run python scripts/query_ai_context.py bootstrap
	$(UV) run python scripts/query_ai_context.py overview
	@if [ -n "$$(git status --short)" ]; then \
		$(UV) run python scripts/query_ai_context.py workset diff; \
	fi
	@# The identity decision cannot be guessed: a checkout named anything at all may be the template
	@# itself or a project built from it. Guessing wrong is worse than asking, so init states the
	@# open question instead of silently leaving the template's name in place forever.
	@if ! grep -q '"is_template": false' docs/project_context.json 2>/dev/null; then \
		echo ""; \
		echo "DECISION REQUIRED: docs/project_context.json still describes the Mayak template."; \
		echo "  If this repository IS the template, nothing to do."; \
		echo "  If it is a project built from it, set project_name and domain to this service's own,"; \
		echo "  then set \"is_template\": false. The project_context.template_identity_not_replaced"; \
		echo "  rule enforces the pair from that point on."; \
		echo ""; \
	fi
	@if [ "$(VERIFY)" = "1" ]; then \
		$(MAKE) quality-gates; \
	fi

refresh-ai-context: ## Refresh generated artifacts | Regenerate AI context maps
	$(UV) run python scripts/generate_ai_context.py

refresh-agent-docs: ## Refresh generated artifacts | Regenerate CLAUDE.md and AGENTS.md
	$(UV) run python scripts/sync_agent_docs.py

refresh-project-map: ## Refresh generated artifacts | Regenerate docs/project_map.md
	$(UV) run python scripts/structure_builder.py

# refresh-project-map runs LAST on purpose: it renders the file tree, and the steps before it
# write files. With the map first, a new generated file left `make refresh-generated-docs`
# immediately followed by a red `structure_builder --check`.
refresh-generated-docs: refresh-ai-context refresh-agent-docs refresh-project-map ## Refresh generated artifacts | All of the above, in dependency order

ai-autofix: ## Validation | Auto-format + fix lint + CBM
	$(UV) run ruff format $(PYTHON_SOURCES)
	$(UV) run ruff check --fix $(PYTHON_SOURCES)
	$(UV) run python scripts/validate_cbm.py --fix

# The five steps that run a tool rather than one of this repository's validators, each as its own
# target. scripts/doctor_layers.py runs these same targets when diagnosing a failed gate, so the
# flags and the source lists have exactly one definition. The doctor used to model 14 of the 23
# quality-gates steps and answered "doctor status: ok" while the suite was red on one of the nine
# it did not model — copying `ruff check $(PYTHON_SOURCES)` into Python would have made that two
# copies to keep in step instead of one.
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

# The one validation command. There used to be three — `ai-workset-check` for the current diff,
# `ai-fast-check` in the middle, and this one — and the ladder was measured on the fourth template
# run: ai-fast-check took 15.0 s against this target's 14.8 s, so the middle rung was slower than
# the thing it was a cheap alternative to. Everything here except the test suite costs 3.0 s; the
# suite is the other 11.8. (The total read 14.2 until 2026-08-14, against parts that add to 14.8 —
# the two halves were measured and the total was typed.) The narrow loop did buy ~12 s, and paid for them by never running mypy
# and by resolving no tests at all for 22 of the 50 files under project/ — a green "workset checks
# passed" on a diff that fails this target with 5 type errors and 24 broken tests.
#
# If the suite ever grows past ~30 s, the answer is test selection from coverage data, not a
# hand-maintained file→test map that goes stale silently.
quality-gates: ## Validation | The validation suite — run it before committing
	@$(MAKE) --no-print-directory refresh-generated-unless-strict
	@$(MAKE) --no-print-directory quality-gates-steps \
		|| ($(MAKE) --no-print-directory doctor; exit 1)

# ATTRIBUTE: GENERATED_PATHS
# Every output of `refresh-generated-docs`, declared once. Two hooks read this list through
# `make print-generated-paths` rather than repeating it: `.githooks/pre-commit`, which stages what
# the refresh rewrote, and `.agents/hooks/pre-edit-guard.sh`, which refuses a write to any of them.
# The pre-commit hook's own copy had already drifted once, missing two entries, so a regenerated
# file went unstaged and the commit went through green with the output left behind.
GENERATED_PATHS = docs/ai_context_map.json docs/ai_change_map.json docs/architecture_rules.json \
	CLAUDE.md AGENTS.md docs/project_map.md

print-generated-paths:
	@echo "$(GENERATED_PATHS)"

# Regenerate before checking, unless something asked for the strict behaviour.
#
# Measured: the ONLY failure both arms of an A/B measurement hit was
# `drift.generated.outdated` — a generated map left behind by an edit, fixed by the same command
# every time. Twice out of two, plus three more times in one session. A step whose fix is
# always the identical command is a ritual, not a check: it costs a full red gate and a rerun to
# tell you something the machine could have done.
#
# So locally the artifacts are refreshed and what changed is printed; the five `--check` steps in
# quality-gates-steps then pass, and the doctor still models each of them for the case where a
# generator genuinely breaks. `STRICT_GENERATED=1` restores check-only behaviour, and `ci-local`
# and the pipeline both set it: stale artifacts must never reach a commit, and there the answer
# has to be a failure rather than a fix.
refresh-generated-unless-strict:
	@if [ "$(STRICT_GENERATED)" = "1" ]; then \
		exit 0; \
	fi; \
	before=$$(mktemp); after=$$(mktemp); \
	$(MAKE) --no-print-directory checksum-generated > "$$before"; \
	$(MAKE) --no-print-directory refresh-generated-docs >/dev/null; \
	$(MAKE) --no-print-directory checksum-generated > "$$after"; \
	changed=$$(diff "$$before" "$$after" | grep '^>' | awk '{print $$3}'); \
	rm -f "$$before" "$$after"; \
	if [ -n "$$changed" ]; then \
		echo "--- generated artifacts were stale and have been refreshed ---"; \
		echo "$$changed" | sed 's/^/  /'; \
	fi

# Checksums before and after, not `git status`. The first version compared the working tree against
# HEAD, which is a different question: an artifact regenerated back to its committed content is
# invisible to git and would have been refreshed in silence. What the reader needs to know is that
# the refresh CHANGED something, whichever direction it moved.
checksum-generated:
	@find $(GENERATED_PATHS) -type f 2>/dev/null | sort | xargs shasum 2>/dev/null || true

# The doctor is wired into the failure path above rather than living behind a second target name.
# `quality-gates-with-doctor` existed for that, CLAUDE.md told everyone to prefer it, and the
# plain target stayed as the one you would reach for by habit — two names for "check my work",
# differing only in whether you get the diagnosis. Recursion is not a concern: the doctor spawns
# the `gate-*` targets with MAYAK_DOCTOR_SUBPROCESS=1, and a doctor running inside that process
# skips its tool layers instead of shelling out again.
quality-gates-steps:
	@$(MAKE) --no-print-directory gate-lockfile
	@$(MAKE) --no-print-directory gate-lint
	@$(MAKE) --no-print-directory gate-format
	@$(MAKE) --no-print-directory gate-types
	$(UV) run python scripts/validate_cbm.py
	$(UV) run python scripts/validate_architecture.py
	$(UV) run python scripts/validate_endpoint_wiring.py
	$(UV) run python scripts/validate_runtime_ownership.py
	$(UV) run python scripts/validate_migrations.py
	$(UV) run python scripts/validate_module_sizes.py
	$(UV) run python scripts/validate_test_quality.py
	$(UV) run python scripts/validate_dependencies.py
	$(UV) run python scripts/validate_skills_frontmatter.py
	$(UV) run python scripts/validate_project_context.py
	$(UV) run python scripts/validate_file_policy.py
	$(UV) run python scripts/validate_script_paths.py
	$(UV) run python scripts/validate_secrets.py
	$(UV) run python scripts/structure_builder.py --check
	$(UV) run python scripts/generate_ai_context.py --check
	$(UV) run python scripts/sync_agent_docs.py --check
	@$(MAKE) --no-print-directory gate-tests

# The pre-commit hook used to call a `quality-gates-no-regen` twin of the target above, on the
# grounds that `refresh-generated-docs` had just run and re-checking the generators was wasted
# work. Measured: those five `--check` invocations cost 0.4 s together. A second copy of a
# twenty-step recipe, kept in step by hand, is not worth four tenths of a second — and the copy
# had already drifted, missing the four generator checks the original gained.

# Intentionally update pyproject.toml dependencies and refresh uv.lock.
# Default `quality-gates` enforces `uv lock --check` to detect accidental drift —
# this target is the documented escape hatch for deliberate updates.
update-deps:
	$(UV) lock

# Run the dependency CVE audit. Reports CVEs in pinned dependencies but does NOT fail on them:
# known CVEs from legacy pinned versions are present today and need per-CVE triage before they
# can become a hard gate. Promote to a hard gate once the list is empty.
#
# `--with pip-audit` matters. The recipe used to call `$(UV) run pip-audit`, and pip-audit is
# declared nowhere — so it never started, the trailing `|| true` swallowed the spawn error, and
# both this target and the CI job that calls it were green while auditing nothing. Which exit
# code is tolerated matters just as much: 1 means "CVEs found" and is informational on purpose,
# anything above that means the tool itself did not run and must be loud.
#
# The requirements file goes to a `mktemp` path, not a fixed /tmp name. The fixed name carried
# the template's own name into every project built from it, and two checkouts auditing at the
# same time wrote over each other's export.
audit-deps:
	@set +e; \
	requirements=$$(mktemp); \
	$(UV) export --no-emit-project --frozen > "$$requirements"; \
	$(UV) run --with pip-audit pip-audit --disable-pip --requirement "$$requirements"; \
	audit_status=$$?; \
	rm -f "$$requirements"; \
	if [ $$audit_status -gt 1 ]; then \
		echo "audit-deps: pip-audit exited $$audit_status — the audit did not run"; \
		exit $$audit_status; \
	fi

# bandit lives here rather than only in a CI job, so the security scan is runnable by the person
# who can act on it. `-ll` reports medium severity and above; the two `# nosec B608` markers in
# the reference repository are load-bearing — without them this exits 1.
security-scan: ## Validation | bandit security scan over project/
	$(UV) run --with bandit bandit -q -r project -ll

# Everything the pipeline runs, in one local command, cheapest lane first so it fails fast.
# It exists because the pipeline stopped being reachable and "green" has to keep meaning
# something. Each lane below corresponds to a job in .github/workflows/ci.yml; adding a job
# there without adding it here puts the two back out of step, which is how a gate starts lying.
#
# There is no minimum-Python lane. requires-python and .python-version now name the same version,
# so such a lane could only re-run the suite on the interpreter lane 1 already used — a gate that
# cannot fail. Reinstate it the day the two numbers diverge again; ADR-002 says how.
# STRICT_GENERATED=1 on both gate lanes: this command answers "would the pipeline pass?", and
# there a stale generated artifact is a failure, not something to fix on the fly. Plain
# `make quality-gates` refreshes instead — by the time you reach ci-local the artifacts are
# already fresh, so this lane only fires for someone who skipped the gate entirely.
ci-local: ## Validation | Everything CI runs, locally: gates, both Postgres modes, security, audit, e2e
	@echo "===> 1/6 quality-gates"
	@STRICT_GENERATED=1 $(MAKE) --no-print-directory quality-gates
	@echo "===> 2/6 quality-gates without PostgreSQL"
	@STRICT_GENERATED=1 POSTGRES_ENABLED=false $(MAKE) --no-print-directory quality-gates
	@echo "===> 3/6 security scan"
	@$(MAKE) --no-print-directory security-scan
	@echo "===> 4/6 dependency audit"
	@$(MAKE) --no-print-directory audit-deps
	@echo "===> 5/6 diff coverage"
	@$(MAKE) --no-print-directory diff-coverage
	@echo "===> 6/6 functional tests"
	@$(MAKE) --no-print-directory test-e2e
	@echo "===> ci-local: all lanes passed"

# MAYAK_DOCTOR_SUBPROCESS is cleared, not merely unset-by-default. The doctor sets it on the
# children it spawns so "the doctor runs the test suite, and the suite exercises the doctor"
# terminates — but a shell that exports it for any other reason would silently disable five layers
# and get a bare "ok" back in a second. This is the entry point a human uses; it starts clean.
doctor:
	@MAYAK_DOCTOR_SUBPROCESS= $(UV) run python scripts/doctor_ai_context.py

doctor-json:
	@MAYAK_DOCTOR_SUBPROCESS= $(UV) run python scripts/doctor_ai_context.py --json

test: ## Testing | Unit + integration (skip functional)
	$(UV) run python scripts/run_all_tests.py --skip-functional

test-all: ## Testing | All tests including functional
	$(UV) run python scripts/run_all_tests.py

test-e2e: ## Testing | Functional tests only (Docker)
	$(UV) run python scripts/run_all_tests.py --functional-only

# Local mirror of the CI diff-coverage job. Runs unit + infrastructure tests
# with coverage, then checks that newly-changed .py lines (vs origin/main)
# have ≥80% coverage. Use this before push to catch what CI would catch.
# Empty diff (no .py changes vs main) returns exit 0 immediately.
# What "new code" is measured against. origin/main is right once the project has a remote; a
# checkout created from the template with `git init` has none, and diff-cover then aborts the
# whole ci-local run with a traceback ending in "Does 'origin/main' exist?" — before the
# functional lane has run at all. So the default asks git whether origin/main exists and falls
# back to HEAD, which yields an empty diff and an immediate pass rather than a crash: a fresh
# project measures nothing until it has a remote, instead of measuring nothing and failing.
# Override for a branch cut from something else:
#   make ci-local DIFF_COMPARE_BRANCH=develop
DIFF_COMPARE_BRANCH ?= $(shell git rev-parse --verify --quiet origin/main >/dev/null 2>&1 && echo origin/main || echo HEAD)

# The warning below exists because this gate silently measured a fraction of a change. diff-cover
# reads `git diff`, which never sees an untracked file, and a new vertical is mostly untracked
# files. Measured: 17 lines across 4 files before committing, 176 across 9 after the same commit —
# the gate that exists to catch uncovered NEW code was blind to almost all of it, and said
# nothing. `git add` is enough; a commit is not required (verified: `git diff HEAD`
# lists a staged addition).
diff-coverage:
	@untracked=$$(git ls-files --others --exclude-standard -- 'project/*.py'); \
	if [ -n "$$untracked" ]; then \
		echo "diff-coverage: WARNING — diff-cover cannot see these new files, so they are NOT measured:"; \
		echo "$$untracked" | sed 's/^/    /'; \
		echo "    Run 'git add' on them and re-run; a commit is not needed."; \
	fi
	$(UV) run python -m pytest tests/application tests/infrastructure tests/integration \
		--cov=project --cov-report=xml --cov-report=term-missing -q
	$(UV) run --with diff-cover diff-cover coverage.xml \
		--compare-branch=$(DIFF_COMPARE_BRANCH) --fail-under=80

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
	if $(POSTGRES_ENABLED_CHECK); then files="$$files -f docker-compose.postgres.yml"; fi; \
	: "$${APP_PORT:=$(SMOKE_APP_PORT)}"; export APP_PORT; \
	: "$${POSTGRES_PORT:=$(SMOKE_POSTGRES_PORT)}"; export POSTGRES_PORT; \
	compose="docker compose -p $(SMOKE_PROJECT) $$files"; \
	trap 'ec=$$?; $$compose down -v || true; exit $$ec' EXIT; \
	$$compose up -d --build --wait; \
	$$compose exec -T app sh -c 'curl -fsS "http://localhost:$${SERVER_PORT:-8000}/health/"'; \
	$$compose exec -T app sh -c 'curl -fsS "http://localhost:$${SERVER_PORT:-8000}/health/ready"'

migrate: ## Run | Apply pending Alembic migrations
	@if $(POSTGRES_ENABLED_CHECK); then \
		if $(POSTGRES_REACHABLE_CHECK); then \
			$(UV) run alembic upgrade head; \
		else \
			echo "PostgreSQL is not reachable with the credentials in .env."; \
			echo "  start one:  docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d db"; \
			echo "  or declare this project needs none:  POSTGRES_ENABLED=false in .env"; \
			exit 1; \
		fi; \
	else \
		echo "POSTGRES_ENABLED=false — no relational store, nothing to migrate."; \
	fi

# The Docker image migrates in entrypoint.sh; local runs used to skip it entirely, so a fresh
# checkout served a green /health/ready against a database with no tables. Same order both ways.
run-local: migrate ## Run | Migrate, then start the app locally (needs a reachable PostgreSQL unless POSTGRES_ENABLED=false)
	$(UV) run python -m project.launcher.main

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
logs: ## Reading a running service | Render the container's semantic log as a trace tree
	@docker compose logs --no-color --no-log-prefix --tail $(or $(LINES),2000) app \
		| $(UV) run python -c "from project.core.logging.trace_formatter import _cli; _cli()" $(ARGS)

# Same source, unrendered, for grepping. Writes to logs/container-<timestamp>.ndjson.
logs-raw: ## Reading a running service | Dump the container's raw NDJSON under logs/
	@mkdir -p logs
	@out="logs/container-$$(date -u +%Y-%m-%dT%H-%M-%S).ndjson"; \
	docker compose logs --no-color --no-log-prefix --tail $(or $(LINES),2000) app > "$$out"; \
	echo "$$out"

autogenerate-migration: ## Scaffolding | Alembic migration: MSG="description"
	@test -n "$(MSG)" || (echo "MSG is required, e.g. make autogenerate-migration MSG=create_customer_table" && exit 1)
	$(UV) run alembic revision --autogenerate -m "$(MSG)"
