# FILE: project/domain/ports.py
# SUMMARY: Canonical example of a domain port (dependency inversion boundary). Verticals add their own ports alongside this one.

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol

from project.domain.reference_task import ReferenceTask


# CLASS: project.domain.ports.LLMPort
# SUMMARY: Minimal LLM-call boundary that keeps domain logic framework-free.
# Verticals depend on this Protocol; the concrete adapter is
# project.infrastructure.agents.prompt_llm_adapter.PromptLLMAdapter, which wraps LLMService. The
# kernel does not instantiate it: the shipped reference vertical is deliberately storage-only, so
# nothing in the template consumes an LLM yet. A vertical that needs one wraps the shared
# LLMService in build_reference_services — `MyService(llm_port=PromptLLMAdapter(llm_service))` —
# in the same place a service receives its repository. Named generically on purpose: naming the
# shipped vertical here would leave a dangling reference the moment someone deletes that
# vertical, in a file the removal checklist doesn't tell you to touch for that reason. The
# signature uses primitive str types deliberately —
# a domain module may import the standard library and project.domain and nothing else (enforced as
# an allowlist by scripts/validate_architecture.py:_DOMAIN_ALLOWED_PREFIXES), and LLMService speaks
# in langchain
# BaseMessage objects, so something has to translate. That adapter is the something; without it this
# Protocol described a boundary nothing crossed. Verticals that need richer message structures
# define their own typed ports next to this one, e.g. project/domain/<vertical>/ports.py.
class LLMPort(Protocol):
    # FUNCTION: call
    # SUMMARY: Send a text prompt, optionally with a system instruction, and return the response.
    # INPUT: system (Optional[str]): Instruction for the system role, or None to send none.
    # NOTE: `system` is a separate parameter rather than something the caller glues onto `prompt`
    # because the two are separate on the wire and separate in the trace. The full-trace extractor
    # splits its `system_prompt` / `user_message` fields by message role, so a system instruction
    # folded into the prompt string is recorded as user text — measured: every vertical on the
    # shipped adapter reported an empty `system_prompt` and the whole instruction as
    # `user_message`.
    #
    # MIGRATION, and it is not free. Callers are safe: passing one positional prompt still
    # type-checks and still runs. IMPLEMENTERS are not. A Protocol requires a conforming method to
    # accept every parameter the Protocol promises, so any class still declaring
    # `async def call(self, prompt: str) -> str` stops satisfying `LLMPort` the moment such a
    # parameter is added — two errors under this repository's mypy configuration, "Incompatible
    # types in assignment" and an argument-type error, both naming what is missing. Runtime duck
    # typing is unaffected; it is the type gate that goes red. The repair is mechanical — add
    # `*, system: Optional[str] = None` to that method, ignoring it if it has no use for one.
    async def call(self, prompt: str, *, system: Optional[str] = None) -> str: ...


# CLASS: project.domain.ports.ReferenceTaskRepositoryPort
# SUMMARY: Canonical data-access boundary. Verticals define one of these per aggregate.
# The concrete adapter is
# project.infrastructure.persistence.reference_task_repository.ReferenceTaskRepository, which owns
# every driver detail: SQL text, connection checkout, and the row -> domain conversion. The
# signatures speak only in domain types, so a service depending on this Protocol cannot
# accidentally receive a psycopg uuid.UUID, a Decimal, or a driver-specific date type. That
# conversion is the whole point of the boundary and the reason the reference implementation is
# covered by a functional test against real PostgreSQL rather than a mock: a mock returns whatever
# the test author imagined the driver returns, which is exactly how type drift reaches production.
class ReferenceTaskRepositoryPort(Protocol):
    # FUNCTION: add
    # SUMMARY: Persist a new task.
    async def add(self, task: ReferenceTask) -> None: ...

    # FUNCTION: get
    # SUMMARY: Load a single task by identifier, or None when it does not exist.
    async def get(self, task_id: str) -> ReferenceTask | None: ...

    # FUNCTION: list_by_status
    # SUMMARY: List tasks in a given workflow status, newest first.
    async def list_by_status(self, status: str, limit: int = 50) -> list[ReferenceTask]: ...

    # FUNCTION: update
    # SUMMARY: Store a changed task, but only while the stored row is the one it was read from.
    # INPUT: task (ReferenceTask): The new state to store, carrying its own fresh `updated_at`.
    # INPUT: expected_updated_at (datetime): The `updated_at` the caller read before changing it.
    # OUTPUT: (ReferenceTask | None): The stored task, or None when no row matched — meaning the
    #         row was written by somebody else in between, or it no longer exists.
    # NOTE: `expected_updated_at` is a separate parameter rather than something the adapter digs
    # out of `task`, because by then `task` carries the NEW timestamp: the value the WHERE clause
    # needs is the one that was read, and only the caller still has it. Returning None instead of
    # raising keeps the port free of the application's vocabulary — the service decides that a miss
    # is a ConflictError, the same way it decides a missing row is a NotFoundError.
    async def update(
        self,
        task: ReferenceTask,
        expected_updated_at: datetime,
    ) -> ReferenceTask | None: ...
