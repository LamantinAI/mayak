# ADR-008: Readiness Criticality Is a Deployment Fact, Not a Dependency List

## Status

Accepted (2026-08-24)

## Decision

A dependency check in `/health/ready` gets a vote in the overall verdict — joins
`critical_statuses` in `health.py`'s `_build_readiness_response` — only when both are true:
this deployment cannot serve its own endpoints without it, and it cannot fail over away from it
(another replica sharing the same dependency does not help). A check that fails either test still
runs, and its real status is still reported in the response body; it just cannot flip the HTTP
status code. The database was already conditional on exactly this reasoning under ADR-006,
though ADR-006 states it in terms of that one dependency (`POSTGRES_ENABLED`) rather than as the
general rule. This ADR names the rule and applies it a second time: the LLM check now joins
`critical_statuses` only when `AGENT_LLM_READINESS_CRITICAL` (`AgentSettings.llm_readiness_critical`
in `project/core/config_settings_agent.py`) is `true`. Default: `false`.

## Rationale

The shipped `reference_task` vertical is storage-only — it never calls the model. Before this
change, an unreachable LLM provider still made `/health/ready` answer 503 for that vertical,
because `checks["llm"]["status"]` went into `critical_statuses` unconditionally, with no escape
hatch analogous to `POSTGRES_ENABLED=false`. That is the general failure the rule above closes:
a dependency the deployment does not actually need was still able to fail it.

The second half of the rule — "and it cannot fail over away from it" — is what makes the LLM a
worse default-critical dependency than the database, not merely an equally bad one. A relational
store is typically one deployment's own; losing it can genuinely mean that replica, or that
region, cannot serve. An LLM provider is typically shared by every replica of a service, often by
every service in an organization. When it has a bad minute — a 429, an expired key, a timeout —
every replica's readiness probe fails at the same moment, for the same reason, and Kubernetes has
nowhere to route traffic to: it pulls all of them, and a partial degradation of a third party
becomes a total outage of a service the provider outage did not otherwise touch. Eviction is a
remedy only when some other replica is in better shape; a shared, external dependency guarantees
none is.

The check stays in the response body regardless of its criticality. Monitoring that reads
`checks.llm.status` directly — rather than only the top-level `status` / HTTP code — still sees
the provider degrade. What changes is only whether that degradation also pages on-call by taking
every replica out of rotation.

## Operationalization

- `project/core/config_settings_agent.py` — `AgentSettings.llm_readiness_critical`
  (`AGENT_LLM_READINESS_CRITICAL`, default `false`)
- `project/infrastructure/api/endpoints/health.py` — `_build_readiness_response` reads the
  setting via the same defensive `getattr(request.app.state.settings, ...)` chain the database
  branch already used, and stamps a `"critical": bool` key onto each of `checks["services"]`,
  `checks["llm"]` and `checks["database"]` so a reader of the response body — human or an
  alerting rule — can tell a reported-but-non-voting status apart from one that just decided the
  HTTP code, without cross-referencing this document or the settings schema.
- `docs/adr/ADR-006-optional-postgres.md` — the first instance of this rule, stated for Postgres
  specifically; not restated here.
- `tests/application/test_health_endpoints.py` — default (non-critical) and
  `AGENT_LLM_READINESS_CRITICAL=true` behavior, both with the LLM healthy and unhealthy.
- `tests/application/test_optional_postgres.py` — regression coverage that the database's own
  conditional criticality is unchanged by this refactor of `critical_statuses`.

## Consequences

- Default `AGENT_LLM_READINESS_CRITICAL=false` means a fresh checkout of this template — whose
  only vertical never touches the model — cannot be evicted by a provider outage it does not
  depend on. A project that adds an LLM-backed vertical does not inherit this for free; see
  below.
- Set `AGENT_LLM_READINESS_CRITICAL=true` when this deployment's own endpoints genuinely cannot
  answer without the model — an agentic vertical with no non-LLM fallback path, for instance.
  That is a real coupling to the provider's availability and an explicit choice to accept it,
  the same way `POSTGRES_ENABLED=true` is an explicit choice to depend on a reachable database.
- `AGENT_LLM_MODE=mock` (ADR-003, the default everywhere but a live deployment) makes the
  question moot regardless of `AGENT_LLM_READINESS_CRITICAL`: `LLMServiceReadinessMixin.
  check_readiness` short-circuits to `"status": "healthy"` before it looks at readiness mode or
  criticality at all, so mock mode can never contribute an unhealthy LLM status for this setting
  to act on.
- `llm_readiness_check_mode="probe"` (also opt-in) still costs a provider round-trip per poll,
  bounded by the 30s cache in `llm_service_readiness.py`, independent of this setting — that cost
  is about call volume, not about whether a slow answer can evict a replica. The two settings are
  orthogonal and are meant to be read together: see the NOTE above
  `AgentSettings.llm_readiness_check_mode` for the operational advice that applies when both are
  turned on at once.
- The next dependency this kernel grows that can fail independently of the deployment's own
  health inherits this same two-part test, not a bespoke one: does this deployment need it, and
  can it fail over away from it. Both "no" answers keep a check informative without letting it
  vote.
