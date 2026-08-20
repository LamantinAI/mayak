# FILE: project/infrastructure/persistence/orm_models.py
# SUMMARY: Defines SQLAlchemy ORM models for database interaction.

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from project.domain.reference_task import MAX_TITLE_LENGTH


# CLASS: project.infrastructure.persistence.orm_models.Base
# EXTENDS: sqlalchemy.orm.DeclarativeBase
# SUMMARY: Base class for all ORM models using SQLAlchemy's typed declarative base.
class Base(DeclarativeBase):
    pass


# CLASS: project.infrastructure.persistence.orm_models.ReferenceTaskORM
# EXTENDS: project.infrastructure.persistence.orm_models.Base
# SUMMARY: Table backing the shipped reference vertical. Alembic reads this metadata; nothing reads the ORM at runtime, because ReferenceTaskRepository speaks raw psycopg. Verticals add their own tables alongside it and delete this one with the rest of the example.
class ReferenceTaskORM(Base):
    __tablename__ = "reference_tasks"
    __table_args__ = (
        Index(
            "ix_reference_tasks_status_created_at",
            "status",
            "created_at",
        ),
    )

    # ATTRIBUTE: id (Mapped[str])
    # SUMMARY: Stable task identifier.
    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True)

    # ATTRIBUTE: title (Mapped[str])
    # SUMMARY: Human-readable task title.
    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)

    # ATTRIBUTE: details (Mapped[str | None])
    # SUMMARY: Optional task description.
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ATTRIBUTE: status (Mapped[str])
    # SUMMARY: Workflow status of the task.
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")

    # ATTRIBUTE: created_at (Mapped[datetime])
    # SUMMARY: Timestamp when the task was created.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
