---
name: initialize-project
description: Bootstrap a new backend from the Mayak template, or set up local development in an existing backend.
triggers: [init, bootstrap, setup, initialize]
minimal_read_set:
  - AGENTS.md
  - docs/project_context.json
validation_command: make verify
---

# Initialize a service

For a repository created from the template, choose the name, business domain and database mode:

```sh
make bootstrap NAME="Room Book" DOMAIN="Manage room bookings" DB=postgres
```

Use `DB=none` for a stateless service. The command sets product identity and provenance, installs
locked dependencies, creates missing local configuration, removes the sample routes and checks
HTTP readiness. PostgreSQL uses this worktree's loopback port. You can rerun the same command
after correcting an environment problem. Keep a dedicated copy for template development.

For an existing service, run `make init-project`, then `make db-up-worktree` if it uses PostgreSQL.
Alembic and the launcher read this checkout's assigned port unless you explicitly select another database through the environment.
Use `make run-local` to serve it. Before handoff, run `make verify DIFF_COMPARE_BRANCH=<starting commit>` to measure committed changes and quote its `verify: scope:` line and final line.
