# Agent onboarding and verification

Status: implementation specification for Mayak 1.1.0 follow-up.

A person should be able to give a fresh Codex or Claude Code session the repository and a business task. The agent should reach a working runtime and produce a diff with trustworthy verification using the instructions in the checkout.

## 1. Dependency audit

Update the locked certifi version without a general dependency refresh. A fresh lock and dependency audit must pass. Document what existing descendants should update.

## 2. Bootstrap and local runtime

- `make bootstrap NAME="Room Book" DOMAIN="Room bookings" DB=postgres` initializes a project made from the template. `DB=none` selects a stateless project. Bootstrap derives a valid package name, sets product version 0.1.0, records template provenance, writes product metadata and samples, refreshes the lock and removes the reference vertical before application edits.
- Keep `make init-project` for installing dependencies and creating a missing local env. It preserves existing credentials and performs no Git configuration writes. Bootstrap reports conflicting existing settings before modifying the project. It is safe to rerun with the same inputs. Existing products keep their identity. A checkout whose origin points to the canonical template cannot be converted with bootstrap.
- PostgreSQL starts in a namespaced Compose project with a loopback port allocated for this worktree. The local tools pick up that port automatically. `DB=none` runs the bootstrap and readiness flow without Docker. The bootstrap smoke starts a temporary loopback application, checks readiness and shuts it down.
- Default Docker host bindings are loopback. Functional verification has explicit PostgreSQL and stateless paths. Changes to local Git configuration, remote writes and deploys require an explicit action.
- A new project records the template version and Git source-tree hash. GitHub template repositories can have unrelated commit histories. Record the tree as the source identifier rather than inventing an upstream commit. Existing descendants can run a local update check with actionable results.

## 3. Verification and diagnostics

- Keep `make gate-fast` as the edit loop. `make verify` runs the strict full gate, measures coverage across the complete application diff, and runs Docker E2E for changes to endpoints, wiring, migrations or container configuration. Report each check, including a skipped or blocked check.
- Diff coverage includes staged, unstaged and untracked application Python. It preserves the real Git index. CI keeps its independent quality and coverage jobs.
- A failed full gate diagnoses the failed run from its captured output, with stable rule IDs where available. A missing Docker daemon or Compose plugin reports the dependency and recovery. `make doctor` remains available as a standalone diagnostic. A logs request with no matching app reports where it looked and fails.
- The operational guide starts with new service, new feature or fix. Provide short runnable stateless HTTP and mock tool-loop examples. Retain the PostgreSQL reference vertical where it demonstrates persistence and concurrency.

## Acceptance

1. A fresh Git-template copy boots with both profiles. The product title and version appear in OpenAPI and health, the demo routes are gone and a second bootstrap preserves local credentials. A canonical upstream clone stays unchanged on refusal.
2. Two isolated checkouts migrate and serve concurrently. A disabled database makes zero Docker calls. No app or database port is published on a non-loopback interface by default.
3. An untracked, untested endpoint fails diff verification. A test covering it turns verification green. The Git index is byte-for-byte unchanged by coverage measurement.
4. Deliberately missing Docker gives a diagnosis with a recovery command, without running the full test suite twice. Template and generated products pass strict gates and profile-appropriate E2E on a Docker-capable host.
5. Record Linux and macOS checks, and distinguish local E2E evidence from any CI or provider test. For later replicated evaluation, compare fresh Codex and Claude sessions across stateless work, a PostgreSQL feature, a maintenance task and an inherited kernel update. Measure first-pass hidden acceptance, intervention and time. Keep the prompts and rubric versioned.

## Repeatable first-pass benchmark

Use the same task text for Mayak 1.1.0, the candidate revision and a minimal FastAPI baseline.
Start each run from the assigned snapshot with an empty agent history. Record the model, client,
snapshot, tool permissions, whether the dependency and image caches were warm, and any human
intervention. Keep failed and interrupted runs in the denominator.

| Task given to the agent | Acceptance exercised by the evaluator |
|---|---|
| Build a stateless service that returns a lowercased room title and rejects an empty title. | Clean start without Docker, correct 200 and 422 bodies, OpenAPI title. Remove validation and confirm a test fails. |
| Build a PostgreSQL service that creates and reads rooms with case-insensitive unique names. | Migrate from an empty database, verify 201, 200, 409 and 404. Concurrent duplicate inserts yield one success. Remove uniqueness and confirm a test fails. |
| In a service with bookings, add cancellation. Cancelling again should have no further effect. | Existing routes still work, a second cancellation has stable state, a missing booking is 404. Test against existing rows. |
| Update the inherited dependency lock for PYSEC-2026-4320. Preserve product identity and business behavior. | Fresh dependency audit, existing HTTP behavior, narrow lock diff and unchanged product identity. |

For each run record first HTTP readiness, minutes to independently accepted diff, intervention
count, and which injected regressions were caught. The primary score is first-pass acceptance
without technical intervention. Report results by task and agent, including the count of runs.
Start with five runs per combination as a pilot. Keep cache reads and reattempts in any cost report.
