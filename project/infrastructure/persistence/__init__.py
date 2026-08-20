# FILE: project/infrastructure/persistence/__init__.py
# SUMMARY: Database persistence layer: SQLAlchemy metadata for migrations, psycopg repositories for runtime.
# SQLAlchemy appears here only as the source of Alembic's metadata (orm_models.py). Nothing at
# runtime opens a SQLAlchemy session or engine — validate_runtime_ownership.py forbids it. Reads and
# writes go through psycopg against the shared pool; reference_task_repository.py is the worked
# example of that shape, including the row -> domain conversion the driver makes necessary.
