"""timezone-aware timestamps

The models now write timezone-aware UTC times (datetime.now(timezone.utc),
replacing the deprecated datetime.utcnow), so the four timestamp columns
become TIMESTAMP WITH TIME ZONE. Existing values were written as naive
UTC, so they are interpreted as UTC when converted.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("tenants", "created_at"),
    ("schema_mapping_versions", "created_at"),
    ("policy_config", "updated_at"),
    ("investigation_history", "closed_at"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(),
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(),
            existing_nullable=False,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )
