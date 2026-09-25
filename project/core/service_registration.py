# FILE: project/core/service_registration.py
# SUMMARY: Vertical-specific service wiring helpers. CompositionRoot delegates per-vertical service construction here so that build_dependencies() stays focused on orchestration.

from typing import Any

from psycopg_pool import AsyncConnectionPool

from project.application.reference_task_service import ReferenceTaskService
from project.core.config import Settings
from project.infrastructure.agents.llm_service import LLMService
from project.infrastructure.persistence.reference_task_repository import (
    ReferenceTaskRepository,
)


# db_pool: None when the project runs with POSTGRES_ENABLED=false and needs no relational store.
def build_reference_services(
    settings: Settings,
    llm_service: LLMService,
    db_pool: AsyncConnectionPool | None,
) -> dict[str, Any]:
    # None when the project runs without the relational store: there is no pool to give it.
    reference_task_service: ReferenceTaskService | None = None
    if db_pool is not None:
        # The service receives the Protocol-conforming repository, and the
        # repository receives the pool the kernel already owns. A vertical that opens its own
        # pool here gets two connection budgets and one of them is never closed on shutdown.
        reference_task_service = ReferenceTaskService(
            repository=ReferenceTaskRepository(connection_pool=db_pool),
        )

    # The key stays in the dict even when the value is None. Two consumers depend
    # on that: validate_endpoint_wiring.py reads this dict literal to learn the service exists at
    # all, and router_registration.py reads the value to decide whether the routes are reachable. Dropping
    # the key when the database is off makes the alias chain unresolvable and
    # validate_endpoint_wiring.py reports endpoint.alias_chain_invalid.
    #
    # settings and llm_service stay in the signature although the reference vertical is
    # storage-only: they are what the next vertical reaches for first, and a builder that has to
    # grow its parameter list is a builder every caller has to be updated for.
    _ = settings, llm_service
    services: dict[str, Any] = {
        "reference_task_service": reference_task_service,
    }
    return services
