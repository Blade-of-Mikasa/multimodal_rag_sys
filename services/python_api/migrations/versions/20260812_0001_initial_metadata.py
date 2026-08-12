"""创建资产、入库任务、会话和权限基础表。

Revision ID: 20260812_0001
Revises:
Create Date: 2026-08-12 15:00:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260812_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "access_control_lists",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_control_lists")),
        sa.UniqueConstraint(
            "tenant_id",
            "name",
            name=op.f("uq_access_control_lists_tenant_id"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_table(
        "access_control_entries",
        sa.Column("acl_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("permission", sa.String(length=16), nullable=False),
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "permission IN ('read', 'write', 'admin')",
            name=op.f("ck_access_control_entries_permission"),
        ),
        sa.CheckConstraint(
            "subject_type IN ('user', 'group', 'service')",
            name=op.f("ck_access_control_entries_subject_type"),
        ),
        sa.ForeignKeyConstraint(
            ["acl_id"],
            ["access_control_lists.id"],
            name=op.f("fk_access_control_entries_acl_id_access_control_lists"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_control_entries")),
        sa.UniqueConstraint(
            "acl_id",
            "subject_type",
            "subject_id",
            "permission",
            name=op.f("uq_access_control_entries_acl_id"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_access_control_entries_subject",
        "access_control_entries",
        ["subject_type", "subject_id"],
        unique=False,
    )
    op.create_table(
        "assets",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("acl_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=127), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "latest_version_number",
            mysql.BIGINT(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("deleted_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint(
            "latest_version_number >= 0",
            name=op.f("ck_assets_latest_version_number"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'deleted')",
            name=op.f("ck_assets_status"),
        ),
        sa.ForeignKeyConstraint(
            ["acl_id"],
            ["access_control_lists.id"],
            name=op.f("fk_assets_acl_id_access_control_lists"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_assets_tenant_owner",
        "assets",
        ["tenant_id", "owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_assets_tenant_status",
        "assets",
        ["tenant_id", "status", "created_at"],
        unique=False,
    )
    op.create_table(
        "asset_versions",
        sa.Column("asset_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("version_number", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("content_sha256", mysql.CHAR(length=64), nullable=False),
        sa.Column("size_bytes", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("media_type", sa.String(length=127), nullable=False),
        sa.Column(
            "ingest_status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("attributes_json", mysql.JSON(), nullable=True),
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint(
            "ingest_status IN ('pending', 'processing', 'ready', 'failed')",
            name=op.f("ck_asset_versions_ingest_status"),
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name=op.f("ck_asset_versions_number"),
        ),
        sa.CheckConstraint(
            "size_bytes >= 0",
            name=op.f("ck_asset_versions_size_bytes"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name=op.f("fk_asset_versions_asset_id_assets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_versions")),
        sa.UniqueConstraint(
            "asset_id",
            "version_number",
            name=op.f("uq_asset_versions_asset_id"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_asset_versions_content_sha256",
        "asset_versions",
        ["content_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_asset_versions_ingest_status",
        "asset_versions",
        ["ingest_status", "created_at"],
        unique=False,
    )
    op.create_table(
        "ingest_tasks",
        sa.Column("asset_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("asset_version_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column("dedupe_key", sa.String(length=191), nullable=False),
        sa.Column("kafka_topic", sa.String(length=249), nullable=True),
        sa.Column("kafka_partition", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("kafka_offset", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("published_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "available_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 "
            "AND attempt_count <= max_attempts",
            name=op.f("ck_ingest_tasks_attempts"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry', 'succeeded', 'failed', "
            "'dead_letter')",
            name=op.f("ck_ingest_tasks_status"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name=op.f("fk_ingest_tasks_asset_id_assets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["asset_version_id"],
            ["asset_versions.id"],
            name=op.f("fk_ingest_tasks_asset_version_id_asset_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingest_tasks")),
        sa.UniqueConstraint(
            "dedupe_key",
            name=op.f("uq_ingest_tasks_dedupe_key"),
        ),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_ingest_tasks_dispatch",
        "ingest_tasks",
        ["status", "available_at"],
        unique=False,
    )
    op.create_index(
        "ix_ingest_tasks_version_type",
        "ingest_tasks",
        ["asset_version_id", "task_type"],
        unique=False,
    )
    op.create_table(
        "conversations",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("acl_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("deleted_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'deleted')",
            name=op.f("ck_conversations_status"),
        ),
        sa.ForeignKeyConstraint(
            ["acl_id"],
            ["access_control_lists.id"],
            name=op.f("fk_conversations_acl_id_access_control_lists"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_conversations_tenant_owner",
        "conversations",
        ["tenant_id", "owner_user_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_conversations_tenant_status",
        "conversations",
        ["tenant_id", "status", "updated_at"],
        unique=False,
    )
    op.create_table(
        "conversation_messages",
        sa.Column("conversation_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", mysql.LONGTEXT(), nullable=False),
        sa.Column("citations_json", mysql.JSON(), nullable=True),
        sa.Column("token_count", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "`role` IN ('system', 'user', 'assistant', 'tool')",
            name=op.f("ck_conversation_messages_role"),
        ),
        sa.CheckConstraint(
            "token_count IS NULL OR token_count >= 0",
            name=op.f("ck_conversation_messages_token_count"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_conversation_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_messages")),
        **TABLE_OPTIONS,
    )
    op.create_index(
        "ix_conversation_messages_conversation_created",
        "conversation_messages",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_conversation_messages_request_id",
        "conversation_messages",
        ["request_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_messages_request_id",
        table_name="conversation_messages",
    )
    op.drop_index(
        "ix_conversation_messages_conversation_created",
        table_name="conversation_messages",
    )
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversations_tenant_status", table_name="conversations")
    op.drop_index("ix_conversations_tenant_owner", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_ingest_tasks_version_type", table_name="ingest_tasks")
    op.drop_index("ix_ingest_tasks_dispatch", table_name="ingest_tasks")
    op.drop_table("ingest_tasks")
    op.drop_index(
        "ix_asset_versions_ingest_status",
        table_name="asset_versions",
    )
    op.drop_index(
        "ix_asset_versions_content_sha256",
        table_name="asset_versions",
    )
    op.drop_table("asset_versions")
    op.drop_index("ix_assets_tenant_status", table_name="assets")
    op.drop_index("ix_assets_tenant_owner", table_name="assets")
    op.drop_table("assets")
    op.drop_index(
        "ix_access_control_entries_subject",
        table_name="access_control_entries",
    )
    op.drop_table("access_control_entries")
    op.drop_table("access_control_lists")
