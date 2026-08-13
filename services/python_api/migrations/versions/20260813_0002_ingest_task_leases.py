"""为 Kafka 发布与消费增加任务租约。

Revision ID: 20260813_0002
Revises: 20260812_0001
Create Date: 2026-08-13 10:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260813_0002"
down_revision: str | Sequence[str] | None = "20260812_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingest_tasks",
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "ingest_tasks",
        sa.Column(
            "lease_expires_at",
            mysql.DATETIME(fsp=6),
            nullable=True,
        ),
    )
    op.add_column(
        "ingest_tasks",
        sa.Column("last_event_id", mysql.CHAR(length=36), nullable=True),
    )
    op.add_column(
        "ingest_tasks",
        sa.Column("last_publish_error_message", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_ingest_tasks_outbox",
        "ingest_tasks",
        ["published_at", "status", "available_at", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ingest_tasks_outbox", table_name="ingest_tasks")
    op.drop_column("ingest_tasks", "last_publish_error_message")
    op.drop_column("ingest_tasks", "last_event_id")
    op.drop_column("ingest_tasks", "lease_expires_at")
    op.drop_column("ingest_tasks", "lease_owner")
