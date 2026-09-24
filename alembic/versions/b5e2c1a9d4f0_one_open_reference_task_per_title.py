"""one open reference task per title

Revision ID: b5e2c1a9d4f0
Revises: 7300d4656a8d
Create Date: 2026-09-24 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b5e2c1a9d4f0"
down_revision: Union[str, None] = "7300d4656a8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The rule "no two open tasks share a title" spans rows, so the single-row updated_at token cannot
# hold it, and neither can a check the service runs before writing: two requests both read "free"
# and both write. PostgreSQL holds it here, for every writer at once, in the statement that writes.
# On a table that already has duplicates this migration fails; find them first with
# SELECT lower(title), count(*) FROM reference_tasks WHERE status <> 'done' GROUP BY 1 HAVING count(*) > 1.
def upgrade() -> None:
    op.create_index(
        "uq_reference_tasks_open_title",
        "reference_tasks",
        [sa.text("lower(title)")],
        unique=True,
        postgresql_where=sa.text("status <> 'done'"),
    )


def downgrade() -> None:
    op.drop_index("uq_reference_tasks_open_title", table_name="reference_tasks")
